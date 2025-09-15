from datetime import date
from decimal import Decimal

from django.core.management import call_command
from django.db.models import Sum
from django.test import TestCase

from voucher.models import (
    AccountType,
    ChartOfAccount,
    FinancialYear,
    Voucher,
    VoucherEntry,
    VoucherType,
)


class CloseYearCommandTests(TestCase):
    def setUp(self):
        self.asset = AccountType.objects.create(name="ASSET")
        self.income = AccountType.objects.create(name="INCOME")
        self.expense = AccountType.objects.create(name="EXPENSE")
        self.equity = AccountType.objects.create(name="EQUITY")

        self.cash = ChartOfAccount.objects.create(
            name="Cash", code="CASH", account_type=self.asset
        )
        self.sales = ChartOfAccount.objects.create(
            name="Sales", code="SALES", account_type=self.income
        )
        self.rent = ChartOfAccount.objects.create(
            name="Rent", code="RENT", account_type=self.expense
        )
        self.retained = ChartOfAccount.objects.create(
            name="Retained Earnings", code="RE", account_type=self.equity
        )

        self.jv, _ = VoucherType.objects.get_or_create(
            code=VoucherType.JOURNAL, defaults={"name": "Journal"}
        )

        self.year = FinancialYear.objects.create(
            start_date=date(2024, 1, 1), end_date=date(2024, 12, 31)
        )

        Voucher.create_with_entries(
            voucher_type=self.jv,
            date=date(2024, 6, 1),
            narration="Sale",
            created_by=None,
            entries=[
                {"account": self.cash, "debit": Decimal("100"), "credit": 0},
                {"account": self.sales, "debit": 0, "credit": Decimal("100")},
            ],
        )
        Voucher.create_with_entries(
            voucher_type=self.jv,
            date=date(2024, 6, 2),
            narration="Rent",
            created_by=None,
            entries=[
                {"account": self.rent, "debit": Decimal("40"), "credit": 0},
                {"account": self.cash, "debit": 0, "credit": Decimal("40")},
            ],
        )

    def balance(self, account):
        totals = VoucherEntry.objects.filter(
            account=account,
            voucher__date__range=(self.year.start_date, self.year.end_date),
        ).aggregate(debit=Sum("debit"), credit=Sum("credit"))
        debit = totals["debit"] or Decimal("0")
        credit = totals["credit"] or Decimal("0")
        return debit, credit

    def test_close_year_transfers_net_income(self):
        call_command("close_year", self.retained.id)

        self.year.refresh_from_db()
        self.assertTrue(self.year.is_closed)

        d, c = self.balance(self.sales)
        self.assertEqual(d, c)

        d, c = self.balance(self.rent)
        self.assertEqual(d, c)

        d, c = self.balance(self.retained)
        self.assertEqual(c - d, Decimal("60"))
