from django.db import models

from voucher.models import ChartOfAccount
from utils.voucher import create_voucher_for_transaction


class InvestorTransaction(models.Model):
    """Represents money movements between the company and an investor.

    When a transaction is saved a corresponding accounting voucher is
    automatically generated.  The debit and credit accounts for the voucher
    depend on the ``transaction_type``.
    """

    TRANSACTION_TYPES = [
        ("investment", "Investment"),
        ("payout", "Payout"),
        ("profit", "Profit"),
    ]

    investor = models.ForeignKey(
        "inventory.Party",
        on_delete=models.CASCADE,
        related_name="inventor_transaction",
        limit_choices_to={"party_type": "investor"},
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    transaction_type = models.CharField(max_length=10, choices=TRANSACTION_TYPES)
    date = models.DateField()
    description = models.TextField(blank=True)

    # Mapping of transaction type to debit/credit accounts.  ``None`` means the
    # investor's own ledger account should be used for that side of the entry.
    LEDGER_MAP = {
        "investment": {"debit": "CASH", "credit": None},
        "payout": {"debit": None, "credit": "CASH"},
        "profit": {"debit": "PROFIT", "credit": None},
    }

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)

        if is_new:
            mapping = self.LEDGER_MAP.get(self.transaction_type)
            if not mapping:
                return

            debit_account = (
                self.investor.chart_of_account
                if mapping["debit"] is None
                else ChartOfAccount.objects.get(code=mapping["debit"])
            )
            credit_account = (
                self.investor.chart_of_account
                if mapping["credit"] is None
                else ChartOfAccount.objects.get(code=mapping["credit"])
            )

            create_voucher_for_transaction(
                voucher_type_code="INV",
                date=self.date,
                amount=self.amount,
                narration=self.description or f"{self.transaction_type.title()} transaction",
                debit_account=debit_account,
                credit_account=credit_account,
            )

    def __str__(self):
        return f"{self.investor.name} - {self.amount}"
