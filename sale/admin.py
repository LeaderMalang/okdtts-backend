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
# ---------- Inline ----------
class SaleReturnItemInline(admin.TabularInline):
    model = SaleReturnItem
    extra = 0
    fields = ("product", "batch_number", "expiry_date", "quantity", "rate", "amount")
    can_delete = True

# ---------- Split form for refund/credit ----------
class RefundCreditBreakdownForm(forms.Form):
    refund_amount = forms.DecimalField(min_value=0, decimal_places=2, max_digits=12)
    credit_amount = forms.DecimalField(min_value=0, decimal_places=2, max_digits=12)
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, outstanding: Decimal, **kwargs):
        super().__init__(*args, **kwargs)
        self.outstanding = Decimal(outstanding or 0)
        self.fields["refund_amount"].widget.attrs.update({"step": "0.01"})
        self.fields["credit_amount"].widget.attrs.update({"step": "0.01"})

    def clean(self):
        data = super().clean()
        refund = Decimal(data.get("refund_amount") or 0)
        credit = Decimal(data.get("credit_amount") or 0)
        if (refund + credit) != self.outstanding:
            # allow penny rounding differences if you like
            raise forms.ValidationError("Refund + Credit must equal outstanding.")
        return data

# ---------- Admin actions (list view) ----------
@admin.action(description="Confirm selected sale returns (book credit note)")
def action_confirm(modeladmin, request, queryset):
    qs = queryset.select_related("customer", "warehouse")
    done, skipped = 0, 0
    for sr in qs:
        try:
            if sr.status != "DRAFT":
                skipped += 1
                continue
            with transaction.atomic():
                sr.status = "CONFIRMED"
                sr.save(update_fields=["status"])
            done += 1
        except Exception as e:
            modeladmin.message_user(request, f"#{sr.pk} {sr.return_no}: {e}", level=messages.ERROR)
    if done:
        messages.success(request, f"Confirmed {done} sale return(s).")
    if skipped:
        messages.warning(request, f"Skipped {skipped} (not in DRAFT).")

@admin.action(description="Mark returned (stock-in items)")
def action_mark_returned(modeladmin, request, queryset):
    qs = queryset.prefetch_related("items").select_related("warehouse")
    done, skipped, errors = 0, 0, 0
    for sr in qs:
        try:
            if sr.status != "CONFIRMED":
                skipped += 1
                continue
            if not sr.items.exists():
                errors += 1
                modeladmin.message_user(request, f"#{sr.pk} {sr.return_no}: No items to stock-in.", level=messages.ERROR)
                continue
            with transaction.atomic():
                sr.status = "RETURNED"
                sr.save(update_fields=["status"])
            done += 1
        except Exception as e:
            errors += 1
            modeladmin.message_user(request, f"#{sr.pk} {sr.return_no}: {e}", level=messages.ERROR)
    if done:
        messages.success(request, f"Marked RETURNED {done} sale return(s).")
    if skipped:
        messages.warning(request, f"Skipped {skipped} (only CONFIRMED allowed).")

@admin.action(description="Refund outstanding (cash/bank)")
def action_refund_outstanding(modeladmin, request, queryset):
    qs = queryset.select_related("customer", "warehouse")
    done, skipped = 0, 0
    for sr in qs:
        try:
            if sr.status not in {"CONFIRMED", "RETURNED"}:
                skipped += 1
                continue
            total = Decimal(sr.total_amount or 0)
            paid  = Decimal(sr.refunded_amount or 0)
            if total - paid <= 0:
                skipped += 1
                continue
            with transaction.atomic():
                sr.status = "REFUNDED"
                sr.save(update_fields=["status"])
            done += 1
        except Exception as e:
            modeladmin.message_user(request, f"#{sr.pk} {sr.return_no}: {e}", level=messages.ERROR)
    if done:
        messages.success(request, f"Issued cash refund for {done} sale return(s).")
    if skipped:
        messages.warning(request, f"Skipped {skipped} (no outstanding or wrong status).")

@admin.action(description="Mark credited (no cash, just A/R reduced)")
def action_mark_credited(modeladmin, request, queryset):
    qs = queryset.select_related("customer")
    done, skipped = 0, 0
    for sr in qs:
        try:
            if sr.status not in {"CONFIRMED", "RETURNED"}:
                skipped += 1
                continue
            total = Decimal(sr.total_amount or 0)
            paid  = Decimal(sr.refunded_amount or 0)
            if paid >= total:
                skipped += 1
                continue
            with transaction.atomic():
                sr.status = "CREDITED"
                sr.save(update_fields=["status"])
            done += 1
        except Exception as e:
            modeladmin.message_user(request, f"#{sr.pk} {sr.return_no}: {e}", level=messages.ERROR)
    if done:
        messages.success(request, f"Marked CREDITED {done} sale return(s).")
    if skipped:
        messages.warning(request, f"Skipped {skipped} (wrong status or already settled).")

@admin.action(description="Cancel (only DRAFT)")
def action_cancel(modeladmin, request, queryset):
    done, skipped = 0, 0
    for sr in queryset:
        if sr.status != "DRAFT":
            skipped += 1
            continue
        with transaction.atomic():
            sr.status = "CANCELLED"
            sr.save(update_fields=["status"])
        done += 1
    if done:
        messages.success(request, f"Cancelled {done} sale return(s).")
    if skipped:
        messages.warning(request, f"Skipped {skipped} (only DRAFT can be cancelled).")

@admin.action(description="Settle with refund/credit (enter amounts)…")
def action_settle_with_breakdown(modeladmin, request, queryset):
    if queryset.count() != 1:
        modeladmin.message_user(request, "Select exactly one sale return.", level=messages.ERROR)
        return
    sr = queryset.first()
    if sr.status not in {"CONFIRMED", "RETURNED"}:
        modeladmin.message_user(request, "Only CONFIRMED or RETURNED sale returns can be settled.", level=messages.ERROR)
        return
    url = reverse("admin:sales_salereturn_settle_breakdown") + f"?id={sr.pk}"
    return HttpResponseRedirect(url)

# ---------- Admin ----------
@admin.register(SaleReturn)
class SaleReturnAdmin(admin.ModelAdmin):
    list_display = ("return_no", "date", "customer", "warehouse", "total_amount", "status", "payment_status", "refunded_amount")
    list_filter  = ("status", "payment_status", "warehouse", "date")
    search_fields = ("return_no", "customer__name")
    autocomplete_fields = ("customer", "warehouse", "invoice")
    inlines = [SaleReturnItemInline]

    actions = [
        action_confirm,
        action_mark_returned,
        action_refund_outstanding,
        action_mark_credited,
        action_cancel,
        action_settle_with_breakdown,
    ]

    class Media:
        # your JS from the previous step (Select2-safe binding)
        js = ("admin/sale_return_autofill.js",)

    # If you have a custom template with object buttons, add change_form_template and form buttons as needed

    def get_urls(self):
        urls = super().get_urls()
        my = [
            path("settle-breakdown/", self.admin_site.admin_view(self.settle_breakdown_view), name="sales_salereturn_settle_breakdown"),
            # object-level POST endpoints
            path("<int:object_id>/confirm/",  self.admin_site.admin_view(self.obj_confirm_view),  name="sales_salereturn_confirm"),
            path("<int:object_id>/returned/", self.admin_site.admin_view(self.obj_mark_returned), name="sales_salereturn_returned"),
            path("<int:object_id>/refund/",   self.admin_site.admin_view(self.obj_refund_outstanding), name="sales_salereturn_refund"),
            path("<int:object_id>/credited/", self.admin_site.admin_view(self.obj_mark_credited), name="sales_salereturn_credited"),
            path("<int:object_id>/cancel/",   self.admin_site.admin_view(self.obj_cancel), name="sales_salereturn_cancel"),
            # invoice JSON for client-side auto-fill
            path("invoice-data/<int:invoice_id>/", self.admin_site.admin_view(self.invoice_data_json), name="sales_salereturn_invoice_data"),
        ]
        return my + urls

    # --- JSON for invoice selection (used by sale_return_autofill.js) ---
    def invoice_data_json(self, request, invoice_id: int):
        try:
            inv = (
                SaleInvoice.objects
                .select_related("customer", "warehouse")
                .prefetch_related("items__product", "items__batch")
                .get(pk=invoice_id)
            )
        except SaleInvoice.DoesNotExist:
            raise Http404

        def norm_batch(b):
            return (b or "").strip()

        # Build key map from invoice items: (product_id, batch_number) -> info
        by_key = {}
        for it in inv.items.all():
            batch_no = ""
            if getattr(it, "batch_id", None) and getattr(it, "batch", None):
                batch_no = norm_batch(getattr(it.batch, "batch_number", ""))
                expiry = getattr(it.batch, "expiry_date", None)
            else:
                batch_no = norm_batch(getattr(it, "batch_number", ""))
                expiry = getattr(it, "expiry_date", None)

            by_key[(it.product_id, batch_no)] = {
                "invoice_item_id": it.id,
                "rate": Decimal(getattr(it, "rate", 0) or 0),
                "ordered_plus_bonus": int((it.quantity or 0) + (getattr(it, "bonus", 0) or 0)),
                "expiry": expiry,
            }

        # delivered qty fallback = ordered (until you add a real delivery table)
        delivered_by_item = {v["invoice_item_id"]: v["ordered_plus_bonus"] for v in by_key.values()}

        # Already returned (effective SRs)
        COUNT_STATUSES = {"CONFIRMED", "RETURNED", "REFUNDED", "CREDITED"}
        returned_by_key = dict(
            SaleReturnItem.objects.filter(
                return_invoice__invoice=inv,
                return_invoice__status__in=COUNT_STATUSES
            )
            .values_list("product_id", "batch_number")
            .annotate(total=Sum("quantity"))
            .values_list("product_id", "batch_number", "total")
        )
        # The above returns a dict with tuple keys only in recent Django versions; if not, build loop-style.

        items_payload = []
        for (prod_id, batch_no), info in by_key.items():
            inv_item_id = info["invoice_item_id"]
            delivered   = int(delivered_by_item.get(inv_item_id, 0))
            already     = int(returned_by_key.get((prod_id, batch_no), 0) or 0)
            returnable  = max(delivered - already, 0)
            if returnable <= 0:
                continue
            expiry = info["expiry"]
            items_payload.append({
                "invoice_item_id": inv_item_id,
                "product_id": prod_id,
                "product_label": str(getattr(SaleInvoiceItem.objects.get(pk=inv_item_id), "product")),
                "batch_number": batch_no,
                "expiry_date": expiry.isoformat() if expiry else "",
                "rate": str(info["rate"]),
                "max_return_qty": returnable,
                "default_qty": returnable,
            })

        payload = {
            "invoice": {
                "id": inv.id,
                "invoice_no": inv.invoice_no,
                "date": date_format(inv.date, "Y-m-d"),
            },
            "customer": {"id": inv.customer_id, "text": str(inv.customer)},
            "warehouse": {"id": inv.warehouse_id, "text": str(inv.warehouse)},
            "items": items_payload,
        }
        return JsonResponse(payload)

    # --- Settle popup (list view action) ---
    def settle_breakdown_view(self, request):
        sr_id = request.GET.get("id") or request.POST.get("id")
        try:
            sr = self.get_queryset(request).select_related("customer", "warehouse").get(pk=sr_id)
        except Exception:
            self.message_user(request, "Invalid selection.", level=messages.ERROR)
            return redirect("admin:sale_salereturn_changelist")

        if sr.status not in {"CONFIRMED", "RETURNED"}:
            self.message_user(request, "Only CONFIRMED/RETURNED sale returns can be settled.", level=messages.ERROR)
            return redirect("admin:sale_salereturn_changelist")

        total = Decimal(sr.total_amount or 0)
        already = Decimal(sr.refunded_amount or 0)
        outstanding = (total - already)
        if outstanding <= 0:
            self.message_user(request, "Nothing outstanding to settle.", level=messages.WARNING)
            return redirect("admin:sale_salereturn_changelist")

        if request.method == "POST":
            form = RefundCreditBreakdownForm(request.POST, outstanding=outstanding)
            if form.is_valid():
                refund = form.cleaned_data["refund_amount"]
                credit = form.cleaned_data["credit_amount"]
                note = form.cleaned_data.get("note", "")
                try:
                    with transaction.atomic():
                        if refund > 0:
                            sr.post_cash_refund_amount(refund, note=note)
                        if credit > 0:
                            sr.status = "CREDITED"
                            sr.refunded_amount = sr._base_total()
                        else:
                            sr.status = "REFUNDED"
                        sr._sync_payment_status()
                        sr.save(update_fields=["status", "payment_status", "refunded_amount", "refund_txn_id"])
                except Exception as exc:
                    self.message_user(request, f"Error: {exc}", level=messages.ERROR)
                    return redirect("admin:sale_salereturn_changelist")

                self.message_user(request, _(f"Settled {sr.return_no}: refund {refund}, credit {credit}."), level=messages.SUCCESS)
                return redirect("admin:sale_salereturn_changelist")
        else:
            half = (outstanding / Decimal("2")).quantize(Decimal("0.01"))
            form = RefundCreditBreakdownForm(outstanding=outstanding, initial={"refund_amount": half, "credit_amount": outstanding - half})

        ctx = {
            **self.admin_site.each_context(request),
            "title": _("Settle Sale Return – Enter Refund/Credit"),
            "opts": self.model._meta,
            "sr": sr,
            "sr_id": sr.pk,
            "total": total,
            "already": already,
            "outstanding": outstanding,
            "form": form,
        }
        return render(request, "admin/sale/salereturn/settle_breakdown.html", ctx)

    # --- Object-level endpoints for buttons on the change form (optional) ---
    def obj_confirm_view(self, request, object_id):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        sr = get_object_or_404(SaleReturn, pk=object_id)
        if sr.status != "DRAFT":
            self.message_user(request, "Only DRAFT can be confirmed.", level=messages.ERROR)
        else:
            with transaction.atomic():
                sr.status = "CONFIRMED"
                sr.save(update_fields=["status"])
            self.message_user(request, "Confirmed.", level=messages.SUCCESS)
        return redirect(reverse("admin:sale_salereturn_change", args=[sr.pk]))

    def obj_mark_returned(self, request, object_id):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        sr = get_object_or_404(SaleReturn, pk=object_id)
        if sr.status != "CONFIRMED":
            self.message_user(request, "Only CONFIRMED can be marked RETURNED.", level=messages.ERROR)
        elif not sr.items.exists():
            self.message_user(request, "No items to stock-in.", level=messages.ERROR)
        else:
            with transaction.atomic():
                sr.status = "RETURNED"
                sr.save(update_fields=["status"])
            self.message_user(request, "Marked RETURNED.", level=messages.SUCCESS)
        return redirect(reverse("admin:sale_salereturn_change", args=[sr.pk]))

    def obj_refund_outstanding(self, request, object_id):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        sr = get_object_or_404(SaleReturn, pk=object_id)
        if sr.status not in {"CONFIRMED", "RETURNED"}:
            self.message_user(request, "Only CONFIRMED/RETURNED can be refunded.", level=messages.ERROR)
        else:
            total = Decimal(sr.total_amount or 0)
            paid  = Decimal(sr.refunded_amount or 0)
            if total - paid <= 0:
                self.message_user(request, "Nothing outstanding to refund.", level=messages.WARNING)
            else:
                with transaction.atomic():
                    sr.status = "REFUNDED"
                    sr.save(update_fields=["status"])
                self.message_user(request, "Refunded outstanding.", level=messages.SUCCESS)
        return redirect(reverse("admin:sale_salereturn_change", args=[sr.pk]))

    def obj_mark_credited(self, request, object_id):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        sr = get_object_or_404(SaleReturn, pk=object_id)
        if sr.status not in {"CONFIRMED", "RETURNED"}:
            self.message_user(request, "Only CONFIRMED/RETURNED can be credited.", level=messages.ERROR)
        else:
            total = Decimal(sr.total_amount or 0)
            already = Decimal(sr.refunded_amount or 0)
            if already >= total:
                self.message_user(request, "Already fully settled.", level=messages.WARNING)
            else:
                with transaction.atomic():
                    sr.status = "CREDITED"
                    sr.save(update_fields=["status"])
                self.message_user(request, "Marked CREDITED.", level=messages.SUCCESS)
        return redirect(reverse("admin:sale_salereturn_change", args=[sr.pk]))

    def obj_cancel(self, request, object_id):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        sr = get_object_or_404(SaleReturn, pk=object_id)
        if sr.status != "DRAFT":
            self.message_user(request, "Only DRAFT can be cancelled.", level=messages.ERROR)
        else:
            with transaction.atomic():
                sr.status = "CANCELLED"
                sr.save(update_fields=["status"])
            self.message_user(request, "Cancelled.", level=messages.SUCCESS)
        return redirect(reverse("admin:sale_salereturn_change", args=[sr.pk]))

    # Optional: when saving from the add/change form, recompute once after inlines
    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        obj: SaleReturn = form.instance
        obj._recompute_totals_from_items()
        obj.save(update_fields=["total_amount"])



@admin.register(RecoveryLog)
class RecoveryLogAdmin(admin.ModelAdmin):
    list_display = ['invoice', 'employee', 'notes', 'date']
    list_filter = ['date', 'employee']
