from datetime import date

from django.test import TestCase
from voucher.models import (
    AccountType,
    ChartOfAccount,
    VoucherType,
    Voucher,
    VoucherEntry,
)
from user.models import CustomUser
from .ratios import current_ratio, gross_profit_margin
from decimal import Decimal


from django.urls import reverse
from rest_framework.test import APITestCase



from .financial_statements import account_type_balances


User = get_user_model()

class RatioTests(TestCase):
    def setUp(self):
        asset_type = AccountType.objects.create(name="ASSET")
        liability_type = AccountType.objects.create(name="LIABILITY")
        income_type = AccountType.objects.create(name="INCOME")
        expense_type = AccountType.objects.create(name="EXPENSE")

        asset_acct = ChartOfAccount.objects.create(
            name="Cash", code="A1", account_type=asset_type
        )
        liability_acct = ChartOfAccount.objects.create(
            name="Payable", code="L1", account_type=liability_type
        )
        income_acct = ChartOfAccount.objects.create(
            name="Sales", code="I1", account_type=income_type
        )
        expense_acct = ChartOfAccount.objects.create(
            name="COGS", code="E1", account_type=expense_type
        )

        user = CustomUser.objects.create(email="test@example.com")
        vtype, _ = VoucherType.objects.get_or_create(code="JV", defaults={"name": "Journal"})
        voucher = Voucher.objects.create(
            voucher_type=vtype,
            date=date.today(),
            amount=0,
            narration="",
            created_by=user,
        )
        VoucherEntry.objects.create(voucher=voucher, account=asset_acct, debit=2000, credit=0)
        VoucherEntry.objects.create(voucher=voucher, account=liability_acct, debit=0, credit=1000)
        VoucherEntry.objects.create(voucher=voucher, account=income_acct, debit=0, credit=5000)
        VoucherEntry.objects.create(voucher=voucher, account=expense_acct, debit=3000, credit=0)

    def test_current_ratio(self):
        entries = VoucherEntry.objects.all()
        self.assertAlmostEqual(current_ratio(entries=entries), 2.0)

    def test_gross_profit_margin(self):
        entries = VoucherEntry.objects.all()
        self.assertAlmostEqual(gross_profit_margin(entries=entries), 0.4)




class FinancialStatementTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user("user@example.com", "pass")

        asset = AccountType.objects.create(name="ASSET")
        liability = AccountType.objects.create(name="LIABILITY")

        self.cash = ChartOfAccount.objects.create(
            name="Cash", code="CASH", account_type=asset
        )
        self.loan = ChartOfAccount.objects.create(
            name="Loan", code="LOAN", account_type=liability
        )

        voucher_type, _ = VoucherType.objects.get_or_create(
            code=VoucherType.JOURNAL, defaults={"name": "Journal"}
        )

        # First voucher: cash debit 100, loan credit 100
        Voucher.create_with_entries(
            voucher_type=voucher_type,
            date=date(2024, 1, 15),
            narration="",
            created_by=self.user,
            entries=[
                {"account": self.cash, "debit": Decimal("100"), "credit": Decimal("0")},
                {"account": self.loan, "debit": Decimal("0"), "credit": Decimal("100")},
            ],
        )

        # Second voucher: loan debit 30, cash credit 30
        Voucher.create_with_entries(
            voucher_type=voucher_type,
            date=date(2024, 2, 1),
            narration="",
            created_by=self.user,
            entries=[
                {"account": self.loan, "debit": Decimal("30"), "credit": Decimal("0")},
                {"account": self.cash, "debit": Decimal("0"), "credit": Decimal("30")},
            ],
        )

    def test_account_type_balances_service(self):
        totals = account_type_balances(
            start_date=date(2024, 1, 1), end_date=date(2024, 12, 31)
        )
        self.assertEqual(totals["ASSET"], Decimal("70"))
        self.assertEqual(totals["LIABILITY"], Decimal("-70"))

    def test_financial_statement_view(self):
        self.client.force_authenticate(self.user)
        url = reverse("financial_statement")
        response = self.client.get(url, {"year": 2024})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(Decimal(data["ASSET"]), Decimal("70"))
        self.assertEqual(Decimal(data["LIABILITY"]), Decimal("-70"))


