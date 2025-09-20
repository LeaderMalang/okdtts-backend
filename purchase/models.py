from django.db import models
from inventory.models import Product, Party
from setting.models import Warehouse
from voucher.models import Voucher, ChartOfAccount, VoucherType
from utils.stock import stock_in, stock_return, stock_out,stock_out_exact_batch
from utils.voucher import create_voucher_for_transaction
from finance.models import PaymentTerm, PaymentSchedule
from datetime import timedelta
from setting.constants import TAX_RECEIVABLE_ACCOUNT_CODE
from decimal import Decimal
from django.db import transaction
# from utils.voucher import post_composite_purchase_voucher,post_composite_purchase_return_voucher
from finance.hordak_posting import post_purchase,post_purchase_return,post_supplier_payment_reverse,reverse_txn_purchase
from hordak.models import Transaction 
from django.core.exceptions import ValidationError
from finance.hordak_posting import post_supplier_payment
from django.db.models import Sum
from .helpers import grn_returnable_map



class GoodsReceipt(models.Model):
    STATUS = (("DRAFT","Draft"), ("POSTED","Posted"), ("CANCELLED","Cancelled"))
    grn_no     = models.CharField(max_length=50, unique=True, blank=True)
    date       = models.DateField()
    invoice    = models.ForeignKey("purchase.PurchaseInvoice", on_delete=models.CASCADE, related_name="grns")
    warehouse  = models.ForeignKey("setting.Warehouse", on_delete=models.CASCADE)
    status     = models.CharField(max_length=12, choices=STATUS, default="DRAFT")
    note       = models.TextField(blank=True)
    posted_txn = models.ForeignKey(Transaction, null=True, blank=True, on_delete=models.SET_NULL)

    created_at = models.DateTimeField(auto_now_add=True)

    def _next_sequence(self, prefix="GRN-"):
        last = GoodsReceipt.objects.filter(grn_no__startswith=prefix).order_by("-id").values_list("grn_no", flat=True).first()
        if not last:
            return f"{prefix}1"
        try:
            n = int(last.split("-")[-1])
        except Exception:
            n = 0
        return f"{prefix}{n+1}"

    def clean(self):
        if self.invoice.warehouse_id != self.warehouse_id:
            raise ValidationError("GRN warehouse must match invoice warehouse.")

    @transaction.atomic
    def post(self):
        if self.status != "DRAFT":
            raise ValidationError("Only DRAFT GRN can be posted.")
        if not self.items.exists():
            raise ValidationError("No GRN items to post.")

        # Validate against outstanding per invoice item
        outstanding = self.invoice.outstanding_receive_map()  # defined below
        for it in self.items.all():
            allow = outstanding.get(it.invoice_item_id, 0)
            if it.quantity <= 0:
                raise ValidationError(f"Quantity must be > 0 for {it.invoice_item}.")
            if Decimal(it.quantity) > Decimal(allow):
                raise ValidationError(f"Qty {it.quantity} exceeds outstanding {allow} for {it.invoice_item}.")

        # Stock-in each GRN item
        for it in self.items.select_related("invoice_item__product"):
            stock_in(
                product=it.invoice_item.product,
                quantity=it.quantity,
                batch_number=it.batch_number or it.invoice_item.batch_number,
                expiry_date=it.expiry_date or it.invoice_item.expiry_date,
                purchase_price=it.purchase_price or it.invoice_item.purchase_price,
                sale_price=it.sale_price or it.invoice_item.sale_price,
                reason=f"GRN {self.grn_no or ''} for {self.invoice.invoice_no}",
                warehouse=self.warehouse,
            )

        # Mark posted
        self.status = "POSTED"
        if not self.grn_no:
            self.grn_no = self._next_sequence()
        self.save(update_fields=["status", "grn_no"])

        # Flip invoice status based on remaining outstanding
        remaining_any = any(qty > 0 for qty in self.invoice.outstanding_receive_map().values())
        self.invoice.status = "PARTIAL" if remaining_any else "RECEIVED"
        self.invoice.save(update_fields=["status"])
    @transaction.atomic
    def unpost_cancel(self, *, reason="Invoice cancelled"):
        """
        Reverse the stock-in done by this GRN and mark GRN as CANCELLED.
        Safe to call multiple times (idempotent): only acts on POSTED.
        """
        if self.status != "POSTED":
            # DRAFT → just flip to CANCELLED
            if self.status == "DRAFT":
                self.status = "CANCELLED"
                self.save(update_fields=["status"])
            return

        # 1) Reverse stock moves: stock_out the exact quantities that were stocked_in by this GRN
        for it in self.items.select_related("invoice_item__product"):
            stock_out_exact_batch(
                product=it.invoice_item.product,
                quantity=it.quantity,
                batch_number= it.invoice_item.batch_number,
                reason=f"GRN {self.grn_no} reversed: {reason}",
                warehouse=self.warehouse,
            )

        # # 2) If you had posted_txn at GRN-level (often not needed), reverse it
        # if self.posted_txn_id:
        #     reverse_txn(self.posted_txn, memo=f"Reverse GRN {self.grn_no}: {reason}")
        #     self.posted_txn = None  # optional

        # 3) Mark cancelled
        self.status = "CANCELLED"
        self.save(update_fields=["status", "posted_txn"])

class GoodsReceiptItem(models.Model):
    grn          = models.ForeignKey(GoodsReceipt, related_name="items", on_delete=models.CASCADE)
    invoice_item = models.ForeignKey("purchase.PurchaseInvoiceItem", on_delete=models.PROTECT, related_name="grn_items")
    quantity     = models.PositiveIntegerField()
    # Optional overrides if supplier ships different batches/prices than on PI
    batch_number = models.CharField(max_length=50, blank=True)
    expiry_date  = models.DateField(null=True, blank=True)
    purchase_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    sale_price     = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    def __str__(self):
        return f"{self.invoice_item.product} x {self.quantity} ({self.grn_id})"

class PurchaseInvoice(models.Model):
    STATUS = (
        ("DRAFT", "Draft (PO)"),
        ("CONFIRMED", "Confirmed (Booked)"),
        ("PARTIAL", "Partially Received"),
        ("RECEIVED", "Received"),
        ("CANCELLED", "Cancelled"),
    )
    PAYMENT_STATUS = (
        ("UNPAID", "Unpaid"),
        ("PARTIAL", "PartiallyPaid"),
        ("PAID", "Paid"),
    )

    invoice_no = models.CharField(max_length=50, unique=True, blank=True)
    date = models.DateField()
    supplier = models.ForeignKey('inventory.Party', on_delete=models.CASCADE, limit_choices_to={'party_type':'supplier'})
    warehouse = models.ForeignKey('setting.Warehouse', on_delete=models.CASCADE)

    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount     = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax          = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    grand_total  = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    paid_amount  = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    status           = models.CharField(max_length=20, choices=STATUS, default="DRAFT")
    payment_status   = models.CharField(max_length=20, choices=PAYMENT_STATUS, default="UNPAID")

    hordak_txn = models.ForeignKey(Transaction, null=True, blank=True, on_delete=models.SET_NULL)
    
    credited_amount  = models.DecimalField(max_digits=12, decimal_places=2, default=0)  # NEW

    @property
    def outstanding(self):
        # Now considers both cash paid and credit-setoff
        total = Decimal(self.grand_total or 0)
        paid = Decimal(self.paid_amount or 0)
        #credited = Decimal(self.credited_amount or 0)
        out = total - paid #-credited
        return out if out > 0 else Decimal(0)
    
    def _calc_totals(self):
        self.total_amount = Decimal(self.total_amount or 0)
        self.discount     = Decimal(self.discount or 0)
        self.tax          = Decimal(self.tax or 0)
        self.grand_total  = (self.total_amount - self.discount + self.tax)

    def _gen_invoice_no(self):
        if not self.invoice_no:
            last = PurchaseInvoice.objects.order_by('-id').first()
            next_no = 1 if not last else last.id + 1
            self.invoice_no = f"PINV-{next_no}"

    @transaction.atomic
    def confirm(self):
        # Book the liability (no stock yet)
        self._calc_totals()
        self._gen_invoice_no()
        if self.status != "DRAFT":
            return
        txn = post_purchase(
            date=self.date,
            description=f"Purchase Invoice {self.invoice_no}",
            total=self.total_amount,
            discount=self.discount,
            tax=self.tax,
            supplier_account=self.supplier.chart_of_account,   # Hordak Account
            warehouse_purchase_account=self.warehouse.default_purchase_account,  # Hordak Account
            paid_amount=self.paid_amount,
            warehouse=self.warehouse,
        )
        self.hordak_txn = txn
        self.status = "CONFIRMED"
        self._recalc_payment_status()
        self.save(update_fields=["invoice_no", "grand_total", "status", "payment_status", "hordak_txn"])


    def _recalc_payment_status(self):
        if (Decimal(self.paid_amount or 0) ) >= Decimal(self.grand_total or 0):
            self.payment_status = "PAID"
        elif Decimal(self.credited_amount or 0) > 0:
            self.payment_status = "PARTIAL"
        else:
            self.payment_status = "UNPAID"

    @transaction.atomic
    def simple_pay(self, amount):
        """Ad-hoc CASH/BANK payment against this PI (kept as-is)."""
        amt = Decimal(amount or 0)
        if amt <= 0:
            return
        post_supplier_payment(
            date=self.date,
            description=f"Payment for {self.invoice_no}",
            supplier_account=self.supplier.chart_of_account,
            amount=amt,
            warehouse=self.warehouse,
        )
        self.paid_amount = Decimal(self.paid_amount or 0) + amt
        self._recalc_payment_status()
        self.save(update_fields=["paid_amount", "payment_status"])

    @transaction.atomic
    def apply_credit(self, amount, note: str = ""):
        """
        Apply a vendor CREDIT (no cash). This does NOT post new accounting entries,
        assuming vendor credit (e.g., from PurchaseReturn) is already posted (DR A/P).
        """
        amt = Decimal(amount or 0)
        if amt <= 0:
            return
        if amt > self.outstanding:
            raise ValidationError("Credit amount exceeds outstanding balance.")

        self.credited_amount = (Decimal(self.credited_amount or 0) + amt).quantize(Decimal("0.01"))
        self._recalc_payment_status()
        self.save(update_fields=["credited_amount", "payment_status"])

    @transaction.atomic
    def settle_with_breakdown(self, *, pay_amount: Decimal, credit_amount: Decimal, note: str = ""):
        """
        Atomic helper to apply both CASH and CREDIT in one go.
        """
        pay_amount = Decimal(pay_amount or 0)
        credit_amount = Decimal(credit_amount or 0)
        if pay_amount < 0 or credit_amount < 0:
            raise ValidationError("Amounts cannot be negative.")

        if pay_amount + credit_amount != self.outstanding:
            raise ValidationError("Payment + Credit must equal the outstanding amount.")

        # 1) Cash/bank posting
        if pay_amount > 0:
            self.simple_pay(pay_amount)  # posts and updates paid_amount + status

        # 2) Credit set-off (no new posting; just reduce outstanding)
        if credit_amount > 0:
            self.apply_credit(credit_amount, note=note)
    def returnable_map(self):
        """
        Returns {invoice_item_id: returnable_qty}.
        returnable = received_qty (or ordered if no GRN) - sum(returned so far).
        """
        # 1) Received qty per invoice item
        rec_map = {}
        try:
            from purchase.models import GoodsReceiptItem  # if you added GRNs
            rec = (
                GoodsReceiptItem.objects
                .filter(invoice_item__invoice=self, grn__status="POSTED")
                .values("invoice_item_id")
                .annotate(qty=Sum("quantity"))
            )
            rec_map = {r["invoice_item_id"]: int(r["qty"] or 0) for r in rec}
        except Exception:
            # Fallback: treat as fully received = ordered + bonus
            pass

        # 2) Already returned per (product, batch) mapped back to invoice item
        from purchase.models import PurchaseReturnItem  # adjust import if app label differs
        ret = (
            PurchaseReturnItem.objects
            .filter(return_invoice__invoice=self)
            .values("product_id", "batch_number")
            .annotate(qty=Sum("quantity"))
        )
        returned_by_key = {(r["product_id"], (r["batch_number"] or "")): int(r["qty"] or 0) for r in ret}

        # 3) Build map
        result = {}
        for it in self.items.all():  # PurchaseInvoiceItem
            ordered_plus_bonus = int((it.quantity or 0) + (getattr(it, "bonus", 0) or 0))
            received = rec_map.get(it.id, ordered_plus_bonus)  # <-- per-item fallback
            already_ret = returned_by_key.get(it.id, 0)
            result[it.id] = max(received - already_ret, 0)
        return result
    def outstanding_receive_map(self):
        """
        Returns {invoice_item_id: outstanding_qty}
        Outstanding = (ordered qty + bonus) - sum(received via GRNs)
        """
        out = {}
        items = self.items.all().values("id", "quantity", "bonus")
        received = (
            GoodsReceiptItem.objects
            .filter(invoice_item__invoice=self, grn__status="POSTED")
            .values("invoice_item_id")
            .annotate(qty=Sum("quantity"))
        )
        rec_map = {r["invoice_item_id"]: r["qty"] or 0 for r in received}
        for it in items:
            ordered = int(it["quantity"] or 0) + int(it.get("bonus") or 0)
            got = int(rec_map.get(it["id"], 0))
            out[it["id"]] = max(ordered - got, 0)
        return out
    

    @transaction.atomic
    def cancel(self, *, reason="User requested cancellation", strict=True):
        """
        Fully cancel a CONFIRMED/PARTIAL/RECEIVED Purchase Invoice.

        Steps:
          1) Reverse any POSTED GRNs (stock-out) and mark them CANCELLED.
          2) Reverse any supplier payments that contributed to paid_amount.
          3) Reverse the original purchase accounting (self.hordak_txn).
          4) Zero paid/credit amounts and set status=CANCELLED, payment_status=UNPAID.

        If `strict=True`, will error if there are dependent docs (e.g., Purchase Returns posted).
        """
        if self.status == "CANCELLED":
            return  # idempotent

        # Optional strict safety: block if returns exist
        if strict:
            from purchase.models import PurchaseReturnItem  # adjust if different
            any_returns = PurchaseReturnItem.objects.filter(return_invoice__invoice=self).exists()
            if any_returns:
                raise ValidationError("Cannot cancel: posted Purchase Return(s) exist against this invoice.")

        # 1) Reverse GRNs (if any)
        for grn in self.grns.select_for_update():
            grn.unpost_cancel(reason=f"PI {self.invoice_no} cancelled")

        # 2) Reverse payments (if any)
        paid = Decimal(self.paid_amount or 0)
        if paid > 0:
            # If you have a SupplierPayment model linked per PI, iterate & reverse each.
            # If not, we post a single net reversal (safe if you didn’t need per-payment audit).
            post_supplier_payment_reverse(
                date=self.date,
                description=f"Reverse payments for {self.invoice_no}",
                supplier_account=self.supplier.chart_of_account,
                amount=paid,
                warehouse=self.warehouse,
            )
            self.paid_amount = Decimal("0.00")

        # Optionally reset applied credits too (these were non-posting offsets)
        if Decimal(self.credited_amount or 0) > 0:
            self.credited_amount = Decimal("0.00")

        # 3) Reverse original purchase accounting
        if self.hordak_txn_id:
            reverse_txn_purchase(self.hordak_txn, memo=f"Cancel PI {self.invoice_no}")
            self.hordak_txn = None

        # 4) Statuses
        self.status = "CANCELLED"
        self.payment_status = "UNPAID"
        self.save(update_fields=["status", "payment_status", "paid_amount", "credited_amount", "hordak_txn"])
class PurchaseInvoiceItem(models.Model):
    invoice = models.ForeignKey(
        PurchaseInvoice, related_name="items", on_delete=models.CASCADE
    )
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    batch_number = models.CharField(max_length=50, unique=True)
    expiry_date = models.DateField()
    quantity = models.PositiveIntegerField()
    bonus = models.PositiveIntegerField(default=0)
    purchase_price = models.DecimalField(max_digits=10, decimal_places=2)
    sale_price = models.DecimalField(max_digits=10, decimal_places=2)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    # net_amount = models.DecimalField(max_digits=12, decimal_places=2)


def _cash_or_bank_for(warehouse):
    return warehouse.default_cash_account or warehouse.default_bank_account
PR_STATUS = (
    ("DRAFT", "Draft (PR)"),
    ("CONFIRMED", "Confirmed (Booked)"),
    ("RETURNED", "Returned (Stock Out)"),
    ("REFUNDED", "Refunded (Cash/Bank)"),
    ("CREDITED", "Credited to Payable"),
    ("CANCELLED", "Cancelled"),
)

PAYMENT_STATUS = (
    ("UNPAID", "Unpaid"),
    ("PARTIAL", "Partially Refunded/Credited"),
    ("PAID", "Refunded/Credited in full"),
)

Q2 = Decimal("0.01")
class PurchaseReturn(models.Model):
    return_no = models.CharField(max_length=50, unique=True, blank=True)
    date = models.DateField()

    invoice = models.ForeignKey(
        "purchase.PurchaseInvoice",
        on_delete=models.SET_NULL, null=True, blank=True, related_name="returns",
    )
    supplier = models.ForeignKey(
        Party, on_delete=models.CASCADE,
        limit_choices_to={"party_type": "supplier"},
    )
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE)

    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    tax = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    status = models.CharField(max_length=20, choices=PR_STATUS, default="DRAFT")
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS, default="UNPAID")
    refunded_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Optional references to the accounting transactions, handy for audits
    confirm_txn = models.ForeignKey(Transaction, null=True, blank=True, on_delete=models.SET_NULL,related_name='confirm_txn'  )
    refund_txn = models.ForeignKey(Transaction, null=True, blank=True, on_delete=models.SET_NULL,related_name='refund_txn'  )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # ------- utilities -------
    @staticmethod
    def _next_sequence(prefix="PRN-"):
        last = (
            PurchaseReturn.objects.filter(return_no__startswith=prefix)
            .order_by("-id")
            .values_list("return_no", flat=True)
            .first()
        )
        if not last:
            return f"{prefix}1"
        try:
            n = int(last.split("-")[-1])
        except Exception:
            n = 0
        return f"{prefix}{n + 1}"

    def _base_total(self) -> Decimal:
        # If you treat tax separately in postings, adjust the helper’s inputs accordingly.
        return Decimal(self.total_amount or 0)

    def _purchase_return_account(self):
        # Prefer an explicit purchase-return/contra account if you have it; otherwise fallback to purchase
        return getattr(self.warehouse, "default_purchase_return_account", None) or getattr(
            self.warehouse, "default_purchase_account", None
        )

    def _supplier_account(self):
        return getattr(self.supplier, "chart_of_account", None)

    def _validate_transition(self, old, new):
        allowed = {
            "DRAFT": {"CONFIRMED", "CANCELLED"},
            "CONFIRMED": {"RETURNED", "REFUNDED", "CREDITED", "CANCELLED"},
            "RETURNED": {"REFUNDED", "CREDITED"},
            "REFUNDED": set(),
            "CREDITED": set(),
            "CANCELLED": set(),
        }
        if new not in allowed.get(old, set()):
            raise ValidationError(f"Invalid transition {old} → {new}.")

    def _sync_payment_status(self):
        total = self._base_total()
        paid = Decimal(self.refunded_amount or 0)
        if paid <= 0:
            self.payment_status = "UNPAID"
        elif paid >= total:
            self.payment_status = "PAID"
        else:
            self.payment_status = "PARTIAL"

    # ------- stock -------
    @transaction.atomic
    def do_stock_out(self):
        # Throws if no items are attached
        lines = list(self.items.all())
        if not lines:
            raise ValidationError("No return items to stock-out.")
        for line in lines:
            stock_out_exact_batch(
            product=line.product,
            quantity=line.quantity,
            warehouse=self.warehouse,
            batch_number=line.batch_number,              # <— exact batch
            reason=f"Purchase Return {self.return_no}",
        )
           

    # ------- postings using your helper -------
    @transaction.atomic
    def post_confirm_entry(self):
        """
        DRAFT -> CONFIRMED:
          DR A/P (Supplier)     amount
          CR Purchase Return    amount
        """
        total = self._base_total()
        if total <= 0:
            return

        supplier_acct = self._supplier_account()
        if not supplier_acct:
            raise ValidationError("Supplier has no chart of account.")

        warehouse_purchase_acct = self._purchase_return_account()
        if not warehouse_purchase_acct:
            raise ValidationError("Warehouse purchase/purchase-return account is missing.")

        txn = post_purchase_return(
            date=self.date,
            description=f"Purchase Return Confirm {self.return_no}",
            amount=total,
            supplier_account=supplier_acct,
            warehouse_purchase_account=warehouse_purchase_acct,
            cash_refund=False,           # NO CASH on confirm
            warehouse=self.warehouse,    # not used when cash_refund=False, but fine to pass
        )
        # Keep a reference if your helper returns a txn with `pk`/`id`/`uuid`
        if getattr(txn, "pk", None):
            self.confirm_txn_id = txn
        # inside PurchaseReturn.post_confirm_entry (after posting the journal):
        try:
            
            if self.invoice_id:
                apply_amount = min(Decimal(self._base_total()), Decimal(self.invoice.outstanding))
                if apply_amount > 0 and hasattr(self.invoice, "apply_credit"):
                    self.invoice.apply_credit(apply_amount, note=f"Auto credit from PR {self.return_no}")
        except Exception:
            # Non-fatal: if invoice missing credited_amount support we just skip the auto-apply
            pass


    @transaction.atomic
    def post_cash_refund_amount(self, amount: Decimal, *, note: str = ""):
        """
        Post a PARTIAL cash/bank refund (<= outstanding).
        DR Cash/Bank        amount
        CR A/P (Supplier)   amount
        """
        amount = Decimal(amount or 0)
        if amount <= 0:
            return

        total = self._base_total()
        already = Decimal(self.refunded_amount or 0)
        outstanding = total - already
        if amount > outstanding:
            raise ValidationError("Refund amount exceeds outstanding balance.")

        supplier_acct = self._supplier_account()
        if not supplier_acct:
            raise ValidationError("Supplier has no chart of account.")

        if not _cash_or_bank_for(self.warehouse):
            raise ValidationError("No Cash/Bank account configured for this warehouse.")

        # Reuse your existing helper
        txn = post_purchase_return(
            date=self.date,
            description=f"Purchase Return Partial Refund {self.return_no} ({amount})",
            amount=amount,
            supplier_account=supplier_acct,
            warehouse_purchase_account=self._purchase_return_account(),  # not used in cash_refund path
            cash_refund=True,
            warehouse=self.warehouse,
        )
        if getattr(txn, "pk", None):
            self.refund_txn_id = txn  # NOTE: overwrites; if you need multiple refs, switch to M2M

        # Increase only by the cash portion
        self.refunded_amount = (already + amount).quantize(Decimal("0.01"))


    @transaction.atomic
    def post_cash_refund_outstanding(self):
        """
        Refund the remaining outstanding:
          DR Cash/Bank          outstanding
          CR A/P (Supplier)     outstanding
        """
        total = self._base_total()
        already = Decimal(self.refunded_amount or 0)
        outstanding = total - already
        if outstanding <= 0:
            return

        supplier_acct = self._supplier_account()
        if not supplier_acct:
            raise ValidationError("Supplier has no chart of account.")

        # Ensure we have a cash/bank for the warehouse; your helper will pick it via warehouse
        if not _cash_or_bank_for(self.warehouse):
            raise ValidationError("No Cash/Bank account configured for this warehouse.")

        txn = post_purchase_return(
            date=self.date,
            description=f"Purchase Return Refund {self.return_no}",
            amount=outstanding,
            supplier_account=supplier_acct,
            warehouse_purchase_account=self._purchase_return_account(),  # not used in cash_refund path
            cash_refund=True,            # CASH path
            warehouse=self.warehouse,    # used to select cash/bank in helper
        )
        if getattr(txn, "pk", None):
            self.refund_txn_id = txn

        # Update running total of refunded (cash) value
        self.refunded_amount = total
    def _recompute_totals_from_items(self):
        total = Decimal("0")
        for li in self.items.all():
            # normalize & auto-fill amount if not provided
            qty   = Decimal(li.quantity or 0)
            price = Decimal(li.purchase_price or 0)
            line_amount = (qty * price).quantize(Q2)
            if li.amount != line_amount:
                # Avoid recursive saves: just stage it; write once after parent save if you prefer
                li.amount = line_amount
                li.save(update_fields=["amount"])
            total += line_amount
        # Tax: keep as-is or compute proportionally; here we leave self.tax untouched
        if self.total_amount != total:
            self.total_amount = total.quantize(Q2)

    def _validate_against_invoice_returnables(self):
        """
        New implementation: validate against GRN-based remaining per GRN line.
        """
        if not self.invoice_id:
            return  # allow free returns not tied to PI (if you want)
        if self.supplier_id != self.invoice.supplier_id:
            raise ValidationError("Supplier must match the selected Purchase Invoice.")
        if self.warehouse_id != self.invoice.warehouse_id:
            raise ValidationError("Warehouse must match the selected Purchase Invoice.")

        # remaining per GRN item
        remaining = grn_returnable_map(self.invoice, exclude_pr_id=self.pk)

        # sum requested per GRN item in this PR
        requested = {}
        for li in self.items.all():
            if not li.grn_item_id:
                raise ValidationError("Every return line must reference a GRN line.")
            requested[li.grn_item_id] = requested.get(li.grn_item_id, 0) + int(li.quantity or 0)

        # compare
        for gid, qty in requested.items():
            allow = int(remaining.get(gid, 0))
            if qty > allow:
                raise ValidationError(f"Return qty {qty} exceeds returnable {allow} for GRN line #{gid}.")

    # ------- save wiring -------
    @transaction.atomic
    def save(self, *args, **kwargs):
        # keep your existing status transition code; DO NOT recompute totals here on create
        # (admin will do it after inlines are saved).
        if not self.return_no:
            self.return_no = self._next_sequence()

        old_status = None
        if self.pk:
            old_status = (
                PurchaseReturn.objects
                .filter(pk=self.pk)
                .values_list("status", flat=True)
                .first()
            )

        super().save(*args, **kwargs)

        if old_status is None:
            old_status = self.status

        if old_status != self.status:
            self._validate_transition(old_status, self.status)

            if old_status == "DRAFT" and self.status == "CONFIRMED":
                self.post_confirm_entry()

            if old_status in {"CONFIRMED"} and self.status == "RETURNED":
                self.do_stock_out()

            if self.status == "REFUNDED":
                self.post_cash_refund_outstanding()

            if self.status == "CREDITED":
                if self.supplier and self.supplier.current_balance is not None:
                    self.supplier.current_balance = (self.supplier.current_balance or 0) - self._base_total()
                    self.supplier.save(update_fields=["current_balance"])
                self.refunded_amount = self._base_total()

            self._sync_payment_status()
            super().save(update_fields=["status", "payment_status", "refunded_amount", "confirm_txn_id", "refund_txn_id"])




class PurchaseReturnItem(models.Model):
    return_invoice = models.ForeignKey(PurchaseReturn, related_name="items", on_delete=models.CASCADE)
    # NEW: link to the exact GRN line this return is reversing
    grn_item = models.ForeignKey(
        "purchase.GoodsReceiptItem",
        on_delete=models.PROTECT,
        related_name="return_items",
        null=False, blank=False,
    )
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    batch_number = models.CharField(max_length=50, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    quantity = models.PositiveIntegerField()
    purchase_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    sale_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    def clean(self):
        # Ensure GRN belongs to same invoice & warehouse
        inv = self.grn_item.invoice_item.invoice
        if self.return_invoice.invoice_id and self.return_invoice.invoice_id != inv.id:
            raise ValidationError("Return must reference a GRN line from the same Purchase Invoice.")
        if self.return_invoice.warehouse_id and self.return_invoice.warehouse_id != self.grn_item.grn.warehouse_id:
            raise ValidationError("Return warehouse must match the GRN warehouse.")

    def save(self, *args, **kwargs):
        # Auto-fill from GRN line + invoice item if not provided
        ii = self.grn_item.invoice_item
        if not self.product_id:
            self.product = ii.product
        if not self.batch_number:
            self.batch_number = (self.grn_item.batch_number or ii.batch_number or "")
        if not self.expiry_date:
            self.expiry_date = self.grn_item.expiry_date or ii.expiry_date
        if not self.purchase_price:
            self.purchase_price = self.grn_item.purchase_price or ii.purchase_price
        if not self.sale_price:
            self.sale_price = self.grn_item.sale_price or ii.sale_price

        self.amount = (Decimal(self.quantity or 0) * Decimal(self.purchase_price or 0)).quantize(Q2)
        super().save(*args, **kwargs)
    def __str__(self):
        return f"{self.product} x {self.quantity} (PR {self.return_invoice.return_no})"



class InvestorTransaction(models.Model):
    """Records cash movement related to an investor."""

    TRANSACTION_TYPES = (
        ("investment", "Investment"),
        ("payout", "Payout"),
        ("profit", "Profit"),
    )

    investor = models.ForeignKey(
        Party,
        on_delete=models.CASCADE,
        limit_choices_to={"party_type": "investor"},
    )
    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    date = models.DateField()
    notes = models.TextField(blank=True)
    purchase_invoice = models.ForeignKey(
        PurchaseInvoice, on_delete=models.SET_NULL, null=True, blank=True, related_name="investor_transactions"
    )

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"{self.investor} - {self.transaction_type} - {self.amount}"

