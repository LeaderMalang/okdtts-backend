from __future__ import annotations
from django.contrib import admin,messages
from django import forms

from django.contrib.admin.helpers import ActionForm

from django.urls import reverse
from django.db import transaction
from django.http import HttpResponseRedirect

from .models import Order, OrderItem
from setting.models import Warehouse

from decimal import Decimal
class OrderAdminForm(forms.ModelForm):
    # Inline-only helper fields so staff can choose a warehouse when saving/confirming
    warehouse = forms.ModelChoiceField(queryset=Warehouse.objects.all(), required=False, help_text="Required to create the Sale Invoice.")

    class Meta:
        model = Order
        fields = "__all__"


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 1


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    form = OrderAdminForm
    inlines = [OrderItemInline]

    list_display = ["order_no", "customer", "date", "status", "total_amount", "paid_amount", "linked_invoice"]
    list_filter  = ["status", "date", "customer"]
    search_fields = ["order_no", "customer__name"]
    readonly_fields = []

    def linked_invoice(self, obj: Order):
        if obj.sale_invoice_id:
            url = reverse("admin:sale_saleinvoice_change", args=[obj.sale_invoice_id])
            return f'<a href="{url}">{obj.sale_invoice.invoice_no}</a>'
        return "-"
    linked_invoice.allow_tags = True
    linked_invoice.short_description = "Sale Invoice"

    # ------- Actions -------
    actions = ["confirm_orders", "open_linked_invoices"]

    class ConfirmOrderActionForm(ActionForm):
        warehouse = forms.ModelChoiceField(queryset=Warehouse.objects.all(), required=False)

    action_form = ConfirmOrderActionForm

    @admin.action(description="Confirm selected orders (create & link Sale Invoice)")
    def confirm_orders(self, request, queryset):
        form = self.action_form(request.POST)
        warehouse = None
        if form.is_valid():
            warehouse = form.cleaned_data.get("warehouse")
        if warehouse is None:
            warehouse = Warehouse.objects.first()

        if warehouse is None:
            self.message_user(request, "Please configure at least one Warehouse.", level=messages.ERROR)
            return

        created, skipped, errors = 0, 0, 0
        for order in queryset.select_related("sale_invoice", "customer"):
            try:
                if order.status == "Cancelled":
                    skipped += 1
                    continue
                with transaction.atomic():
                    inv = order.confirm(warehouse=warehouse)
                created += 1 if inv else 0
            except Exception as exc:
                errors += 1
                self.message_user(request, f"{order.order_no}: {exc}", level=messages.ERROR)

        if created:
            self.message_user(request, f"Confirmed {created} order(s) and created Sale Invoices.", level=messages.SUCCESS)
        if skipped:
            self.message_user(request, f"Skipped {skipped} (cancelled).", level=messages.WARNING)
        if errors:
            self.message_user(request, f"Errors on {errors} order(s). Check messages.", level=messages.ERROR)

    @admin.action(description="Open linked Sale Invoices (if any)")
    def open_linked_invoices(self, request, queryset):
        # If single selection and linked, redirect straight to the invoice
        qs = queryset.filter(sale_invoice__isnull=False)
        if qs.count() == 1:
            inv = qs.first().sale_invoice
            return HttpResponseRedirect(reverse("admin:sale_saleinvoice_change", args=[inv.pk]))
        if qs.count() == 0:
            self.message_user(request, "No linked invoices found in selection.", level=messages.WARNING)
            return
        self.message_user(request, f"{qs.count()} selected order(s) have linked invoices.", level=messages.INFO)

    # ------- Save hooks -------
    def save_model(self, request, obj: Order, form, change):
        # Keep order total in sync with items
        super().save_model(request, obj, form, change)
        obj._recompute_total_from_items()

        # If user set status to Confirmed on the form, create the invoice too.
        if form.cleaned_data.get("status") == "Confirmed" and not obj.sale_invoice_id:
            warehouse = form.cleaned_data.get("warehouse") or Warehouse.objects.first()
            if not warehouse:
                self.message_user(request, "Select a Warehouse before confirming.", level=messages.ERROR)
                return
            try:
                with transaction.atomic():
                    obj.confirm(warehouse=warehouse)
                self.message_user(request, f"Order {obj.order_no} confirmed and invoice created.", level=messages.SUCCESS)
            except Exception as exc:
                self.message_user(request, f"Could not confirm: {exc}", level=messages.ERROR)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        # After inlines saved, recompute total
        form.instance._recompute_total_from_items()