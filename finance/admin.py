from django.contrib import admin
from .models import FinancialYear, PaymentTerm, PaymentSchedule


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
