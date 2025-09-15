from django.db import models

from voucher.models import ChartOfAccount, Voucher, VoucherType
from utils.voucher import create_voucher_for_transaction


class ExpenseCategory(models.Model):
    """Represents a grouping for expenses tied to a chart of account."""
    name = models.CharField(max_length=100, unique=True)
    chart_of_account = models.ForeignKey(ChartOfAccount, on_delete=models.PROTECT)

    def __str__(self) -> str:

        return self.name


class Expense(models.Model):

    """An individual expense entry that posts to accounting via a voucher."""
    date = models.DateField()
    category = models.ForeignKey(ExpenseCategory, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    description = models.TextField(blank=True)
    payment_account = models.ForeignKey(
        ChartOfAccount, on_delete=models.PROTECT, related_name="expense_payments"
    )
    voucher = models.ForeignKey(Voucher, on_delete=models.SET_NULL, null=True, blank=True)

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)

        if (is_new or not self.voucher) and self.category.chart_of_account and self.payment_account:
            # Ensure voucher type for expenses exists
            try:
                VoucherType.objects.get(code="EXP")
            except VoucherType.DoesNotExist:
                VoucherType.objects.create(code="EXP", name="Expense")

            voucher = create_voucher_for_transaction(
                voucher_type_code="EXP",
                date=self.date,
                amount=self.amount,
                narration=self.description or f"Expense for {self.category.name}",
                debit_account=self.category.chart_of_account,
                credit_account=self.payment_account,
                created_by=getattr(self, "created_by", None),
                branch=getattr(self, "branch", None),
            )
            self.voucher = voucher
            super().save(update_fields=["voucher"])

