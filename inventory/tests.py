from django.urls import reverse
from django.test import TestCase
from datetime import date
from django.core.exceptions import ValidationError

from utils.stock import stock_in, stock_out

from setting.models import Company, Group, Distributor, Branch, Warehouse
from .models import Product, PriceList, PriceListItem, Batch


class PriceListAPITest(TestCase):
    def setUp(self):
        company = Company.objects.create(name="Comp")
        group = Group.objects.create(name="Grp")
        distributor = Distributor.objects.create(name="Dist")
        self.product = Product.objects.create(
            name="Prod",
            barcode="123",
            company=company,
            group=group,
            distributor=distributor,
            trade_price=10,
            retail_price=12,
            sales_tax_ratio=1,
            fed_tax_ratio=1,
            disable_sale_purchase=False,
        )
        self.price_list = PriceList.objects.create(name="List")
        PriceListItem.objects.create(price_list=self.price_list, product=self.product, custom_price=8)

    def test_price_list_detail_endpoint(self):
        url = reverse('price_list_detail', args=[self.price_list.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['name'], 'List')
        self.assertEqual(len(data['items']), 1)
        self.assertEqual(data['items'][0]['custom_price'], '8.00')


class InventoryLevelsAPITest(TestCase):
    def setUp(self):
        company = Company.objects.create(name="Comp")
        group = Group.objects.create(name="Grp")
        distributor = Distributor.objects.create(name="Dist")
        branch = Branch.objects.create(name="Main", address="Addr")
        warehouse = Warehouse.objects.create(name="W1", branch=branch)

        self.p1 = Product.objects.create(
            name="Prod1",
            barcode="111",
            company=company,
            group=group,
            distributor=distributor,
            trade_price=10,
            retail_price=12,
            sales_tax_ratio=1,
            fed_tax_ratio=1,
            disable_sale_purchase=False,
        )
        self.p2 = Product.objects.create(
            name="Prod2",
            barcode="222",
            company=company,
            group=group,
            distributor=distributor,
            trade_price=20,
            retail_price=25,
            sales_tax_ratio=1,
            fed_tax_ratio=1,
            disable_sale_purchase=False,
        )

        Batch.objects.create(
            product=self.p1,
            batch_number="B1",
            expiry_date=date.today(),
            purchase_price=5,
            sale_price=8,
            quantity=10,
            warehouse=warehouse,
        )
        Batch.objects.create(
            product=self.p1,
            batch_number="B2",
            expiry_date=date.today(),
            purchase_price=5,
            sale_price=8,
            quantity=5,
            warehouse=warehouse,
        )
        Batch.objects.create(
            product=self.p2,
            batch_number="B3",
            expiry_date=date.today(),
            purchase_price=7,
            sale_price=9,
            quantity=7,
            warehouse=warehouse,
        )

    def test_inventory_levels_endpoint(self):
        url = reverse('inventory_levels')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        levels = {item['product']['id']: item['totalStock'] for item in data['levels']}
        self.assertEqual(levels[self.p1.id], 15)
        self.assertEqual(levels[self.p2.id], 7)


class StockMovementTests(TestCase):
    def setUp(self):
        company = Company.objects.create(name="Comp")
        group = Group.objects.create(name="Grp")
        distributor = Distributor.objects.create(name="Dist")
        branch = Branch.objects.create(name="Main", address="Addr")
        self.warehouse = Warehouse.objects.create(name="W1", branch=branch)
        self.product = Product.objects.create(
            name="Prod",
            barcode="999",
            company=company,
            group=group,
            distributor=distributor,
            trade_price=5,
            retail_price=7,
            sales_tax_ratio=0,
            fed_tax_ratio=0,
            disable_sale_purchase=False,
        )

    def test_stock_out_raises_on_shortage(self):
        stock_in(
            self.product,
            quantity=3,
            batch_number="B1",
            expiry_date=date.today(),
            purchase_price=2,
            sale_price=4,
            reason="init",
        )
        with self.assertRaises(ValidationError):
            stock_out(self.product, 5, "shortage")
