# finance/models_receipts.py  (new file, or add to finance/models.py)
from decimal import Decimal
from django.db import models, transaction
from django.core.exceptions import ValidationError
from hordak.models import Transaction
from inventory.models import Party
from setting.models import Warehouse
# from sale.models import SaleInvoice
from .hordak_posting import post_customer_receipt

class CustomerReceipt(models.Model):
    number = models.CharField(max_length=50, unique=True, blank=True)
    date   = models.DateField()
    customer = models.ForeignKey(Party, on_delete=models.PROTECT, limit_choices_to={"party_type": "customer"})
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    description = models.CharField(max_length=255, blank=True)
    hordak_txn  = models.ForeignKey(Transaction, null=True, blank=True, on_delete=models.SET_NULL)
    unallocated_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def _next_number(self, prefix="RCPT-"):
        last = CustomerReceipt.objects.filter(number__startswith=prefix).order_by("-id").values_list("number", flat=True).first()
        if not last: return f"{prefix}1"
        try: n = int(last.split("-")[-1])
        except Exception: n = 0
        return f"{prefix}{n+1}"

    @transaction.atomic
    def post(self):
        """Post to Hordak once. Also update Party.current_balance (reduce)."""
        if self.hordak_txn_id:
            return  # already posted
        txn = post_customer_receipt(
            date=self.date,
            description=self.description or f"Receipt {self.number or ''} ({self.customer})",
            customer_account=self.customer.chart_of_account,
            amount=Decimal(self.amount or 0),
            warehouse=self.warehouse,
        )
        self.hordak_txn = txn
        self.unallocated_amount = Decimal(self.amount or 0)
        self.save(update_fields=["hordak_txn", "unallocated_amount"])

        # Decrease operational balance
        self.customer.current_balance = (self.customer.current_balance or 0) - Decimal(self.amount or 0)
        self.customer.save(update_fields=["current_balance"])

    @transaction.atomic
    def allocate(self, invoice, amount: Decimal):
        """Non-posting allocation: links a receipt to an invoice and updates invoice fields."""
        amount = Decimal(amount or 0)
        if amount <= 0:
            raise ValidationError("Allocation must be > 0")
        if amount > Decimal(self.unallocated_amount or 0):
            raise ValidationError("Allocation exceeds unallocated amount")
        if invoice.customer_id != self.customer_id:
            raise ValidationError("Invoice belongs to a different customer")
        if invoice.payment_status == "PAID":
            raise ValidationError("Invoice already paid")
        if amount > invoice.outstanding:
            raise ValidationError(f"Allocation {amount} exceeds invoice outstanding {invoice.outstanding}")

        # Create row and update figures
        CustomerReceiptAllocation.objects.create(receipt=self, invoice=invoice, amount=amount)
        self.unallocated_amount = Decimal(self.unallocated_amount or 0) - amount
        self.save(update_fields=["unallocated_amount"])

        invoice.paid_amount = Decimal(invoice.paid_amount or 0) + amount
        if invoice.paid_amount >= invoice.grand_total:
            invoice.payment_status = "PAID"
        elif invoice.paid_amount > 0:
            invoice.payment_status = "PARTIAL"
        else:
            invoice.payment_status = "UNPAID"
        invoice.save(update_fields=["paid_amount", "payment_status"])

class CustomerReceiptAllocation(models.Model):
    receipt = models.ForeignKey(CustomerReceipt, related_name="allocations", on_delete=models.CASCADE)
    invoice = models.ForeignKey("sale.SaleInvoice", related_name="receipt_allocations", on_delete=models.CASCADE)
    amount  = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
