from django.db import models, transaction
from user.models import CustomUser
from finance.models import FinancialYear

class AccountType(models.Model):
    ACCOUNT_TYPES = [
        ('ASSET', 'Asset'),
        ('LIABILITY', 'Liability'),
        ('INCOME', 'Income'),
        ('EXPENSE', 'Expense'),
        ('EQUITY', 'Equity'),
    ]
    name = models.CharField(max_length=20, choices=ACCOUNT_TYPES, unique=True)

    def __str__(self):
        return self.get_name_display()


class ChartOfAccount(models.Model):
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=20, unique=True)
    account_type = models.ForeignKey(AccountType, on_delete=models.CASCADE)
    parent_account = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.code} - {self.name}"




class VoucherType(models.Model):
    """Lookup for categorising different kinds of vouchers."""

    #: Marker used for general journal entries.
    JOURNAL = "JV"

    name = models.CharField(max_length=100)
    code = models.CharField(max_length=10, unique=True)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name

class VoucherQuerySet(models.QuerySet):
    def active_year(self):
        return self.filter(financial_year=FinancialYear.get_active())


class VoucherManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(financial_year=FinancialYear.get_active())


class Voucher(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
    ]
    voucher_type = models.ForeignKey(VoucherType, on_delete=models.PROTECT)
    date = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    narration = models.TextField(blank=True)
    created_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, related_name='vouchers_created')
    approved_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, related_name='vouchers_approved', blank=True)
    branch = models.ForeignKey('setting.Branch', on_delete=models.SET_NULL, null=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PENDING')
    financial_year = models.ForeignKey(
        FinancialYear, on_delete=models.PROTECT, related_name='vouchers', null=True
    )

    objects = VoucherManager()
    all_objects = models.Manager()

    def __str__(self):
        return f"{self.voucher_type.code} - {self.date}"

    @classmethod
    def create_with_entries(
        cls,
        *,
        voucher_type,
        date,
        narration,
        created_by,
        entries,
        branch=None,
        financial_year=None,
    ):
        """Create a voucher with an arbitrary number of debit/credit lines.

        ``entries`` should be an iterable of dictionaries with keys:
        ``account``, ``debit``, ``credit`` and optional ``remarks``. The
        total debit and credit amounts must balance.
        """

        total_debit = sum(entry["debit"] for entry in entries)
        total_credit = sum(entry["credit"] for entry in entries)
        if total_debit != total_credit:
            raise ValueError("Debit and credit totals must match")
        financial_year = financial_year or FinancialYear.get_active()

        with transaction.atomic():
            voucher = cls.objects.create(
                voucher_type=voucher_type,
                date=date,
                amount=total_debit,
                narration=narration,
                created_by=created_by,
                branch=branch,
                financial_year=financial_year,
            )
            for entry in entries:
                VoucherEntry.objects.create(
                    voucher=voucher,
                    account=entry["account"],
                    debit=entry.get("debit", 0),
                    credit=entry.get("credit", 0),
                    remarks=entry.get("remarks", ""),
                )

        return voucher


class VoucherEntry(models.Model):
    voucher = models.ForeignKey(Voucher, related_name='entries', on_delete=models.CASCADE)
    account = models.ForeignKey(ChartOfAccount, on_delete=models.PROTECT)
    debit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    remarks = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"{self.voucher} | {self.account.name}"


class BankAccount(models.Model):
    bank_name = models.CharField(max_length=100)
    account_number = models.CharField(max_length=50)
    branch_code = models.CharField(max_length=20)
    account_title = models.CharField(max_length=100)

    def __str__(self):
        return f"{self.bank_name} - {self.account_number}"
