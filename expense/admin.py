# apps/expenses/admin.py
from django.contrib import admin, messages
from django.db import transaction
from .models import Expense, ExpenseCategory
from django import forms
from finance.hordak_posting import ensure_category_expense_account
from hordak.models import Account


# ---- Custom field that avoids Account.__str__ (no get_balance calls) ----
class AccountNoBalanceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        # Light label: no DB-heavy balance lookups
        code = getattr(obj, "code", "") or obj.pk
        return f"{obj.name} [{code}]"

# ---- Forms ----
class ExpenseCategoryAdminForm(forms.ModelForm):
    default_expense_account = AccountNoBalanceField(
        queryset=Account.objects.only("id", "name", "code").filter(type=getattr(Account.TYPES, "expense", "expense")),
        required=False,
        label="Default expense account",
    )
    class Meta:
        model = ExpenseCategory
        fields = "__all__"

class ExpenseAdminForm(forms.ModelForm):
    expense_account  = AccountNoBalanceField(
        queryset=Account.objects.only( "name").filter(type=getattr(Account.TYPES, "expense", "expense")),
        required=True,
        label="Expense (DR)",
    )
    payment_account  = AccountNoBalanceField(
        queryset=Account.objects.only( "name").filter(id__in=[2,3]),  # bank/cash are assets in most charts
        required=True,
        label="Payment (CR)",
    )
    # if you also added payable_account to Expense, include it similarly:
    # payable_account = AccountNoBalanceField(
    #     queryset=Account.objects.only("id", "name", "code").filter(type=getattr(Account.TYPES, "liability", "liability")),
    #     required=False,
    #     label="Payable (CR/DR)",
    # )

    class Meta:
        model = Expense
        fields = "__all__"
@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(admin.ModelAdmin):
    form = ExpenseCategoryAdminForm
    list_display = ("name", "default_expense_account")
    search_fields = ("name",)

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        # Double-ensure (harmless if already set)
        if not obj.default_expense_account_id:
            acct = ensure_category_expense_account(obj.name)
            if obj.default_expense_account_id != acct.id:
                obj.default_expense_account = acct
                obj.save(update_fields=["default_expense_account"])


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    form = ExpenseAdminForm
    list_display = (
        "id",
        "date",
        "category",
        "amount",
        "currency",
        "payment_account",
        "status",
    )
    list_filter = ("status", "currency", "date", "category")
    search_fields = ("id", "description")
    readonly_fields = ("posted_txn", "reversal_txn", "status")

    actions = ("post_expense", "cancel_expense")

    @admin.action(description="Post expense → create Hordak transaction")
    def post_expense(self, request, queryset):
        processed = skipped = 0
        with transaction.atomic():
            for exp in queryset.select_for_update():
                try:
                    if exp.status != "DRAFT":
                        skipped += 1
                        continue
                    exp.post_to_ledger()
                    processed += 1
                except Exception as e:
                    skipped += 1
                    self.message_user(
                        request,
                        f"Expense #{exp.pk}: could not post ({e})",
                        level=messages.WARNING,
                    )
        if processed:
            self.message_user(request, f"Posted {processed} expense(s).", level=messages.SUCCESS)
        if skipped:
            self.message_user(request, f"Skipped {skipped} expense(s).", level=messages.INFO)

    @admin.action(description="Cancel expense → reverse Hordak transaction")
    def cancel_expense(self, request, queryset):
        processed = skipped = 0
        with transaction.atomic():
            for exp in queryset.select_for_update():
                try:
                    if exp.status != "POSTED":
                        skipped += 1
                        continue
                    exp.cancel(memo="Cancelled via admin action")
                    processed += 1
                except Exception as e:
                    skipped += 1
                    self.message_user(
                        request,
                        f"Expense #{exp.pk}: could not cancel ({e})",
                        level=messages.WARNING,
                    )
        if processed:
            self.message_user(request, f"Cancelled {processed} expense(s).", level=messages.SUCCESS)
        if skipped:
            self.message_user(request, f"Skipped {skipped} expense(s).", level=messages.INFO)
