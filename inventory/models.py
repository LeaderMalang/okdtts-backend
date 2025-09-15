from django.db import models
from setting.models import Company, Group, Distributor
from user.models import CustomUser
from hordak.models import Account
# Master Product
class Product(models.Model):
    name = models.CharField(max_length=255)
    barcode = models.CharField(max_length=100, blank=True)
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    group = models.ForeignKey(Group, on_delete=models.CASCADE)
    distributor = models.ForeignKey(Distributor, on_delete=models.CASCADE)
    trade_price = models.DecimalField(max_digits=10, decimal_places=2)
    e_rate = models.DecimalField(max_digits=10, decimal_places=2,default=0)
    retail_price = models.DecimalField(max_digits=10, decimal_places=2)
    sales_tax_ratio = models.DecimalField(max_digits=5, decimal_places=2)
    fed_tax_ratio = models.DecimalField(max_digits=5, decimal_places=2)
    disable_sale_purchase = models.BooleanField(default=False)
    packing= models.CharField(max_length=100, blank=True, null=True)
    image_1 = models.ImageField(upload_to='static/products/', null=True, blank=True)
    image_2 = models.ImageField(upload_to='static/products/', null=True, blank=True)


    @property
    def rate(self):
        """Alias to the product's trade price for unified naming."""
        return self.trade_price

    @rate.setter
    def rate(self, value):
        self.trade_price = value

    @property
    def stock(self):
        """Total available quantity across all batches."""
        return self.batch_set.aggregate(total=models.Sum('quantity'))['total'] or 0
    @stock.setter
    def stock(self, value):
        self.stock = value


   

    def __str__(self):
        return self.name


# Batch per product
class Batch(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    batch_number = models.CharField(max_length=100)
    expiry_date = models.DateField()
    purchase_price = models.DecimalField(max_digits=10, decimal_places=2)
    sale_price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField()
    warehouse = models.ForeignKey('setting.Warehouse', on_delete=models.CASCADE)  # optional but recommended

    @property
    def rate(self):
        """Alias to sale price using common terminology."""
        return self.sale_price

    @rate.setter
    def rate(self, value):
        self.sale_price = value

    @property
    def stock(self):
        """Expose quantity as stock for clarity."""
        return self.quantity

    @stock.setter
    def stock(self, value):
        self.quantity = value

    def __str__(self):
        return f"{self.product.name} - {self.batch_number}"


# Stock movement logs (for audit & reports)
class StockMovement(models.Model):
    MOVEMENT_TYPE_CHOICES = [
        ('IN', 'Stock In'),
        ('OUT', 'Stock Out'),
        ('ADJUST', 'Adjustment'),
    ]
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE)
    movement_type = models.CharField(max_length=10, choices=MOVEMENT_TYPE_CHOICES)
    quantity = models.IntegerField()
    reason = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    ref_model = models.CharField(max_length=100, blank=True)  # e.g., 'SaleInvoice'
    ref_id = models.PositiveIntegerField(null=True, blank=True)  # link to invoice or voucher


    def __str__(self):
        return f"{self.batch} - {self.movement_type} - {self.quantity}"


# Party master (Customer/Supplier)
class Party(models.Model):
    PARTY_TYPES = (
        ('customer', 'Customer'),
        ('supplier', 'Supplier'),
         ('investor', 'Investor'),
    )
    name = models.CharField(max_length=255)
    address = models.TextField()
    phone = models.CharField(max_length=20)
    # email = models.EmailField(null=True, blank=True)
    party_type = models.CharField(max_length=10, choices=PARTY_TYPES)
    category = models.CharField(max_length=100, null=True, blank=True)
    city = models.ForeignKey("setting.City", null=True, blank=True, on_delete=models.SET_NULL)
    area = models.ForeignKey("setting.Area", null=True, blank=True, on_delete=models.SET_NULL)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    price_list = models.CharField(max_length=100, null=True, blank=True)
    proprietor = models.CharField(max_length=255, blank=True)
    license_no = models.CharField(max_length=100, blank=True)
    license_expiry = models.DateField(null=True, blank=True)
    credit_limit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    current_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    chart_of_account = models.ForeignKey(Account, on_delete=models.PROTECT,null=True, blank=True,related_name='party_account')
    user = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True)
    business_image = models.ImageField(upload_to='static/parties/', null=True, blank=True)
    def __str__(self):
        return f"{self.name} ({self.party_type})"
    class Meta:
        indexes = [
            models.Index(fields=["party_type"]),
            models.Index(fields=["city", "area"]),
            models.Index(fields=["name"]),
            models.Index(fields=["phone"]),
            models.Index(fields=["proprietor"]),
        ]


# Custom price lists
class PriceList(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name


class PriceListItem(models.Model):
    price_list = models.ForeignKey(PriceList, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE,related_name="price_list_items")
    custom_price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        unique_together = ('price_list', 'product')

    def __str__(self):
        return f"{self.price_list.name} - {self.product.name}"
