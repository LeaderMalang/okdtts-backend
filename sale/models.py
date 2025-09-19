from django.db import models

from setting.models import Warehouse

from inventory.models import Party, Product, Batch


import logging

from voucher.models import Voucher, ChartOfAccount, VoucherType
from utils.voucher import create_voucher_for_transaction
from utils.stock import stock_return, stock_out
from finance.models import PaymentTerm, PaymentSchedule
from datetime import timedelta
from setting.constants import TAX_PAYABLE_ACCOUNT_CODE
from decimal import Decimal
from django.db import transaction
from utils.voucher import post_composite_sales_voucher,post_composite_sales_return_voucher

from finance.models_receipts import CustomerReceipt
from hordak.models import Transaction
from finance.hordak_posting import post_sale, post_customer_receipt,post_customer_refund,post_sale_return,post_cancel_sale,post_reverse_customer_receipt_partial
from django.core.exceptions import ValidationError
from django.db.models import Sum
from finance.models_receipts import CustomerReceiptAllocation
logger = logging.getLogger(__name__)
# Reuse your helper for selecting the warehouse cash/bank account
def _cash_or_bank_for(warehouse):
    return warehouse.default_cash_account or warehouse.default_bank_account

# Your Hordak posting helpers — align names/imports to your projectt # <- implement/align if needed
Q2 = Decimal("0.01")
class SaleInvoice(models.Model):
    STATUS = (
        ("DRAFT", "Draft (SO)"),
        ("CONFIRMED", "Confirmed (Booked)"),
        ("DELIVERED", "Delivered (Stock Out)"),
        ("CANCELLED", "Cancelled"),
    )
    PAYMENT_STATUS = (("UNPAID", "Unpaid"), ("PARTIAL", "Partially Paid"), ("PAID", "Paid"))

    # Number frozen after first save
    invoice_no = models.CharField(max_length=50, unique=True, blank=True)


    date = models.DateField()
    customer = models.ForeignKey(
        "inventory.Party",
        on_delete=models.CASCADE,
        limit_choices_to={"party_type": "customer"},
    )
    warehouse = models.ForeignKey("setting.Warehouse", on_delete=models.CASCADE)

    # amounts (no sub_total, no net_amount)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)  # sum of line.amount
    discount     = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax          = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    grand_total  = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    paid_amount  = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # optional terms
    payment_term = models.ForeignKey(PaymentTerm, on_delete=models.SET_NULL, null=True, blank=True)

    # states
    status         = models.CharField(max_length=20, choices=STATUS, default="DRAFT")
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS, default="UNPAID")

    # extras
    qr_code   = models.CharField(max_length=255, blank=True)
    hordak_txn = models.ForeignKey(Transaction, null=True, blank=True, on_delete=models.SET_NULL)

    # staff links you already use
    recoveries      = models.ManyToManyField("hr.Employee", through="RecoveryLog", related_name="recovery_invoices", blank=True)
    booking_man_id  = models.ForeignKey("hr.Employee", on_delete=models.SET_NULL, null=True, blank=True, related_name="bookings")
    delivery_man_id = models.ForeignKey("hr.Employee", on_delete=models.SET_NULL, null=True, blank=True, related_name="deliveries")

    # ---------- numbering ----------
    @staticmethod
    def _next_sequence(prefix="SINV-"):
        last = (
            SaleInvoice.objects.filter(invoice_no__startswith=prefix)
            .order_by("-id")
            .values_list("invoice_no", flat=True)
            .first()
        )
        if not last:
            return f"{prefix}1"
        try:
            n = int(str(last).split("-")[-1])
        except Exception:
            n = 0
        return f"{prefix}{n + 1}"

    def _ensure_number(self):
        if not self.invoice_no:
            self.invoice_no = self._next_sequence()

    # ---------- derived ----------
    @property
    def outstanding(self) -> Decimal:
        return max(Decimal(self.grand_total or 0) - Decimal(self.paid_amount or 0), Decimal("0"))

    def _recalc_totals_from_items(self):
        total = sum((li.amount or 0) for li in self.items.all()) or Decimal("0")
        self.total_amount = Decimal(total)
        self.grand_total  = self.total_amount - Decimal(self.discount or 0) + Decimal(self.tax or 0)

    def _recalc_payment_status(self):
        paid = Decimal(self.paid_amount or 0)
        if paid <= 0:
            self.payment_status = "UNPAID"
        elif paid >= Decimal(self.grand_total or 0):
            self.payment_status = "PAID"
        else:
            self.payment_status = "PARTIAL"

    # ---------- workflow ----------
    @transaction.atomic
    def confirm(self):
        """
        DRAFT -> CONFIRMED:
          uses post_sale(subtotal, tax) with subtotal=(total_amount - discount),
          NO cash at confirm; payments via receive_payment().
        """
        if self.status != "DRAFT":
            return
        self._ensure_number()
        self._recalc_totals_from_items()

        base_subtotal = Decimal(self.total_amount or 0) - Decimal(self.discount or 0)

        txn = post_sale(
            date=self.date,
            description=f"Sales Invoice {self.invoice_no}",
            subtotal=base_subtotal,
            tax=Decimal(self.tax or 0),
            customer_account=self.customer.chart_of_account,
            warehouse_sales_account=self.warehouse.default_sales_account,
            paid_amount=Decimal("0"),      # NO cash here
            warehouse=self.warehouse,
        )
        self.hordak_txn = txn
        self.status = "CONFIRMED"
        self._recalc_payment_status()
        if self.outstanding > 0:
            self.customer.current_balance = (self.customer.current_balance or 0) + self.outstanding
            self.customer.save(update_fields=["current_balance"])
        self.save(update_fields=[
            "invoice_no","total_amount","grand_total",
            "status","payment_status","hordak_txn"
        ])

    @transaction.atomic
    def receive_payment(self, amount: Decimal):
        """
        DR Cash/Bank   amount
        CR A/R         amount
        """
        amt = Decimal(amount or 0)
        if amt <= 0:
            return
        if self.status not in {"CONFIRMED", "DELIVERED"}:
            raise ValidationError("Receive payment only for CONFIRMED/DELIVERED invoices.")
        if not _cash_or_bank_for(self.warehouse):
            raise ValidationError("No Cash/Bank account configured for this warehouse.")

        rcpt = CustomerReceipt.objects.create(
        date=self.date,
        customer=self.customer,
        warehouse=self.warehouse,
        amount=amount,
        description=f"Receipt for {self.invoice_no}",
        )
        rcpt.post()                # GL: DR cash, CR A/R; Party.current_balance -= amount
        rcpt.allocate(self, amount)
        self.paid_amount = (Decimal(self.paid_amount or 0) + amt)
        self._recalc_payment_status()
        self.save(update_fields=["paid_amount","payment_status"])

    # ---------- stock out (partial-aware) ----------
    @transaction.atomic
    def deliver_partial(self, quantities: dict[int, int]):
        """
        Deliver per-line quantities (partial allowed).
        quantities = { SaleInvoiceItem.id : qty_to_deliver_now }
        """
        if self.status not in {"CONFIRMED", "DELIVERED"}:
            raise ValidationError("Deliver allowed only from CONFIRMED/DELIVERED.")

        items = {li.id: li for li in self.items.select_related("product")}
        any_delivered = False

        for item_id, qty in (quantities or {}).items():
            if item_id not in items:
                continue
            li = items[item_id]
            qty = int(qty or 0)
            if qty <= 0:
                continue

            remain = li.remaining_to_deliver
            if qty > remain:
                raise ValidationError(f"Line {li.id} exceeds remaining {remain}.")

            stock_out(
                product=li.product,
                quantity=qty,
                reason=f"Sales Delivery {self.invoice_no}",
            )
            li.delivered_qty = (li.delivered_qty or 0) + qty
            li.save(update_fields=["delivered_qty"])
            any_delivered = True

        if not any_delivered:
            return

        # If all lines fully delivered -> mark DELIVERED
        if all(li.remaining_to_deliver <= 0 for li in self.items.all()):
            self.status = "DELIVERED"
            self.save(update_fields=["status"])

    @transaction.atomic
    def deliver_all_remaining(self):
        """Deliver everything that remains."""
        qmap = {li.id: li.remaining_to_deliver for li in self.items.all() if li.remaining_to_deliver > 0}
        self.deliver_partial(qmap)
    @transaction.atomic
    def cancel(self, *, reason: str = ""):
        """
        Cancel even if receipts/allocations exist.
        Steps:
          1) Reverse any delivered stock (put it back).
          2) For each receipt allocation to this invoice, post a reversing receipt txn
             (DR A/R, CR Cash) for the allocated amount ONLY, update the receipt numbers,
             delete the allocation; if the receipt becomes empty, delete the receipt record.
          3) Reverse the sale confirm journal (DR Sales, DR Tax, CR A/R).
          4) Adjust Party.current_balance: +reversed_receipts - grand_total_at_confirm.
          5) Mark invoice CANCELLED, zero out paid fields.
        """
        if self.status == "CANCELLED":
            return

        # ---------- 1) reverse delivered stock ----------
        delivered_lines = list(self.items.all())
        for li in delivered_lines:
            qty_del = int(getattr(li, "delivered_qty", 0) or 0)
            if qty_del > 0:

                stock_return(
                    product=li.product,
                    batch_number=li.batch.batch_number if getattr(li, "batch", None) else "",
                    quantity=qty_del,
                    reason=f"Cancel Sales {self.invoice_no}",
                )
                li.delivered_qty = 0
                li.save(update_fields=["delivered_qty"])

        # ---------- 2) reverse receipts allocated to this invoice ----------
        # group allocations by receipt
        allocs = (
            CustomerReceiptAllocation.objects
            .select_related("receipt")
            .filter(invoice=self)
        )
        reversed_receipts_total = Decimal("0.00")
        cash_or_bank = getattr(self.warehouse, "default_cash_account", None) or getattr(self.warehouse, "default_bank_account", None)
        if not cash_or_bank:
            raise ValidationError("No Cash/Bank account configured for this warehouse (needed to reverse receipts).")

        by_receipt = {}
        for a in allocs:
            by_receipt.setdefault(a.receipt_id, {"receipt": a.receipt, "amount": Decimal("0.00"), "rows": []})
            by_receipt[a.receipt_id]["amount"] += Decimal(a.amount or 0)
            by_receipt[a.receipt_id]["rows"].append(a)

        for rid, pack in by_receipt.items():
            rcpt = pack["receipt"]
            amt_to_reverse = pack["amount"]
            if amt_to_reverse <= 0:
                continue

            # post reversing GL (DR A/R, CR Cash) for just the allocated amount
            post_reverse_customer_receipt_partial(
                date=self.date,
                description=f"Reverse Receipt {rcpt.number} (cancel {self.invoice_no})",
                customer_account=self.customer.chart_of_account,
                cash_or_bank_account=cash_or_bank,
                amount=amt_to_reverse,
            )
            reversed_receipts_total += amt_to_reverse

            # delete allocations rows for this invoice
            CustomerReceiptAllocation.objects.filter(id__in=[r.id for r in pack["rows"]]).delete()

            # shrink or delete the receipt record (we're NOT keeping any advance)
            rcpt.amount = Decimal(rcpt.amount or 0) - amt_to_reverse
            rcpt.unallocated_amount = max(Decimal(rcpt.unallocated_amount or 0) - amt_to_reverse, Decimal("0"))
            rcpt.save(update_fields=["amount", "unallocated_amount"])

            if rcpt.amount <= 0 and rcpt.allocations.count() == 0 and rcpt.unallocated_amount <= 0:
                # nuke the receipt record itself (accounting already reversed above)
                rcpt.delete()

        # ---------- 3) reverse sale confirm journal ----------
        # What we posted at confirm was subtotal=(total_amount - discount), tax=self.tax
        base_subtotal = Decimal(self.total_amount or 0) - Decimal(self.discount or 0)
        tax_amount = Decimal(self.tax or 0)
        post_cancel_sale(
            date=self.date,
            description=f"Reverse Sales {self.invoice_no} (cancel)",
            subtotal=base_subtotal,
            tax=tax_amount,
            customer_account=self.customer.chart_of_account,
            warehouse_sales_account=self.warehouse.default_sales_account,
        )

        # ---------- 4) fix customer's running balance ----------
        # At confirm we increased by grand_total.
        # Each receipt originally decreased by its posted amount; we just reversed those -> increase by same.
        # Net effect to return to pre-invoice state:  + reversed_receipts_total  - grand_total
        grand_total_now = Decimal(self.grand_total or 0)
        delta = reversed_receipts_total - grand_total_now
        if delta:
            self.customer.current_balance = (self.customer.current_balance or 0) + delta
            self.customer.save(update_fields=["current_balance"])

        # ---------- 5) finalize invoice fields ----------
        self.paid_amount = Decimal("0.00")
        self.payment_status = "UNPAID"
        self.status = "CANCELLED"
        self.save(update_fields=["paid_amount", "payment_status", "status"])
    # Keep same invoice number from first save
    def save(self, *args, **kwargs):
        if self.pk is None and not self.invoice_no:
            self._ensure_number()
        super().save(*args, **kwargs)


class SaleInvoiceItem(models.Model):
    invoice  = models.ForeignKey(SaleInvoice, related_name="items", on_delete=models.CASCADE)
    product  = models.ForeignKey("inventory.Product", on_delete=models.PROTECT)
    batch    = models.ForeignKey("inventory.Batch", null=True, blank=True, on_delete=models.SET_NULL)
    quantity = models.PositiveIntegerField()
    bonus    = models.PositiveIntegerField(default=0)
    packing  = models.PositiveIntegerField(default=0)
    rate     = models.DecimalField(max_digits=10, decimal_places=2)
    discount1= models.DecimalField(max_digits=5, decimal_places=2, default=0)
    discount2= models.DecimalField(max_digits=5, decimal_places=2, default=0)
    amount   = models.DecimalField(max_digits=12, decimal_places=2)
    bid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Track partial deliveries
    delivered_qty = models.PositiveIntegerField(default=0)
    @property
    def remaining_to_deliver(self) -> int:
        return max(int(self.quantity or 0) - int(self.delivered_qty or 0), 0)

    @property
    def total_ordered(self) -> int:
        return int((self.quantity or 0) + (self.bonus or 0))

    @property
    def remaining_to_deliver(self) -> int:
        return max(self.total_ordered - int(self.delivered_qty or 0), 0)



Q2 = Decimal("0.01")

SR_STATUS = (
    ("DRAFT",     "Draft (SR)"),
    ("CONFIRMED", "Confirmed (Credit Note)"),
    ("RETURNED",  "Returned (Stock In)"),
    ("REFUNDED",  "Refunded (Cash/Bank)"),
    ("CREDITED",  "Credited to Receivable"),
    ("CANCELLED", "Cancelled"),
)

PAYMENT_STATUS = (
    ("UNPAID",   "Unpaid"),
    ("PARTIAL",  "Partially Refunded/Credited"),
    ("PAID",     "Refunded/Credited in full"),
)

class SaleReturn(models.Model):
    return_no = models.CharField(max_length=50, unique=True, blank=True)
    date      = models.DateField()

    invoice   = models.ForeignKey(
        "sale.SaleInvoice",
        null=True, blank=True, on_delete=models.SET_NULL, related_name="returns"
    )
    customer  = models.ForeignKey(
        Party, on_delete=models.CASCADE,
        limit_choices_to={"party_type": "customer"},
    )
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE)

    total_amount    = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax             = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    status          = models.CharField(max_length=20, choices=SR_STATUS, default="DRAFT")
    payment_status  = models.CharField(max_length=20, choices=PAYMENT_STATUS, default="UNPAID")
    refunded_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Optional references for audit trail
    confirm_txn = models.ForeignKey(Transaction, null=True, blank=True, on_delete=models.SET_NULL, related_name="sr_confirm_txn")
    refund_txn  = models.ForeignKey(Transaction, null=True, blank=True, on_delete=models.SET_NULL, related_name="sr_refund_txn")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # ---------- utilities ----------
    @staticmethod
    def _next_sequence(prefix="SRN-"):
        last = (
            SaleReturn.objects.filter(return_no__startswith=prefix)
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
        return Decimal(self.total_amount or 0)

    def _sales_return_account(self):
        # prefer dedicated sales-return account; fallback to sales
        return getattr(self.warehouse, "default_sales_return_account", None) or getattr(
            self.warehouse, "default_sales_account", None
        )

    def _customer_account(self):
        return getattr(self.customer, "chart_of_account", None)

    def _validate_transition(self, old, new):
        allowed = {
            "DRAFT":     {"CONFIRMED", "CANCELLED"},
            "CONFIRMED": {"RETURNED", "REFUNDED", "CREDITED", "CANCELLED"},
            "RETURNED":  {"REFUNDED", "CREDITED"},
            "REFUNDED":  set(),
            "CREDITED":  set(),
            "CANCELLED": set(),
        }
        if new not in allowed.get(old, set()):
            raise ValidationError(f"Invalid transition {old} → {new}.")

    def _sync_payment_status(self):
        total = self._base_total()
        paid  = Decimal(self.refunded_amount or 0)
        if paid <= 0:
            self.payment_status = "UNPAID"
        elif paid >= total:
            self.payment_status = "PAID"
        else:
            self.payment_status = "PARTIAL"

    # ---------- stock ----------
    @transaction.atomic
    def do_stock_in(self):
        lines = list(self.items.all())
        if not lines:
            raise ValidationError("No return items to stock-in.")
        for line in lines:
            stock_return(
                product=line.product,
                quantity=line.quantity,
                batch_number=getattr(line, "batch_number", "") or "",
                reason=f"Sale Return {self.return_no}",
            )

    # ---------- postings ----------
    @transaction.atomic
    def post_confirm_entry(self):
        """
        DRAFT -> CONFIRMED:
          DR Sales Returns     amount
          CR A/R (Customer)    amount
        """
        total = self._base_total()
        if total <= 0:
            return
        cust_acct = self._customer_account()
        if not cust_acct:
            raise ValidationError("Customer has no chart of account.")
        sales_return_acct = self._sales_return_account()
        if not sales_return_acct:
            raise ValidationError("Warehouse sales/sales-return account is missing.")

        txn = post_sale_return(
            date=self.date,
            description=f"Sale Return Confirm {self.return_no}",
            amount=total,
            tax=Decimal(self.tax or 0),
            customer_account=cust_acct,
            warehouse_sales_return_account=sales_return_acct,
            refund_cash=False,           # NO cash on confirm
            warehouse=self.warehouse,    # ok to pass
        )
        if getattr(txn, "pk", None):
            self.confirm_txn_id = txn

        # Optional: operational AR balance (not the ledger) sync when we finalize as CREDITED
        # (kept consistent with your PurchaseReturn convention)

    @transaction.atomic
    def post_cash_refund_amount(self, amount: Decimal, *, note: str = ""):
        """
        Post a PARTIAL cash/bank refund (<= outstanding):
          DR Cash/Bank          amount
          CR A/R (Customer)     amount
        """
        amount = Decimal(amount or 0)
        if amount <= 0:
            return

        total = self._base_total()
        already = Decimal(self.refunded_amount or 0)
        outstanding = total - already
        if amount > outstanding:
            raise ValidationError("Refund amount exceeds outstanding balance.")

        cust_acct = self._customer_account()
        if not cust_acct:
            raise ValidationError("Customer has no chart of account.")
        if not _cash_or_bank_for(self.warehouse):
            raise ValidationError("No Cash/Bank account configured for this warehouse.")

        txn = post_sale_return(
            date=self.date,
            description=f"Sale Return Partial Refund {self.return_no} ({amount})",
            amount=amount,
            tax=Decimal(0),  # tax was handled at confirm total; adjust if you pro-rate tax
            customer_account=cust_acct,
            warehouse_sales_return_account=self._sales_return_account(),
            refund_cash=True,
            warehouse=self.warehouse,
        )
        if getattr(txn, "pk", None):
            self.refund_txn_id = txn
        self.refunded_amount = (already + amount).quantize(Q2)

    @transaction.atomic
    def post_cash_refund_outstanding(self):
        total = self._base_total()
        already = Decimal(self.refunded_amount or 0)
        outstanding = total - already
        if outstanding <= 0:
            return

        cust_acct = self._customer_account()
        if not cust_acct:
            raise ValidationError("Customer has no chart of account.")
        if not _cash_or_bank_for(self.warehouse):
            raise ValidationError("No Cash/Bank account configured for this warehouse.")

        txn = post_sale_return(
            date=self.date,
            description=f"Sale Return Refund {self.return_no}",
            amount=outstanding,
            customer_account=cust_acct,
            warehouse_sales_return_account=self._sales_return_account(),
            refund_cash=True,
            warehouse=self.warehouse,
        )
        if getattr(txn, "pk", None):
            self.refund_txn_id = txn
        self.refunded_amount = total

    # ---------- totals & validation ----------
    def _recompute_totals_from_items(self):
        total = Decimal("0")
        for li in self.items.all():
            qty  = Decimal(li.quantity or 0)
            rate = Decimal(li.rate or 0)
            line_amount = (qty * rate).quantize(Q2)
            if li.amount != line_amount:
                li.amount = line_amount
                li.save(update_fields=["amount"])
            total += line_amount
        if self.total_amount != total:
            self.total_amount = total.quantize(Q2)

    def _validate_against_invoice_returnables(self):
        """
        Allow multiple partial returns up to delivered quantity.
        If you don't track delivery yet, we fall back to ordered qty.
        Returnable is per (product, batch_number) pair.
        """
        if not self.invoice_id:
            return  # free/standalone return: skip this guard

        # Enforce customer/warehouse match
        if self.customer_id and self.customer_id != self.invoice.customer_id:
            raise ValidationError("Customer must match the selected Sale Invoice.")
        if self.warehouse_id and self.warehouse_id != self.invoice.warehouse_id:
            raise ValidationError("Warehouse must match the selected Sale Invoice.")

        def norm_batch(b):  # normalize blank/None
            return (b or "").strip()

        # Build map of invoice lines by (product, batch_number)
        by_key = {}
        for it in self.invoice.items.all():  # SaleInvoiceItem
            batch_no = ""
            if getattr(it, "batch_id", None) and getattr(it, "batch", None):
                batch_no = norm_batch(getattr(it.batch, "batch_number", ""))
            else:
                batch_no = norm_batch(getattr(it, "batch_number", ""))  # if your schema stores on item
            by_key[(it.product_id, batch_no)] = {
                "invoice_item_id": it.id,
                "ordered_plus_bonus": int((it.quantity or 0) + (getattr(it, "bonus", 0) or 0)),
                "rate": Decimal(getattr(it, "rate", 0) or 0),
            }

        # If you have a Delivery/Dispatch model, compute actual delivered qty per invoice line here.
        # Fallback: treat delivered = ordered_plus_bonus
        delivered_by_item = {v["invoice_item_id"]: v["ordered_plus_bonus"] for v in by_key.values()}

        # Already returned so far (only effective SRs)
        COUNT_STATUSES = {"CONFIRMED", "RETURNED", "REFUNDED", "CREDITED"}
        ret_rows = (
            SaleReturnItem.objects
            .filter(
                return_invoice__invoice=self.invoice,
                return_invoice__status__in=COUNT_STATUSES,
            )
            .exclude(return_invoice=self)  # exclude current SR (we sum it below)
            .values("product_id", "batch_number")
            .annotate(qty=Sum("quantity"))
        )
        returned_by_key = {(r["product_id"], norm_batch(r["batch_number"])): int(r["qty"] or 0) for r in ret_rows}

        # Requested in THIS SR (sum duplicates)
        req_by_key = {}
        for li in self.items.all():
            key = (li.product_id, norm_batch(li.batch_number))
            req_by_key[key] = req_by_key.get(key, 0) + int(li.quantity or 0)

        # Validate each requested group against (delivered - already_returned)
        for key, qty in req_by_key.items():
            info = by_key.get(key)
            if not info:
                raise ValidationError(f"Product/batch {key[0]} / '{key[1]}' not found on the selected invoice.")
            inv_item_id = info["invoice_item_id"]
            delivered = int(delivered_by_item.get(inv_item_id, 0))
            already   = int(returned_by_key.get(key, 0))
            allow     = max(delivered - already, 0)
            if qty > allow:
                raise ValidationError(
                    f"Requested return qty {qty} exceeds returnable {allow} for product/batch {key[0]} / '{key[1]}'."
                )

    # ---------- save wiring ----------
    @transaction.atomic
    def save(self, *args, **kwargs):
        is_create = self.pk is None
        super_save = super(SaleReturn, self).save

        # Always recompute totals & validate before persist on updates
        if not is_create:
            self._recompute_totals_from_items()
            self._validate_against_invoice_returnables()

        # Initial persist
        super_save(*args, **kwargs)

        # On first save (parent row created), recompute after inlines exist
        if is_create:
            if not self.return_no:
                self.return_no = self._next_sequence()
            self._recompute_totals_from_items()
            self._validate_against_invoice_returnables()
            super_save(update_fields=["return_no", "total_amount"])

        # status transitions
        old_status = None
        if self.pk:
            old_status = (
                SaleReturn.objects.filter(pk=self.pk)
                .values_list("status", flat=True)
                .first()
            )
        if old_status is None:
            # fresh save path already handled above
            return

        if old_status != self.status:
            self._validate_transition(old_status, self.status)

            if old_status == "DRAFT" and self.status == "CONFIRMED":
                self.post_confirm_entry()

            if old_status in {"CONFIRMED"} and self.status == "RETURNED":
                self.do_stock_in()

            if self.status == "REFUNDED":
                self.post_cash_refund_outstanding()

            if self.status == "CREDITED":
                # Optional: keep operational A/R in sync (if you track it)
                if self.customer and self.customer.current_balance is not None:
                    self.customer.current_balance = (self.customer.current_balance or 0) - self._base_total()
                    self.customer.save(update_fields=["current_balance"])
                self.refunded_amount = self._base_total()

            self._sync_payment_status()
            super_save(update_fields=["status", "payment_status", "refunded_amount", "confirm_txn_id", "refund_txn_id"])

class SaleReturnItem(models.Model):
    return_invoice = models.ForeignKey(SaleReturn, related_name="items", on_delete=models.CASCADE)
    product       = models.ForeignKey(Product, on_delete=models.CASCADE)
    batch_number  = models.CharField(max_length=50, blank=True)
    expiry_date   = models.DateField(null=True, blank=True)
    quantity      = models.PositiveIntegerField()
    rate          = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    amount        = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    def __str__(self):
        return f"{self.product} x {self.quantity} (SR {self.return_invoice.return_no})"
class RecoveryLog(models.Model):
    invoice = models.ForeignKey(SaleInvoice, on_delete=models.CASCADE, related_name='recovery_logs')
    employee = models.ForeignKey('hr.Employee', on_delete=models.SET_NULL, null=True, blank=True, related_name='recovery_logs')
    date = models.DateField()
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"{self.invoice.invoice_no} - {self.date}"

