from datetime import date


from rest_framework.test import APITestCase

from voucher.models import AccountType, ChartOfAccount,Voucher,VoucherType
from inventory.models import Party
from .models import InvestorTransaction
from django.test import TestCase




def create_investor():
    asset = AccountType.objects.create(name="ASSET")
    account = ChartOfAccount.objects.create(
        name="Investor", code="3000", account_type=asset
    )
    return Party.objects.create(
        name="Inv",
        address="addr",
        phone="123",
        party_type="investor",
        chart_of_account=account,
    )


class InvestorLedgerTests(APITestCase):
    def test_ledger_endpoint(self):
        investor = create_investor()
        InvestorTransaction.objects.create(
            investor=investor,
            amount=100,
            transaction_type="IN",
            date=date.today(),
            description="Invest",
        )
        InvestorTransaction.objects.create(
            investor=investor,
            amount=40,
            transaction_type="OUT",
            date=date.today(),
            description="Payout",
        )
        resp = self.client.get(f"/investor/investors/{investor.id}/ledger/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 2)
        self.assertEqual(resp.data[-1]["balance"], 60)

    def test_ledger_endpoint_invalid_investor(self):
        resp = self.client.get("/investor/investors/999/ledger/")
        self.assertEqual(resp.status_code, 404)




class InvestorTransactionVoucherTests(TestCase):
    """Ensure vouchers are created with correct ledger entries."""

    def setUp(self):
        asset = AccountType.objects.create(name="ASSET")
        liability = AccountType.objects.create(name="LIABILITY")
        expense = AccountType.objects.create(name="EXPENSE")

        self.cash_account = ChartOfAccount.objects.create(
            name="Cash", code="CASH", account_type=asset
        )
        self.profit_account = ChartOfAccount.objects.create(
            name="Profit Distribution", code="PROFIT", account_type=expense
        )
        self.investor_account = ChartOfAccount.objects.create(
            name="Investor Ledger", code="INVESTOR", account_type=liability
        )

        self.investor = Party.objects.create(
            name="Investor A",
            address="addr",
            phone="123",
            party_type="investor",
            chart_of_account=self.investor_account,
        )

        VoucherType.objects.create(name="Investor Txn", code="INV")

    def _get_entries(self):
        voucher = Voucher.objects.latest("id")
        entries = {e.account.code: (e.debit, e.credit) for e in voucher.entries.all()}
        return voucher, entries

    def test_investment_creates_voucher(self):
        InvestorTransaction.objects.create(
            investor=self.investor,
            amount=100,
            transaction_type="investment",
            date=date.today(),
        )

        voucher, entries = self._get_entries()
        self.assertEqual(voucher.amount, 100)
        self.assertEqual(entries["CASH"], (100, 0))
        self.assertEqual(entries["INVESTOR"], (0, 100))

    def test_payout_creates_voucher(self):
        InvestorTransaction.objects.create(
            investor=self.investor,
            amount=50,
            transaction_type="payout",
            date=date.today(),
        )

        voucher, entries = self._get_entries()
        self.assertEqual(entries["INVESTOR"], (50, 0))
        self.assertEqual(entries["CASH"], (0, 50))

    def test_profit_creates_voucher(self):
        InvestorTransaction.objects.create(
            investor=self.investor,
            amount=75,
            transaction_type="profit",
            date=date.today(),
        )

        voucher, entries = self._get_entries()
        self.assertEqual(entries["PROFIT"], (75, 0))
        self.assertEqual(entries["INVESTOR"], (0, 75))


