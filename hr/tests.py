from datetime import date
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch


from django.test import SimpleTestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from django.test import SimpleTestCase, TestCase


from setting.models import Company
from voucher.models import AccountType, ChartOfAccount
from .models import Employee, LeaveRequest, PayrollSlip
from .views import LeaveRequestViewSet

User = get_user_model()


class LeaveRequestApprovalTests(SimpleTestCase):
    def test_leave_balance_deducted_on_approval(self):
        employee = Employee(id=1, name="Test", phone="123")
        request = LeaveRequest(
            employee=employee,
            leave_type="ANNUAL",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 3),
            status="APPROVED",
        )
        viewset = LeaveRequestViewSet()
        mock_balance = MagicMock()
        with patch(
            "hr.views.LeaveBalance.objects.get_or_create",
            return_value=(mock_balance, True),
        ):
            viewset._deduct_balance_if_approved(request, previous_status="PENDING")

        mock_balance.deduct_leave.assert_called_once_with("ANNUAL", 3)



class LeaveRequestStatusAPITest(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user("user@example.com", "pass")
        self.employee = Employee.objects.create(name="Emp", phone="123")
        self.leave = LeaveRequest.objects.create(
            employee=self.employee,
            leave_type="ANNUAL",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 1),
            status="PENDING",
        )

    def test_update_status_endpoint(self):
        self.client.force_authenticate(self.user)
        resp = self.client.patch(
            f"/hr/leave-requests/{self.leave.id}/status/",
            {"status": "APPROVED"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.leave.refresh_from_db()
        self.assertEqual(self.leave.status, "APPROVED")

    def test_status_endpoint_requires_status(self):
        self.client.force_authenticate(self.user)
        resp = self.client.patch(
            f"/hr/leave-requests/{self.leave.id}/status/",
            {},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

class PayrollSlipVoucherTests(TestCase):
    def setUp(self):
        expense = AccountType.objects.create(name="EXPENSE")
        asset = AccountType.objects.create(name="ASSET")
        self.expense_account = ChartOfAccount.objects.create(
            name="Salaries", code="5002", account_type=expense
        )
        self.cash_account = ChartOfAccount.objects.create(
            name="Cash", code="1001", account_type=asset
        )
        Company.objects.create(
            name="C1",
            payroll_expense_account=self.expense_account,
            payroll_payment_account=self.cash_account,
        )
        self.employee = Employee.objects.create(name="John", phone="123")

    def test_voucher_linked_and_net_salary_calculated(self):
        slip = PayrollSlip.objects.create(
            employee=self.employee,
            month=date(2024, 1, 1),
            base_salary=Decimal("3000"),
            present_days=28,
            absent_days=2,
            leaves_paid=1,
            deductions=Decimal("100"),
            net_salary=0,
        )
        slip.refresh_from_db()
        self.assertEqual(slip.net_salary, Decimal("2800"))
        self.assertIsNotNone(slip.voucher)
        voucher = slip.voucher
        entries = list(voucher.entries.all().order_by("id"))
        self.assertEqual(voucher.amount, Decimal("2800"))
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].account, self.expense_account)
        self.assertEqual(entries[0].debit, Decimal("2800"))
        self.assertEqual(entries[1].account, self.cash_account)
        self.assertEqual(entries[1].credit, Decimal("2800"))


