from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from django.conf import settings

from django.test import TestCase, SimpleTestCase
from unittest.mock import patch


from inventory.models import Party, Product

from setting.models import Branch, Warehouse, Company, Distributor, Group, City, Area
from voucher.models import AccountType, ChartOfAccount, VoucherType
from voucher.test_utils import assert_ledger_entries
from hr.models import Employee
from setting.constants import TAX_PAYABLE_ACCOUNT_CODE

from utils.stock import stock_in, stock_out
from .models import SaleInvoice,SaleReturn,SaleReturnItem



settings.MIGRATION_MODULES = {
    "sale": None,
    "purchase": None,
    "inventory": None,
}



User = get_user_model()


def setup_basic_entities():
    """Factory to create commonly used objects for sales tests."""
    asset = AccountType.objects.create(name="ASSET")
    income = AccountType.objects.create(name="INCOME")
    customer_account = ChartOfAccount.objects.create(
        name="Customer", code="1000", account_type=asset
    )
    sales_account = ChartOfAccount.objects.create(
        name="Sales", code="4000", account_type=income
    )
    branch = Branch.objects.create(name="Main", address="addr")
    warehouse = Warehouse.objects.create(
        name="W1", branch=branch, default_sales_account=sales_account
    )
    city = City.objects.create(name="Metropolis")
    area = Area.objects.create(name="Center", city=city)
    company = Company.objects.create(name="C1")
    group = Group.objects.create(name="G1")
    distributor = Distributor.objects.create(name="D1")
    product = Product.objects.create(
        name="P1",
        barcode="123",
        company=company,
        group=group,
        distributor=distributor,
        trade_price=10,
        retail_price=10,
        sales_tax_ratio=0,
        fed_tax_ratio=0,
        disable_sale_purchase=False,
    )
    customer = Party.objects.create(
        name="Cust",
        address="addr",
        phone="123",
        party_type="customer",
        chart_of_account=customer_account,
        city=city,
        area=area,
    )
    return {
        "customer_account": customer_account,
        "warehouse": warehouse,
        "product": product,
        "customer": customer,
    }


class SaleInvoiceVoucherLinkTest(APITestCase):
    """Ensure a voucher is linked when invoices are created via the API."""

    def setUp(self):

        data = setup_basic_entities()
        self.customer_account = data["customer_account"]
        self.warehouse = data["warehouse"]
        self.product = data["product"]
        self.customer = data["customer"]

        asset = AccountType.objects.create(name="ASSET")
        income = AccountType.objects.create(name="INCOME")
        liability = AccountType.objects.create(name="LIABILITY")
        self.customer_account = ChartOfAccount.objects.create(
            name="Customer", code="1000", account_type=asset
        )
        self.cash_account = ChartOfAccount.objects.create(
            name="Cash", code="1100", account_type=asset
        )
        self.sales_account = ChartOfAccount.objects.create(
            name="Sales", code="4000", account_type=income
        )
        self.tax_payable_account = ChartOfAccount.objects.create(
            name="Tax Payable", code=TAX_PAYABLE_ACCOUNT_CODE, account_type=liability
        )
        self.branch = Branch.objects.create(name="Main", address="addr")
        self.warehouse = Warehouse.objects.create(
            name="W1",
            branch=self.branch,
            default_sales_account=self.sales_account,
            default_cash_account=self.cash_account,
        )
        company = Company.objects.create(name="C1")
        group = Group.objects.create(name="G1")
        distributor = Distributor.objects.create(name="D1")
        self.product = Product.objects.create(
            name="P1",
            barcode="123",
            company=company,
            group=group,
            distributor=distributor,
            trade_price=10,
            retail_price=10,
            sales_tax_ratio=0,
            fed_tax_ratio=0,
            disable_sale_purchase=False,
        )
        self.customer = Party.objects.create(
            name="Cust",
            address="addr",
            phone="123",
            party_type="customer",
            chart_of_account=self.customer_account,
        )

        VoucherType.objects.create(name="Sale", code="SAL")
        self.user = User.objects.create_user("user@example.com", "pass")

    def test_cash_invoice_uses_cash_account(self):
        invoice = SaleInvoice.objects.create(
            invoice_no="INV-001",
            date=date.today(),
            customer=self.customer,
            warehouse=self.warehouse,
            total_amount=10,
            sub_total=10,
            discount=0,
            tax=0,
            paid_amount=10,
            payment_method="Cash",
            status="Paid",
        )

        self.assertIsNotNone(invoice.voucher)
        assert_ledger_entries(
            self,
            invoice.voucher,
            [
                (self.customer_account, Decimal("10"), Decimal("0")),
                (self.sales_account, Decimal("0"), Decimal("10")),
            ],
        )

    def test_cash_invoice_with_tax_posts_tax_payable(self):
        invoice = SaleInvoice.objects.create(
            invoice_no="INV-TAX-1",
            date=date.today(),
            customer=self.customer,
            warehouse=self.warehouse,
            total_amount=100,
            sub_total=100,
            discount=0,
            tax=10,
            paid_amount=110,
            payment_method="Cash",
            status="Paid",
        )

        self.assertIsNotNone(invoice.voucher)
        assert_ledger_entries(
            self,
            invoice.voucher,
            [
                (self.cash_account, Decimal("110"), Decimal("0")),
                (self.sales_account, Decimal("0"), Decimal("100")),
                (self.tax_payable_account, Decimal("0"), Decimal("10")),
            ],
        )

    def test_sale_return_posts_correct_ledger(self):
        return_invoice = SaleReturn.objects.create(
            return_no="RET-1",
            date=date.today(),
            customer=self.customer,
            warehouse=self.warehouse,
            total_amount=5,
        )
        self.assertIsNotNone(return_invoice.voucher)
        assert_ledger_entries(
            self,
            return_invoice.voucher,
            [
                (self.sales_account, Decimal("5"), Decimal("0")),
                (self.customer_account, Decimal("0"), Decimal("5")),
            ],
        )


        debit_entry = invoice.voucher.entries.get(debit=invoice.grand_total)
        credit_entry = invoice.voucher.entries.get(credit=invoice.grand_total)
        self.assertEqual(debit_entry.account, self.cash_account)
        self.assertEqual(credit_entry.account, self.sales_account)

    def test_credit_invoice_uses_customer_account(self):
        invoice = SaleInvoice.objects.create(
            invoice_no="INV-003",
            date=date.today(),
            customer=self.customer,
            warehouse=self.warehouse,
            total_amount=10,
            sub_total=10,
            discount=0,
            tax=0,
            grand_total=10,
            paid_amount=0,
            net_amount=10,
            payment_method="Credit",
            status="Pending",
        )
        debit_entry = invoice.voucher.entries.get(debit=invoice.grand_total)
        credit_entry = invoice.voucher.entries.get(credit=invoice.grand_total)
        self.assertEqual(debit_entry.account, self.customer_account)
        self.assertEqual(credit_entry.account, self.sales_account)


    def test_status_action_updates_status_and_delivery_man(self):
        invoice = SaleInvoice.objects.create(
            invoice_no="INV-002",
            date=date.today(),
            customer=self.customer,
            warehouse=self.warehouse,
            total_amount=10,
            sub_total=10,
            discount=0,
            tax=0,
            payment_method="Cash",
            status="Pending",
        )
        self.client.force_authenticate(user=self.user)
        employee = Employee.objects.create(name="Delivery", phone="123")
        patch_data = {"status": "Paid", "delivery_man_id": employee.id}
        patch_resp = self.client.patch(
            f"/sales/invoices/{invoice.id}/status/", patch_data, format="json"
        )
        self.assertEqual(patch_resp.status_code, 200, patch_resp.data)
        self.assertEqual(patch_resp.data["status"], "Paid")
        self.assertEqual(patch_resp.data["delivery_man_id"], employee.id)


    def test_status_action_requires_status(self):
        invoice = SaleInvoice.objects.create(
            invoice_no="INV-003",
            date=date.today(),
            customer=self.customer,
            warehouse=self.warehouse,
            total_amount=10,
            sub_total=10,
            discount=0,
            tax=0,
            grand_total=10,
            paid_amount=0,
            net_amount=10,
            payment_method="Cash",
            status="Pending",
        )
        self.client.force_authenticate(user=self.user)
        resp = self.client.patch(
            f"/sales/invoices/{invoice.id}/status/", {}, format="json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_stock_out_raises_when_insufficient(self):
        stock_in(
            self.product,
            quantity=5,
            batch_number="B1",
            expiry_date=date.today(),
            purchase_price=5,
            sale_price=8,
            reason="init",
        )
        with self.assertRaises(ValidationError):
            stock_out(self.product, 10, "insufficient")



class SaleBalanceTests(APITestCase):
    """Ensure party balances adjust for different sale transactions."""

    def setUp(self):
        asset = AccountType.objects.create(name="ASSET")
        income = AccountType.objects.create(name="INCOME")
        self.customer_account = ChartOfAccount.objects.create(
            name="Customer", code="1100", account_type=asset
        )
        self.sales_account = ChartOfAccount.objects.create(
            name="Sales", code="4100", account_type=income
        )
        self.branch = Branch.objects.create(name="Main", address="addr")
        self.warehouse = Warehouse.objects.create(
            name="W1", branch=self.branch, default_sales_account=self.sales_account
        )
        self.city = City.objects.create(name="Metropolis")
        self.area = Area.objects.create(name="Center", city=self.city)


class SaleReturnVoucherTest(APITestCase):
    def setUp(self):
        asset = AccountType.objects.create(name="ASSET")
        income = AccountType.objects.create(name="INCOME")
        VoucherType.objects.create(name="Sale", code="SAL")
        VoucherType.objects.create(name="Sale Return", code="SRN")

        self.cash_account = ChartOfAccount.objects.create(
            name="Cash", code="1001", account_type=asset
        )
        self.customer_account = ChartOfAccount.objects.create(
            name="Customer", code="1002", account_type=asset
        )
        self.sales_account = ChartOfAccount.objects.create(
            name="Sales", code="4000", account_type=income
        )

        self.branch = Branch.objects.create(name="Main", address="addr")
        self.warehouse = Warehouse.objects.create(
            name="W1",
            branch=self.branch,
            default_sales_account=self.sales_account,
        )

        company = Company.objects.create(name="C1")
        group = Group.objects.create(name="G1")
        distributor = Distributor.objects.create(name="D1")
        self.product = Product.objects.create(
            name="P1",
            barcode="123",
            company=company,
            group=group,
            distributor=distributor,
            trade_price=10,
            retail_price=10,
            sales_tax_ratio=0,
            fed_tax_ratio=0,
            disable_sale_purchase=False,
        )

        self.batch = Batch.objects.create(
            product=self.product,
            batch_number="B1",
            expiry_date=date.today(),
            purchase_price=10,
            sale_price=10,
            quantity=0,
            warehouse=self.warehouse,
        )


        self.customer = Party.objects.create(
            name="Cust",
            address="addr",
            phone="123",
            party_type="customer",
            chart_of_account=self.customer_account,

            city=self.city,
            area=self.area,
        )
        VoucherType.objects.create(name="Sale", code="SAL")
        VoucherType.objects.create(name="Sale Return", code="SRN")

    def test_cash_sale_does_not_change_balance(self):
        SaleInvoice.objects.create(
            invoice_no="INV-100",
            date=date.today(),
            customer=self.customer,
            warehouse=self.warehouse,
            total_amount=100,
            discount=0,
            tax=0,
            paid_amount=100,
            net_amount=0,
            payment_method="Cash",
            status="Paid",
        )
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.current_balance, 0)

    def test_credit_sale_increases_balance(self):
        SaleInvoice.objects.create(
            invoice_no="INV-101",
            date=date.today(),
            customer=self.customer,
            warehouse=self.warehouse,
            total_amount=100,
            discount=0,
            tax=0,
            paid_amount=0,
            net_amount=0,
            payment_method="Credit",
            status="Pending",
        )
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.current_balance, 100)

    def test_sale_return_reduces_balance(self):
        SaleInvoice.objects.create(
            invoice_no="INV-102",
            date=date.today(),
            customer=self.customer,
            warehouse=self.warehouse,
            total_amount=100,
            discount=0,
            tax=0,
            paid_amount=0,
            net_amount=0,
            payment_method="Credit",
            status="Pending",
        )
        SaleReturn.objects.create(
            return_no="SR-1",
            date=date.today(),
            customer=self.customer,
            warehouse=self.warehouse,
            total_amount=30,
        )
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.current_balance, 70)

    def _create_invoice(self, method):
        return SaleInvoice.objects.create(
            invoice_no=f"INV-{method}",
            date=date.today(),
            customer=self.customer,
            warehouse=self.warehouse,
            total_amount=50,
            sub_total=50,
            discount=0,
            tax=0,
            grand_total=50,
            paid_amount=50 if method == "Cash" else 0,
            net_amount=50,
            payment_method=method,
            status="Paid" if method == "Cash" else "Pending",
        )

    def test_cash_sale_return_accounts_and_inventory(self):
        invoice = self._create_invoice("Cash")
        sr = SaleReturn.objects.create(
            return_no="SR1",
            date=date.today(),
            invoice=invoice,
            customer=self.customer,
            warehouse=self.warehouse,
            total_amount=50,
        )
        SaleReturnItem.objects.create(
            return_invoice=sr,
            product=self.product,
            batch_number="B1",
            expiry_date=date.today(),
            quantity=5,
            rate=10,
            discount1=0,
            discount2=0,
            amount=50,
            net_amount=50,
        )
        sr.save()

        self.batch.refresh_from_db()
        self.assertEqual(self.batch.quantity, 5)
        debit = sr.voucher.entries.get(debit__gt=0)
        credit = sr.voucher.entries.get(credit__gt=0)
        self.assertEqual(debit.account, self.sales_account)
        self.assertEqual(credit.account, self.cash_account)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.current_balance, 0)

    def test_credit_sale_return_accounts_and_party_balance(self):
        self.customer.current_balance = 50
        self.customer.save()
        invoice = self._create_invoice("Credit")
        sr = SaleReturn.objects.create(
            return_no="SR2",
            date=date.today(),
            invoice=invoice,
            customer=self.customer,
            warehouse=self.warehouse,
            total_amount=20,
        )
        SaleReturnItem.objects.create(
            return_invoice=sr,
            product=self.product,
            batch_number="B1",
            expiry_date=date.today(),
            quantity=2,
            rate=10,
            discount1=0,
            discount2=0,
            amount=20,
            net_amount=20,
        )
        sr.save()

        self.batch.refresh_from_db()
        self.assertEqual(self.batch.quantity, 2)
        debit = sr.voucher.entries.get(debit__gt=0)
        credit = sr.voucher.entries.get(credit__gt=0)
        self.assertEqual(debit.account, self.sales_account)
        self.assertEqual(credit.account, self.customer_account)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.current_balance, 30)



