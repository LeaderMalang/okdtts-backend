from django.db import models
from hordak.models import Account, Transaction
from decimal import Decimal
from django.db import models, transaction
from django.utils import timezone
from finance.hordak_posting import post_expense_txn, reverse_txn_generic,ensure_category_expense_account
from django.core.exceptions import ValidationError

class ExpenseCategory(models.Model):
    """
    Optional grouping; can hold a default expense Account from Hordak.
    """
    name = models.CharField(max_length=100, unique=True)
    default_expense_account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        help_text="Hordak expense account to DR by default.",
        related_name="expense_categories",
    )

    def __str__(self):
        return self.name

    @transaction.atomic
    def save(self, *args, **kwargs):
        """
        On create (or when missing), auto-provision a Hordak expense account
        named exactly as this category and link it into `default_expense_account`.
        """
        creating = self._state.adding
        super().save(*args, **kwargs)  # save first to get PK

        if creating or not self.default_expense_account_id:
            acct = ensure_category_expense_account(self.name)
            if self.default_expense_account_id != acct.id:
                self.default_expense_account = acct
                super().save(update_fields=["default_expense_account"])


class Expense(models.Model):
    STATUS = (
        ("DRAFT", "Draft"),
        ("POSTED", "Posted"),
        ("CANCELLED", "Cancelled"),
    )

    date = models.DateField(default=timezone.now)
    category = models.ForeignKey(
        ExpenseCategory,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        help_text="Optional category; can supply default expense account.",
    )
    description = models.TextField(blank=True)

    # Monetary
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="PKR")

    # Hordak accounts (no Voucher/ChartOfAccount anywhere)
    # expense_account = models.ForeignKey(
    #     Account,
    #     on_delete=models.PROTECT,
    #     help_text="Hordak expense account to DR.",
    #     related_name="expenses_dr",
    # )
    payment_account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        help_text="Hordak cash/bank account to CR.",
        related_name="expenses_cr",
    )

    # Ledger linkage
    posted_txn = models.ForeignKey(
        Transaction,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="expense_postings",
        help_text="Hordak transaction created on posting.",
    )
    reversal_txn = models.ForeignKey(
        Transaction,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="expense_reversals",
        help_text="Hordak reversal transaction on cancel.",
    )

    status = models.CharField(max_length=12, choices=STATUS, default="DRAFT")

    class Meta:
        ordering = ("-date", "-id")

    def __str__(self):
        return f"Expense #{self.pk or '—'} - {self.amount} {self.currency}"
    # def clean(self):
        
    #     if not self.expense_account and not (
    #         self.category and self.category.default_expense_account_id
    #     ):
    #         raise ValidationError("Either select an expense account or pick a category with a default account.")

    # --- Domain actions ---
    @transaction.atomic
    def post_to_ledger(self):
        if self.status != "DRAFT":
            raise ValueError("Only DRAFT expenses can be posted.")
        if self.posted_txn_id:
            raise ValueError("Expense already posted (transaction exists).")
        if Decimal(self.amount or 0) <= 0:
            raise ValueError("Amount must be > 0 to post.")
        # in post_to_ledger()
        acct= self.category and self.category.default_expense_account
        
        if not acct:
            raise ValueError("No expense account available (category default missing).")
        txn = post_expense_txn(
            date=self.date,
            description=self.description or f"Expense #{self.pk or ''}",
            amount=self.amount,
            expense_account=acct,
            payment_account=self.payment_account,
            currency=self.currency,
        )
        self.posted_txn = txn
        self.status = "POSTED"
        self.save(update_fields=["posted_txn", "status"])

    @transaction.atomic
    def cancel(self, memo: str = ""):
        if self.status != "POSTED":
            raise ValueError("Only POSTED expenses can be cancelled.")
        if not self.posted_txn_id:
            raise ValueError("No posted transaction to reverse.")
        if self.reversal_txn_id:
            raise ValueError("Expense already cancelled (reversal exists).")

        rv = reverse_txn_generic(self.posted_txn, memo=memo or f"Cancel Expense #{self.pk}")
        self.reversal_txn = rv
        self.status = "CANCELLED"
        self.save(update_fields=["reversal_txn", "status"])

    
    # def save(self, *args, **kwargs):
    # # auto-fill from category if empty
    #     if not self.expense_account_id and self.category and self.category.default_expense_account_id:
    #         self.expense_account_id = self.category.default_expense_account_id
    #     super().save(*args, **kwargs)