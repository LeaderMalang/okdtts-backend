from django.db import models
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from user.models import CustomUser
from decimal import Decimal
from datetime import date as date_cls
from django.db import models, transaction
from django.utils import timezone
from django.core.exceptions import ValidationError
from hordak.models import Transaction, Account
from setting.models import Company
from hordak.models import Account
from finance.hordak_posting import post_payroll_confirm_txn,post_payroll_payment_txn
class EmployeeRole(models.TextChoices):
    SUPER_ADMIN = "SUPER_ADMIN", "Super Admin"
    CUSTOMER = "CUSTOMER", "Customer"
    MANAGER = "MANAGER", "Manager"
    SALES = "SALES", "Sales"
    DELIVERY = "DELIVERY", "Delivery"
    WAREHOUSE_ADMIN = "WAREHOUSE_ADMIN", "Warehouse Admin"
    DELIVERY_MANAGER = "DELIVERY_MANAGER", "Delivery Manager"
    RECOVERY_OFFICER = "RECOVERY_OFFICER", "Recovery Officer"
    INVESTOR = "INVESTOR", "Investor"
    SUPPLIER = "supplier", "Supplier"

class Employee(models.Model):

    user = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employee",
    )
    role = models.CharField(
        max_length=30, choices=EmployeeRole.choices, blank=True, null=True
    )
    name = models.CharField(max_length=100)

    phone = models.CharField(max_length=15)
    # email = models.EmailField(blank=True, null=True)
    cnic = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)
    active = models.BooleanField(default=True)
  

    def __str__(self):
        return self.name


class EmployeeContract(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE)
    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)
    salary = models.DecimalField(max_digits=10, decimal_places=2)
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"{self.employee.name} - Contract"


class LeaveRequest(models.Model):
    LEAVE_TYPE_CHOICES = [
        ('ANNUAL', 'Annual'),
        ('SICK', 'Sick'),
        ('CASUAL', 'Casual'),
    ]

    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
    ]

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE)
    leave_type = models.CharField(max_length=20, choices=LEAVE_TYPE_CHOICES)
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PENDING')
    applied_on = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='leave_reviews')

    def __str__(self):
        return f"{self.employee.name} - {self.leave_type}"


class SalesTarget(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE)
    month = models.DateField(help_text="1st of the target month")
    target_amount = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        unique_together = ('employee', 'month')

    def __str__(self):
        return f"{self.employee.name} - {self.month.strftime('%B %Y')}"


class DeliveryAssignment(models.Model):
    employee = models.ForeignKey(Employee, limit_choices_to={'role': 'DELIVERY'}, on_delete=models.CASCADE)
    sale = models.ForeignKey('sale.SaleInvoice', on_delete=models.CASCADE)
    assigned_date = models.DateField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=[
        ('ASSIGNED', 'Assigned'),
        ('DELIVERED', 'Delivered'),
        ('FAILED', 'Failed'),
    ], default='ASSIGNED')
    remarks = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.employee.name} - {self.sale.invoice_no}"


class Attendance(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE)
    date = models.DateField()
    check_in = models.TimeField(null=True, blank=True)
    check_out = models.TimeField(null=True, blank=True)
    is_absent = models.BooleanField(default=False)
    remarks = models.TextField(blank=True)

    class Meta:
        unique_together = ('employee', 'date')




class LeaveBalance(models.Model):
    employee = models.OneToOneField(Employee, on_delete=models.CASCADE)
    annual = models.DecimalField(max_digits=5, decimal_places=1, default=12.0)
    sick = models.DecimalField(max_digits=5, decimal_places=1, default=8.0)
    casual = models.DecimalField(max_digits=5, decimal_places=1, default=4.0)

    def deduct_leave(self, leave_type, days):
        if leave_type == 'ANNUAL':
            self.annual = max(0, self.annual - days)
        elif leave_type == 'SICK':
            self.sick = max(0, self.sick - days)
        elif leave_type == 'CASUAL':
            self.casual = max(0, self.casual - days)
        self.save()




class PayrollSlip(models.Model):
    STATUS = (
        ("DRAFT", "Draft"),
        ("CONFIRMED", "Confirmed (Accrued)"),
        ("PAID", "Paid"),
    )

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE)
    month = models.DateField(help_text="1st of the payroll month")

    base_salary = models.DecimalField(max_digits=12, decimal_places=2)
    present_days = models.PositiveIntegerField()
    absent_days = models.PositiveIntegerField()
    leaves_paid = models.PositiveIntegerField(default=0)
    deductions = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    net_salary = models.DecimalField(max_digits=12, decimal_places=2)

    # --- Hordak integration (no vouchers) ---
    # Optional per-slip overrides; if not set, resolve from company defaults
    expense_account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        help_text="Expense account to DR on confirmation (e.g., Wages Expense).",
        related_name="payroll_expense_dr",
    )
    payable_account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        help_text="Liability to CR on confirmation and DR on payment (e.g., Salaries Payable).",
        related_name="payroll_payable_liab",
    )
    payment_account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        help_text="Cash/Bank to CR on payment.",
        related_name="payroll_cash_cr",
    )

    # Posted transactions
    accrual_txn = models.ForeignKey(
        Transaction,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payroll_accruals",
        help_text="Hordak transaction created on confirmation.",
    )
    payment_txn = models.ForeignKey(
        Transaction,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payroll_payments",
        help_text="Hordak transaction created on payment.",
    )

    status = models.CharField(max_length=12, choices=STATUS, default="DRAFT")
    created_on = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("employee", "month")
        ordering = ("-month", "-id")

    def __str__(self):
        return f"{self.employee} - {self.month.strftime('%B %Y')}"

    # --- internal helpers ---
    def _resolve_accounts(self):
        """
        Resolve accounts from per-slip override or from company settings.
        Implement your own company-level resolution if needed.
        """
        exp = self.expense_account
        pay = self.payable_account
        cash = self.payment_account

        # Example if you have company defaults:
        # company = getattr(self.employee, "company", None) or Company.objects.first()
        # if not exp and company: exp = company.payroll_expense_account
        # if not pay and company: pay = company.payroll_payable_account
        # if not cash and company: cash = company.payroll_payment_account

        return exp, pay, cash

    # --- calculations ---
    def _compute_net(self) -> Decimal:
        total_days = self.present_days + self.absent_days
        per_day = (self.base_salary / total_days) if total_days else Decimal("0")
        unpaid_absent = max(self.absent_days - (self.leaves_paid or 0), 0)
        return self.base_salary - (per_day * unpaid_absent) - (self.deductions or 0)

    def clean(self):
        # recompute net for validation consistency
        self.net_salary = self._compute_net()
        if self.net_salary <= 0:
            raise ValidationError("Net salary must be greater than 0.")

    def save(self, *args, **kwargs):
        self.net_salary = self._compute_net()
        super().save(*args, **kwargs)

    # --- domain actions ---
    @transaction.atomic
    def confirm(self):
        if self.status != "DRAFT":
            raise ValidationError("Only DRAFT slips can be confirmed.")
        exp, pay, _cash = self._resolve_accounts()
        if not exp or not pay:
            raise ValidationError("Expense and Payable accounts are required to confirm payroll.")
        if self.accrual_txn_id:
            raise ValidationError("Accrual transaction already exists.")

        desc = f"Payroll accrual for {self.employee} - {self.month.strftime('%B %Y')}"
        txn = post_payroll_confirm_txn(
            date=self.month if isinstance(self.month, date_cls) else timezone.now().date(),
            description=desc,
            amount=self.net_salary,
            expense_account=exp,
            payable_account=pay,
        )
        self.accrual_txn = txn
        self.status = "CONFIRMED"
        self.save(update_fields=["accrual_txn", "status"])

    @transaction.atomic
    def mark_paid(self, *, payment_date=None):
        if self.status != "CONFIRMED":
            raise ValidationError("Only CONFIRMED slips can be marked PAID.")
        _exp, pay, cash = self._resolve_accounts()
        if not pay or not cash:
            raise ValidationError("Payable and Cash/Bank accounts are required to pay payroll.")
        if self.payment_txn_id:
            raise ValidationError("Payment transaction already exists.")

        desc = f"Payroll payment for {self.employee} - {self.month.strftime('%B %Y')}"
        txn = post_payroll_payment_txn(
            date=payment_date or timezone.now().date(),
            description=desc,
            amount=self.net_salary,
            payable_account=pay,
            cash_bank_account=cash
        )
        self.payment_txn = txn
        self.status = "PAID"
        self.save(update_fields=["payment_txn", "status"])


class Task(models.Model):
    """General task assigned to an employee."""

    STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("IN_PROGRESS", "In Progress"),
        ("COMPLETED", "Completed"),
        ("CANCELLED", "Cancelled"),
    ]

    assignment = models.CharField(max_length=255)
    assigned_to = models.ForeignKey(
        Employee, related_name="tasks", on_delete=models.CASCADE
    )
    assigned_by = models.ForeignKey(
        Employee,
        related_name="assigned_tasks",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    due_date = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="PENDING")

    # Optional links
    party = models.ForeignKey(
        "inventory.Party", on_delete=models.SET_NULL, null=True, blank=True
    )
    invoice_content_type = models.ForeignKey(
        ContentType, on_delete=models.SET_NULL, null=True, blank=True
    )
    invoice_object_id = models.PositiveIntegerField(null=True, blank=True)
    invoice = GenericForeignKey("invoice_content_type", "invoice_object_id")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.assignment
