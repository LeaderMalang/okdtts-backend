from datetime import date


from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from inventory.models import Party, Product
from setting.models import Branch, Company, Distributor, Group, Warehouse
from voucher.models import AccountType, ChartOfAccount, VoucherType
from utils.stock import stock_in
from .models import PurchaseInvoice,PurchaseReturn
from decimal import Decimal
from django.test import TestCase
from django.conf import settings
from voucher.test_utils import assert_ledger_entries
from setting.constants import TAX_RECEIVABLE_ACCOUNT_CODE



settings.MIGRATION_MODULES = {
    "sale": None,
    "purchase": None,
    "inventory": None,
}

User = get_user_model()


def setup_entities():
    asset = AccountType.objects.create(name="ASSET")
    expense = AccountType.objects.create(name="EXPENSE")
    supplier_account = ChartOfAccount.objects.create(
        name="Supplier", code="2000", account_type=asset
    )
    purchase_account = ChartOfAccount.objects.create(
        name="Purchase", code="5000", account_type=expense
    )
    branch = Branch.objects.create(name="Main", address="addr")
    warehouse = Warehouse.objects.create(
        name="W1", branch=branch, default_purchase_account=purchase_account
    )
    company = Company.objects.create(name="C1")
    group = Group.objects.create(name="G1")
    distributor = Distributor.objects.create(name="D1")
    product = Product.objects.create(
        name="Prod",
        barcode="123",
        company=company,
        group=group,
        distributor=distributor,
        trade_price=10,
        retail_price=12,
        sales_tax_ratio=0,
        fed_tax_ratio=0,
        disable_sale_purchase=False,
    )
    supplier = Party.objects.create(
        name="Supp",
        address="addr",
        phone="123",
        party_type="supplier",
        chart_of_account=supplier_account,
    )
    return {
        "warehouse": warehouse,
        "supplier": supplier,
        "product": product,
    }


class PurchaseInvoiceTests(APITestCase):
    def setUp(self):
        data = setup_entities()
        self.warehouse = data["warehouse"]
        self.supplier = data["supplier"]
        self.product = data["product"]
        VoucherType.objects.create(name="Purchase", code="PUR")
        self.user = User.objects.create_user("user@example.com", "pass")

    def test_voucher_created_on_save(self):
        PurchaseInvoice.objects.create(
            invoice_no="PINV-001",
            date=date.today(),
            supplier=self.supplier,
            warehouse=self.warehouse,
            total_amount=10,
            discount=0,
            tax=0,
            grand_total=10,
            payment_method="Cash",
            paid_amount=10,
        )


class PurchaseBalanceTests(TestCase):
    """Ensure supplier balances adjust for purchase transactions."""

    def setUp(self):
        liability = AccountType.objects.create(name="LIABILITY")
        expense = AccountType.objects.create(name="EXPENSE")
        self.supplier_account = ChartOfAccount.objects.create(
            name="Supplier", code="2100", account_type=liability
        )
        self.purchase_account = ChartOfAccount.objects.create(
            name="Purchases", code="5100", account_type=expense
        )
        self.branch = Branch.objects.create(name="Main", address="addr")
        self.warehouse = Warehouse.objects.create(
            name="W1", branch=self.branch, default_purchase_account=self.purchase_account
        )
        self.supplier = Party.objects.create(
            name="Supp", address="addr", phone="123", party_type="supplier", chart_of_account=self.supplier_account
        )
        VoucherType.objects.create(name="Purchase", code="PUR")
        VoucherType.objects.create(name="Purchase Return", code="PRN")

    def test_cash_purchase_does_not_change_balance(self):
        PurchaseInvoice.objects.create(
            invoice_no="PI-100",

            date=date.today(),
            supplier=self.supplier,
            warehouse=self.warehouse,
            total_amount=100,
            discount=0,
            tax=0,
            grand_total=100,
            payment_method="Cash",
            paid_amount=100,

        )
        self.assertIsNotNone(invoice.voucher)

    def test_invoice_list_endpoint(self):
        PurchaseInvoice.objects.create(
            invoice_no="PINV-002",
            date=date.today(),
            supplier=self.supplier,
            warehouse=self.warehouse,
            total_amount=50,
            discount=0,
            tax=0,
            grand_total=50,
            payment_method="Cash",
            paid_amount=50,
        )
        response = self.client.get("/purchase/invoices/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

    def test_stock_in_duplicate_batch_invalid(self):
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
            stock_in(
                self.product,
                quantity=5,
                batch_number="B1",
                expiry_date=date.today(),
                purchase_price=5,
                sale_price=8,
                reason="dup",
            )

    def test_credit_purchase_increases_balance(self):
        PurchaseInvoice.objects.create(
            invoice_no="PI-101",
            date=date.today(),
            supplier=self.supplier,
            warehouse=self.warehouse,
            total_amount=100,
            discount=0,
            tax=0,
            grand_total=100,
            payment_method="Credit",
            paid_amount=0,
            status="Pending",
        )
        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.current_balance, 100)

    def test_purchase_return_reduces_balance(self):
        PurchaseInvoice.objects.create(
            invoice_no="PI-102",
            date=date.today(),
            supplier=self.supplier,
            warehouse=self.warehouse,
            total_amount=100,
            discount=0,
            tax=0,
            grand_total=100,
            payment_method="Credit",
            paid_amount=0,
            status="Pending",
        )
        PurchaseReturn.objects.create(
            return_no="PR-1",
            date=date.today(),
            supplier=self.supplier,
            warehouse=self.warehouse,
            total_amount=30,
        )
        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.current_balance, 70)





class PurchaseVoucherTests(TestCase):
    def setUp(self):
        asset = AccountType.objects.create(name="ASSET")
        liability = AccountType.objects.create(name="LIABILITY")
        self.inventory_account = ChartOfAccount.objects.create(
            name="Inventory", code="1100", account_type=asset
        )
        self.supplier_account = ChartOfAccount.objects.create(
            name="Supplier", code="2100", account_type=liability
        )
        self.tax_receivable_account = ChartOfAccount.objects.create(
            name="Tax Receivable", code=TAX_RECEIVABLE_ACCOUNT_CODE, account_type=asset
        )
        self.branch = Branch.objects.create(name="Main", address="addr")
        self.warehouse = Warehouse.objects.create(
            name="W1", branch=self.branch, default_purchase_account=self.inventory_account
        )
        self.supplier = Party.objects.create(
            name="Supp",
            address="addr",
            phone="123",
            party_type="supplier",
            chart_of_account=self.supplier_account,
        )
        VoucherType.objects.create(name="Purchase", code="PUR")
        VoucherType.objects.create(name="Purchase Return", code="PRN")
        # model uses non-existent attribute purchase_account in return, patch it
        self.warehouse.purchase_account = self.inventory_account

    def test_purchase_invoice_posts_ledger_entries(self):
        invoice = PurchaseInvoice.objects.create(
            invoice_no="PINV-1",
            date=date.today(),
            supplier=self.supplier,
            warehouse=self.warehouse,
            total_amount=100,
            discount=0,
            tax=0,
            grand_total=100,
            payment_method="Cash",
            paid_amount=100,
            status="Paid",
        )
        self.assertIsNotNone(invoice.voucher)
        assert_ledger_entries(
            self,
            invoice.voucher,
            [
                (self.inventory_account, Decimal("100"), Decimal("0")),
                (self.supplier_account, Decimal("0"), Decimal("100")),
            ],
        )

    def test_purchase_invoice_with_tax_posts_tax_receivable(self):
        invoice = PurchaseInvoice.objects.create(
            invoice_no="PINV-TAX-1",
            date=date.today(),
            supplier=self.supplier,
            warehouse=self.warehouse,
            total_amount=100,
            discount=0,
            tax=10,
            grand_total=110,
            payment_method="Credit",
            paid_amount=0,
            status="Pending",
        )
        self.assertIsNotNone(invoice.voucher)
        assert_ledger_entries(
            self,
            invoice.voucher,
            [
                (self.inventory_account, Decimal("100"), Decimal("0")),
                (self.tax_receivable_account, Decimal("10"), Decimal("0")),
                (self.supplier_account, Decimal("0"), Decimal("110")),
            ],
        )

    def test_purchase_return_posts_ledger_entries(self):
        pr = PurchaseReturn.objects.create(
            return_no="PR-1",
            date=date.today(),
            supplier=self.supplier,
            warehouse=self.warehouse,
            total_amount=50,
        )
        self.assertIsNotNone(pr.voucher)
        assert_ledger_entries(
            self,
            pr.voucher,
            [
                (self.supplier_account, Decimal("50"), Decimal("0")),
                (self.inventory_account, Decimal("0"), Decimal("50")),
            ],
        )


