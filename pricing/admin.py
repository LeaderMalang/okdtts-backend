from django.contrib import admin
from . import models

if hasattr(models, 'PriceGroup'):
    group_display = ['name']
    if hasattr(models.PriceGroup, 'description'):
        group_display.append('description')
    if hasattr(models.PriceGroup, 'created_at'):
        group_display.append('created_at')

    @admin.register(models.PriceGroup)
    class PriceGroupAdmin(admin.ModelAdmin):
        list_display = tuple(group_display)
        search_fields = ('name',)
        list_filter = ('name',)

if hasattr(models, 'PriceItem'):
    group_field = 'price_group' if hasattr(models.PriceItem, 'price_group') else 'price_list'
    price_field = 'custom_price' if hasattr(models.PriceItem, 'custom_price') else 'price'

    @admin.register(models.PriceItem)
    class PriceItemAdmin(admin.ModelAdmin):
        list_display = (group_field, 'product', price_field)
        search_fields = (f'{group_field}__name', 'product__name')
        list_filter = (group_field,)
