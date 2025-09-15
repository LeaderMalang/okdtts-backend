from django.contrib import admin
from .models import City,Area, Company, Group, Distributor,Branch,Warehouse

@admin.register(City)
class CityAdmin(admin.ModelAdmin):
    list_display = ['name']

@admin.register(Area)
class AreaAdmin(admin.ModelAdmin):
    list_display = ['name', 'city']
    list_filter = ['city']
    search_fields = ['name', 'city__name']

@admin.register(Company)
class GroupAdmin(admin.ModelAdmin):
    list_display = ['name']

@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ['name']

@admin.register(Distributor)
class DistributorAdmin(admin.ModelAdmin):
    list_display = ['name']


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ['name','address','sale_invoice_footer']
@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ['name', 'branch']
    search_fields = ("name", "branch__name")
