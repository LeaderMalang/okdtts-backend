from django.contrib import admin
from .models import AccountType, ChartOfAccount, VoucherType, Voucher, VoucherEntry, BankAccount
from django.forms.models import BaseInlineFormSet
from django.core.exceptions import ValidationError



@admin.action(description="Mark selected vouchers as APPROVED")
def approve_vouchers(modeladmin, request, queryset):
    for voucher in queryset.filter(status='PENDING'):
        voucher.status = 'APPROVED'
        voucher.approved_by = request.user
        voucher.save()
class VoucherEntryInlineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        total_debit = sum(form.cleaned_data.get('debit', 0) or 0 for form in self.forms if not form.cleaned_data.get('DELETE'))
        total_credit = sum(form.cleaned_data.get('credit', 0) or 0 for form in self.forms if not form.cleaned_data.get('DELETE'))

        if total_debit != total_credit:
            raise ValidationError("Total Debit must equal Total Credit.")

@admin.register(AccountType)
class AccountTypeAdmin(admin.ModelAdmin):
    list_display = ['name']

@admin.register(ChartOfAccount)
class ChartOfAccountAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'account_type', 'parent_account', 'is_active']
    list_filter = ['account_type', 'is_active']

@admin.register(VoucherType)
class VoucherTypeAdmin(admin.ModelAdmin):
    list_display = ['code', 'name']



class VoucherEntryInline(admin.TabularInline):  # Or use StackedInline if you prefer
    model = VoucherEntry
    #extra = 1  # Number of empty rows shown initially
    extra = 0
    formset = VoucherEntryInlineFormSet

    def has_add_permission(self, request, obj):
        return not obj or obj.status != 'APPROVED'

    def has_change_permission(self, request, obj=None):
        return not obj or obj.status != 'APPROVED'

    def has_delete_permission(self, request, obj=None):
        return not obj or obj.status != 'APPROVED'


@admin.register(Voucher)
class VoucherAdmin(admin.ModelAdmin):
    list_display = ['voucher_type', 'date', 'amount', 'status', 'created_by', 'approved_by']
    inlines = [VoucherEntryInline]
    actions = [approve_vouchers]
    def save_model(self, request, obj, form, change):
        if obj.status == 'APPROVED' and not obj.approved_by:
            obj.approved_by = request.user
        super().save_model(request, obj, form, change)
        obj.amount = sum(entry.debit for entry in obj.entries.all())
        obj.save()
    def get_readonly_fields(self, request, obj=None):
        if obj and obj.status == 'APPROVED':
            return [f.name for f in self.model._meta.fields]
        return []

@admin.register(BankAccount)
class BankAccountAdmin(admin.ModelAdmin):
    list_display = ['bank_name', 'account_number', 'branch_code', 'account_title']
