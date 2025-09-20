from django.contrib import admin,messages
from django.utils.html import format_html
from django.http import HttpResponse
from django.template.loader import render_to_string
from xhtml2pdf import pisa
from inventory.models import Batch, StockMovement
from .models import (
    SaleInvoice,
    SaleInvoiceItem,
    SaleReturn,
    SaleReturnItem,
    RecoveryLog,
)
# from .forms import SaleReturnAdminForm
from django.urls import reverse
from django.db.models import Sum, F, DecimalField, ExpressionWrapper,Prefetch
from django.utils.translation import gettext_lazy as _
from django import forms
from django.http import JsonResponse, Http404
from django.urls import path
from django.http import HttpResponseRedirect, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, render,redirect
from django.urls import path, reverse
from django.db import transaction
from decimal import Decimal
from django.utils.dateformat import format as date_format
from finance.hordak_posting import reverse_txn_generic,post_sale_return_refund_cash,post_sale_return_credit_note,_cash_or_bank
from utils.stock import stock_in, stock_out,stock_return
# --- Inlines ---

#--- PDF generation ---
def generate_pdf_invoice(invoice):
    context = {
        'invoice': invoice,
        'items': invoice.items.all() if hasattr(invoice, 'items') else [],
        'invoice_type': invoice.__class__.__name__,
    }
    html = render_to_string("invoices/pdf_invoice.html", context)
    response = HttpResponse(content_type='application/pdf')
    pisa.CreatePDF(html, dest=response)
    return response

# --- Admin Actions ---

def print_invoice_pdf(modeladmin, request, queryset):
    if queryset.count() == 1:
        return generate_pdf_invoice(queryset.first())
    return HttpResponse("Please select only one invoice to print.")

print_invoice_pdf.short_description = "Print Invoice PDF"
class SaleInvoiceItemInline(admin.TabularInline):
    model = SaleInvoiceItem
    extra = 1
    readonly_fields = ("delivered_qty",)

class PartialDeliveryForm(forms.Form):
    def __init__(self, invoice: SaleInvoice, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for li in invoice.items.select_related("product"):
            remain = li.remaining_to_deliver
            if remain > 0:
                self.fields[f"item_{li.id}"] = forms.IntegerField(
                    min_value=0,
                    max_value=remain,
                    initial=remain,
                    required=False,
                    label=f"{li.product} (remaining {remain})",
                )

class ReceivePaymentForm(forms.Form):
    amount = forms.DecimalField(max_digits=12, decimal_places=2, min_value=0.01, label="Amount")


@admin.register(SaleInvoice)
class SaleInvoiceAdmin(admin.ModelAdmin):
    list_display  = (
        "invoice_no","date","customer","warehouse",
        "total_amount","discount","tax","grand_total",
        "paid_amount","payment_status","status",
    )
    list_filter   = ("status","payment_status","date","warehouse","booking_man_id","delivery_man_id")
    search_fields = ("invoice_no","customer__name")
    readonly_fields = ("invoice_no","grand_total","paid_amount")
    inlines = [SaleInvoiceItemInline]

    change_form_template = "admin/sale/saleinvoice/change_form.html"  # adds buttons

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        obj: SaleInvoice = form.instance
        obj._recalc_totals_from_items()
        obj._recalc_payment_status()
        obj.save(update_fields=["total_amount","grand_total","payment_status"])

    # Bulk actions
    @admin.action(description="Confirm selected invoices")
    def action_confirm(self, request, queryset):
        updated = 0
        with transaction.atomic():
            for inv in queryset:
                if inv.status != "DRAFT":
                    self.message_user(request, f"{inv.invoice_no} already confirmed.", messages.WARNING)
                    continue
                inv.confirm()
                updated += 1
        self.message_user(request, f"Confirmed {updated} invoice(s).", messages.SUCCESS)

    @admin.action(description="Deliver ALL remaining for selected invoices")
    def action_deliver_all(self, request, queryset):
        updated = 0
        with transaction.atomic():
            for inv in queryset:
                try:
                    inv.deliver_all_remaining()
                    updated += 1
                except Exception as e:
                    self.message_user(request, f"{inv.invoice_no}: {e}", messages.ERROR)
        if updated:
            self.message_user(request, f"Delivered (all remaining) for {updated} invoice(s).", messages.SUCCESS)

    actions = [action_confirm, action_deliver_all]

    # Object-level endpoints (buttons in change page)
    def get_urls(self):
        urls = super().get_urls()
        my = [
            path("<int:object_id>/confirm/",         self.admin_site.admin_view(self.obj_confirm_view),         name="sale_saleinvoice_confirm"),
            path("<int:object_id>/deliver/",         self.admin_site.admin_view(self.obj_deliver_all_view),     name="sale_saleinvoice_deliver_all"),
            path("<int:object_id>/deliver-partial/", self.admin_site.admin_view(self.obj_deliver_partial_view), name="sale_saleinvoice_deliver_partial"),
            path("<int:object_id>/pay/",             self.admin_site.admin_view(self.obj_receive_payment_view), name="sale_saleinvoice_pay"),
            path("<int:pk>/cancel/", self.admin_site.admin_view(self.cancel_view), name="sale_saleinvoice_cancel"),
        ]
        return my + urls
    
    def cancel_view(self, request, pk):
        obj = self.get_object(request, pk)
        if not obj:
            messages.error(request, "Invoice not found.")
            return redirect("admin:sale_saleinvoice_changelist")
        try:
            obj.cancel(reason="admin-button")
            messages.success(request, f"Invoice {obj.invoice_no} cancelled and fully reversed.")
        except Exception as e:
            messages.error(request, f"Cancel failed: {e}")
        return redirect(reverse("admin:sale_saleinvoice_change", args=[pk]))
    def obj_confirm_view(self, request, object_id):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        inv = get_object_or_404(SaleInvoice, pk=object_id)
        if inv.status != "DRAFT":
            self.message_user(request, "Only DRAFT can be confirmed.", messages.ERROR)
        else:
            inv.confirm()
            self.message_user(request, "Confirmed.", messages.SUCCESS)
        return HttpResponseRedirect(reverse("admin:sale_saleinvoice_change", args=[inv.pk]))

    def obj_deliver_all_view(self, request, object_id):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        inv = get_object_or_404(SaleInvoice, pk=object_id)
        try:
            inv.deliver_all_remaining()
            self.message_user(request, "Delivered (all remaining).", messages.SUCCESS)
        except Exception as e:
            self.message_user(request, f"Error: {e}", messages.ERROR)
        return HttpResponseRedirect(reverse("admin:sale_saleinvoice_change", args=[inv.pk]))

    def obj_deliver_partial_view(self, request, object_id):
        inv = get_object_or_404(SaleInvoice, pk=object_id)
        if inv.status not in {"CONFIRMED", "DELIVERED"}:
            self.message_user(request, "Deliver only for CONFIRMED/DELIVERED.", messages.ERROR)
            return HttpResponseRedirect(reverse("admin:sale_saleinvoice_change", args=[inv.pk]))

        if request.method == "POST":
            form = PartialDeliveryForm(inv, request.POST)
            if form.is_valid():
                qmap = {}
                for li in inv.items.all():
                    qty = form.cleaned_data.get(f"item_{li.id}")
                    if qty:
                        qmap[li.id] = int(qty)
                try:
                    inv.deliver_partial(qmap)
                    self.message_user(request, "Partial delivery posted.", messages.SUCCESS)
                except Exception as e:
                    self.message_user(request, f"Error: {e}", messages.ERROR)
                return HttpResponseRedirect(reverse("admin:sale_saleinvoice_change", args=[inv.pk]))
        else:
            form = PartialDeliveryForm(inv)

        ctx = {
            **self.admin_site.each_context(request),
            "title": f"Deliver (Partial) – {inv.invoice_no}",
            "opts": self.model._meta,
            "original": inv,
            "form": form,
        }
        return render(request, "admin/sale/saleinvoice/deliver_partial.html", ctx)

    def obj_receive_payment_view(self, request, object_id):
        inv = get_object_or_404(SaleInvoice, pk=object_id)
        if inv.status not in {"CONFIRMED","DELIVERED"}:
            self.message_user(request, "Receive payment only for CONFIRMED/DELIVERED.", messages.ERROR)
            return HttpResponseRedirect(reverse("admin:sale_saleinvoice_change", args=[inv.pk]))

        if request.method == "POST":
            form = ReceivePaymentForm(request.POST)
            if form.is_valid():
                try:
                    inv.receive_payment(form.cleaned_data["amount"])
                    self.message_user(request, "Payment received.", messages.SUCCESS)
                except Exception as e:
                    self.message_user(request, f"Error: {e}", messages.ERROR)
            return HttpResponseRedirect(reverse("admin:sale_saleinvoice_change", args=[inv.pk]))
        else:
            form = ReceivePaymentForm(initial={"amount": inv.outstanding})

        ctx = {
            **self.admin_site.each_context(request),
            "title": f"Receive Payment – {inv.invoice_no}",
            "opts": self.model._meta,
            "original": inv,
            "form": form,
        }
        return render(request, "admin/sale/saleinvoice/receive_payment.html", ctx)
class SaleReturnItemInline(admin.TabularInline):
    model = SaleReturnItem
    extra = 0
    fields = ("product", "batch_number", "expiry_date", "quantity", "rate", "amount", "returned_qty")
    readonly_fields = ("returned_qty",)

@admin.register(SaleReturn)
class SaleReturnAdmin(admin.ModelAdmin):
    list_display = ("return_no","date","customer","warehouse","total_amount","returned_value","refunded_amount","status")
    autocomplete_fields = ("customer","warehouse","invoice")
    inlines = [SaleReturnItemInline]

    class Media:
        js = ("admin/sale_return_autofill.js",)  # updated script below

    # ---- URLs for dedicated pages
    def get_urls(self):
        urls = super().get_urls()
        my = [
            path("<int:pk>/return-products/", self.admin_site.admin_view(self.return_products_view), name="sale_salereturn_return_products"),
            path("<int:pk>/return-payment/",  self.admin_site.admin_view(self.return_payment_view),  name="sale_salereturn_return_payment"),
            path("<int:pk>/cancel/",          self.admin_site.admin_view(self.cancel_view),          name="sale_salereturn_cancel"),
            path("invoice-data/<int:invoice_id>/", self.admin_site.admin_view(self.invoice_data_json), name="sale_salereturn_invoice_data"),
        ]
        return my + urls

    # ---- Page 1: Return products (stock-in exact batches)
    class ReturnProductsForm(forms.Form):
        """
        Renders dynamic rows: one per SaleReturnItem with 'return now' quantity.
        """
        def __init__(self, *args, items_qs, **kwargs):
            super().__init__(*args, **kwargs)
            self.rows = []
            for it in items_qs:
                field = forms.IntegerField(min_value=0, required=False, label=str(it.product))
                self.fields[f"qty_{it.pk}"] = field
                self.initial[f"qty_{it.pk}"] = 0
                self.rows.append(it)

    def return_products_view(self, request, pk):
        sr = get_object_or_404(SaleReturn.objects.prefetch_related("items"), pk=pk)
        if request.method == "POST":
            form = self.ReturnProductsForm(request.POST, items_qs=sr.items.all())
            if form.is_valid():
                try:
                    with transaction.atomic():
                        for it in sr.items.select_for_update():
                            qty = int(form.cleaned_data.get(f"qty_{it.pk}") or 0)
                            if qty <= 0:
                                continue
                            # stock-in to EXACT BATCH that customer returned
                            stock_return(
                                product=it.product,
                                quantity=qty,
                                batch_number=(it.batch_number or ""),
                                
                                reason=f"Sale Return {sr.return_no}",
                            )
                            it.returned_qty = (it.returned_qty or 0) + qty
                            it.save(update_fields=["returned_qty"])
                        # refresh parent returned value
                        sr.recompute_returned_value()
                        if sr.returned_value > 0 and sr.status == "DRAFT":
                            sr.status = "PRODUCTS_RETURNED"
                            sr.save(update_fields=["status"])
                    self.message_user(request, "Products returned and stocked in.", level=messages.SUCCESS)
                except Exception as e:
                    self.message_user(request, f"Error: {e}", level=messages.ERROR)
                return redirect(reverse("admin:sale_salereturn_change", args=[sr.pk]))
        else:
            form = self.ReturnProductsForm(items_qs=sr.items.all())
        return render(request, "admin/sale/salereturn/return_products.html", {"sr": sr, "form": form,"opts": self.model._meta,})

    # ---- Page 2: Return payment (credit note + optional cash refund)
    class ReturnPaymentForm(forms.Form):
        REFUND_CHOICES = (("credit", "Credit to A/R"), ("cash", "Cash refund"))
        settlement = forms.ChoiceField(choices=REFUND_CHOICES)
        refund_amount = forms.DecimalField(min_value=0, decimal_places=2, max_digits=12, required=False)

        def __init__(self, *args, returned_value: Decimal, **kwargs):
            super().__init__(*args, **kwargs)
            self.returned_value = Decimal(returned_value or 0)
            self.fields["refund_amount"].widget.attrs.update({"step": "0.01"})

        def clean(self):
            data = super().clean()
            returned_value = self.returned_value
            settlement = data.get("settlement")
            refund = Decimal(data.get("refund_amount") or 0)
            if settlement == "cash" and (refund <= 0 or refund > returned_value):
                raise forms.ValidationError("Refund must be > 0 and ≤ returned value.")
            return data

    def return_payment_view(self, request, pk):
        sr = get_object_or_404(SaleReturn.objects.select_related("customer","warehouse"), pk=pk)
        sr.recompute_returned_value()
        total_returned = Decimal(sr.returned_value or 0)
        already_refund = Decimal(sr.refunded_amount or 0)
        outstanding = (total_returned - already_refund).quantize(Decimal("0.01"))
        if sr.returned_value <= 0:
            self.message_user(request, "Nothing returned yet. Return products first.", level=messages.WARNING)
            return redirect(reverse("admin:sale_salereturn_change", args=[sr.pk]))

        if request.method == "POST":
            form = self.ReturnPaymentForm(request.POST, returned_value=sr.returned_value)
            if form.is_valid():
                settlement = form.cleaned_data["settlement"]
                refund     = Decimal(form.cleaned_data.get("refund_amount") or 0)

                try:
                    with transaction.atomic():
                        # 1) Credit note for full returned_value (base + tax split if you use tax)
                        cust = sr.customer.chart_of_account
                        sales_ret = getattr(sr.warehouse, "default_sales_return_account", None) or sr.warehouse.default_sales_account
                        tax_acct  = getattr(sr.warehouse, "default_output_tax_account", None)  # optional

                        cn = post_sale_return_credit_note(
                            date=sr.date,
                            description=f"Sale Return Credit Note {sr.return_no}",
                            base_amount=sr.returned_value,
                            tax_amount=Decimal(sr.tax or 0),
                            customer_account=cust,
                            sales_return_account=sales_ret,
                            output_tax_account=tax_acct,
                        )
                        sr.credit_note_txn = cn

                        # 2) Settlement flavor
                        if settlement == "cash" and refund > 0:
                            cash = _cash_or_bank(sr.warehouse)
                            rt = post_sale_return_refund_cash(
                                date=sr.date,
                                description=f"Sale Return Cash Refund {sr.return_no}",
                                amount=refund,
                                customer_account=cust,
                                cash_bank_account=cash,
                            )
                            sr.refund_txn = rt
                            sr.refunded_amount = (sr.refunded_amount or 0) + refund
                            sr.total_amount=sr.refunded_amount
                            sr.status = "REFUNDED" if sr.refunded_amount >= sr.returned_value else "PRODUCTS_RETURNED"
                        else:
                            # purely credit to A/R
                            sr.status = "CREDITED"

                        sr.save(update_fields=["credit_note_txn","refund_txn","refunded_amount","total_amount","status"])
                    self.message_user(request, "Payment/credit processed.", level=messages.SUCCESS)
                except Exception as e:
                    self.message_user(request, f"Error: {e}", level=messages.ERROR)

                return redirect(reverse("admin:sale_salereturn_change", args=[sr.pk]))
        else:
            form = self.ReturnPaymentForm(returned_value=sr.returned_value)
            
        return render(request, "admin/sale/salereturn/return_payment.html", {"sr": sr, "total_returned": total_returned,
        "already_refund": already_refund,"outstanding": outstanding, "form": form,"opts": self.model._meta,})

    # ---- Cancel: reverse accounting + reverse stock-in
    def cancel_view(self, request, pk):
        sr = get_object_or_404(SaleReturn.objects.prefetch_related("items"), pk=pk)
        if request.method != "POST":
            return redirect(reverse("admin:sale_salereturn_change", args=[sr.pk]))

        try:
            with transaction.atomic():
                # reverse stock-in (only returned_qty)
                for it in sr.items.select_for_update():
                    q = int(it.returned_qty or 0)
                    if q > 0:
                        stock_out(
                            product=it.product,
                            quantity=q,
                            warehouse=sr.warehouse,
                            batch_number=(it.batch_number or ""),
                            reason=f"Reverse SR {sr.return_no}",
                        )
                        it.returned_qty = 0
                        it.save(update_fields=["returned_qty"])

                # reverse accounting
                if sr.refund_txn_id:
                    reverse_txn_generic(sr.refund_txn, memo=f"Cancel SR {sr.return_no} – refund")
                    sr.refund_txn = None
                if sr.credit_note_txn_id:
                    reverse_txn_generic(sr.credit_note_txn, memo=f"Cancel SR {sr.return_no} – credit note")
                    sr.credit_note_txn = None

                sr.refunded_amount = Decimal("0.00")
                sr.returned_value  = Decimal("0.00")
                sr.status = "CANCELLED"
                sr.save(update_fields=["status","refunded_amount","returned_value","credit_note_txn","refund_txn"])
            self.message_user(request, "Sale Return cancelled and reversed.", level=messages.SUCCESS)
        except Exception as e:
            self.message_user(request, f"Cancel failed: {e}", level=messages.ERROR)

        return redirect(reverse("admin:sale_salereturn_change", args=[sr.pk]))

    # ---------- JSON used by the JS autofill ----------
   

    def invoice_data_json(self, request, invoice_id: int):
        from sale.models import SaleInvoice
        try:
            inv = (SaleInvoice.objects
                   .select_related("customer","warehouse")
                   .prefetch_related("items__product","items__batch")
                   .get(pk=invoice_id))
        except Exception:
            raise Http404

        def norm(b): return (b or "").strip()

        payload_items = []
        for it in inv.items.all():
            batch_no = norm(getattr(it.batch, "batch_number", "") if getattr(it, "batch_id", None) else getattr(it, "batch_number", ""))
            expiry   = getattr(it.batch, "expiry_date", None) if getattr(it, "batch_id", None) else getattr(it, "expiry_date", None)
            qty      = int((it.quantity or 0) + (getattr(it, "bonus", 0) or 0))
            rate     = Decimal(getattr(it, "rate", 0) or 0)
            payload_items.append({
                "product_id": it.product_id,
                "product_label": str(it.product),
                "batch_number": batch_no,
                "expiry_date": expiry.isoformat() if expiry else "",
                "default_qty": qty,
                "rate": str(rate),
            })

        return JsonResponse({
            "customer":  {"id": inv.customer_id,  "text": str(inv.customer)},
            "warehouse": {"id": inv.warehouse_id, "text": str(inv.warehouse)},
            "items": payload_items,
        })



@admin.register(RecoveryLog)
class RecoveryLogAdmin(admin.ModelAdmin):
    list_display = ['invoice', 'employee', 'notes', 'date']
    list_filter = ['date', 'employee']
