from django.contrib import admin
from .models import FinancialYear, PaymentTerm, PaymentSchedule
from .models_receipts import CustomerReceipt, CustomerReceiptAllocation
from django.shortcuts import redirect
from django.urls import path, reverse

from .models_tools import OpeningBalanceTool  
@admin.register(FinancialYear)
class FinancialYearAdmin(admin.ModelAdmin):
    list_display = ("name", "start_date", "end_date", "is_active")
    actions = ["activate_year", "close_year"]

    def activate_year(self, request, queryset):
        for year in queryset:
            year.activate()
    activate_year.short_description = "Activate selected years"

    def close_year(self, request, queryset):
        queryset.update(is_active=False)
    close_year.short_description = "Close selected years"


admin.site.register(PaymentTerm)
admin.site.register(PaymentSchedule)



class CustomerReceiptAllocationInline(admin.TabularInline):
    model = CustomerReceiptAllocation
    extra = 0
    readonly_fields = ("invoice", "amount", "created_at")

@admin.register(CustomerReceipt)
class CustomerReceiptAdmin(admin.ModelAdmin):
    list_display = ("number", "date", "customer", "warehouse", "amount", "unallocated_amount")
    list_filter  = ("date", "warehouse")
    search_fields = ("number", "customer__name")
    inlines = [CustomerReceiptAllocationInline]
    readonly_fields = ("hordak_txn", "unallocated_amount")

    def save_model(self, request, obj, form, change):
        if not obj.number:
            obj.number = obj._next_number()
        super().save_model(request, obj, form, change)
        # Post to Hordak on first save (you can move this to a button if you prefer)
        obj.post()




from .models_tools import OpeningBalanceTool  # import the dummy model

@admin.register(OpeningBalanceTool)
class OpeningBalanceToolAdmin(admin.ModelAdmin):
    # Hide add/change/delete buttons
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False

    # Make sure it’s visible in the menu (view permission = True)
    def has_view_permission(self, request, obj=None): return True

    # When you click the menu item, go straight to the opening balance form
    def changelist_view(self, request, extra_context=None):
        return redirect(reverse("ar-opening-balance-admin"))

    # (Optional) If someone navigates to a “change” URL, also redirect
    def change_view(self, request, object_id, form_url="", extra_context=None):
        return redirect(reverse("ar-opening-balance-admin"))