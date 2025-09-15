# from django.db import models


# class PriceList(models.Model):
#     name = models.CharField(max_length=100)
#     description = models.TextField(blank=True)
#     created_at = models.DateTimeField(auto_now_add=True)

#     def __str__(self):
#         return self.name


# class PriceListItem(models.Model):
#     price_list = models.ForeignKey(PriceList, on_delete=models.CASCADE, related_name='items')
#     product = models.ForeignKey('inventory.Product', on_delete=models.CASCADE)
#     price = models.DecimalField(max_digits=10, decimal_places=2)
#     description = models.TextField(blank=True)

#     def __str__(self):
#         return f"{self.product.name} - {self.price}"
