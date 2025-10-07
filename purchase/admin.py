from django.contrib import admin,messages
from django import forms
from django.http import HttpResponse,HttpResponseRedirect,HttpResponseNotAllowed
from django.template.loader import render_to_string
from xhtml2pdf import pisa
from django.db import transaction
from .models import (
    PurchaseInvoice,
    PurchaseInvoiceItem,
    PurchaseReturn,
    PurchaseReturnItem,
    InvestorTransaction,
    GoodsReceipt,
    GoodsReceiptItem,
)
from inventory.models import Batch, StockMovement
from decimal import Decimal
from .admin_forms import RefundCreditBreakdownForm,PaymentCreditBreakdownForm,GRNLineFormSet,PurchaseInvoiceItemForm,PurchaseInvoiceItemFormSet
from django.urls import path, reverse
from django.shortcuts import render, redirect,get_object_or_404
from django.utils.translation import gettext_lazy as _
from django.http import JsonResponse, Http404
from django.utils.dateformat import format as date_format
from django.db.models import Sum
from django.core.exceptions import ValidationError
from .helpers import grn_returnable_map


# --- PurchaseInvoiceItemInline ---
class PurchaseInvoiceItemInline(admin.TabularInline):
    model = PurchaseInvoiceItem
    form = PurchaseInvoiceItemForm
    formset = PurchaseInvoiceItemFormSet
    extra = 1
    autocomplete_fields = ["product"]
    fields = ("product", "quantity", "purchase_price", "sale_price", "batch_number", "expiry_date", "line_total")
    # readonly_fields = ("line_total",)

    class Media:
        js = ("purchase/purchase_totals.js",)  # see step 4


# --- PDF Helper ---

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
@admin.action(description="Settle with payment/credit (enter amounts)…")
def action_settle_with_breakdown(modeladmin, request, queryset):
    if queryset.count() != 1:
        modeladmin.message_user(request, "Select exactly one purchase invoice.", level=messages.ERROR)
        return
    inv = queryset.first()
    if inv.status not in {"CONFIRMED", "RECEIVED"}:
        modeladmin.message_user(
            request, "Only CONFIRMED or RECEIVED invoices can be settled.", level=messages.ERROR
        )
        return
    url = reverse("admin:purchase_purchaseinvoice_settle_breakdown") + f"?id={inv.pk}"
    return HttpResponseRedirect(url)



@admin.action(description="Cancel selected Purchase Invoices (reverse GRN, payments, and accounting)")
def cancel_purchase_invoices(modeladmin, request, queryset):
    success, failed = 0, 0
    for pi in queryset.select_for_update():
        try:
            with transaction.atomic():
                pi.cancel(reason=f"Cancelled by {request.user}")
                success += 1
        except Exception as e:
            failed += 1
            messages.error(request, f"{pi.invoice_no or pi.pk}: {e}")
    if success:
        messages.success(request, f"Cancelled {success} invoice(s).")
    if failed:
        messages.warning(request, f"Failed to cancel {failed} invoice(s).")
@admin.register(PurchaseInvoice)
class PurchaseInvoiceAdmin(admin.ModelAdmin):
    list_display = ("invoice_no", "supplier", "date", "status", "payment_status", "grand_total", "paid_amount")
    list_filter = ("status", "payment_status", "date", "supplier")
    search_fields = ("invoice_no", "company_invoice_number", "supplier__name")
    autocomplete_fields = ("supplier", "warehouse")
    readonly_fields = ("total_amount", "grand_total",) 
    inlines = [PurchaseInvoiceItemInline]
    actions = ["action_confirm", "action_receive", "action_mark_paid","print_invoice_pdf",action_settle_with_breakdown,cancel_purchase_invoices]

    @admin.action(description="Confirm selected invoices")
    def action_confirm(self, request, queryset):
        updated = 0
        with transaction.atomic():
            for invoice in queryset:
                if invoice.status != "DRAFT":
                    self.message_user(request, f"Invoice {invoice.invoice_no} already confirmed.", messages.WARNING)
                    continue
                invoice.confirm()  # Ensure confirmed
                
                invoice.save()
                updated += 1
        self.message_user(request, f"{updated} invoice(s) confirmed.", messages.SUCCESS)

    @admin.action(description="Receive stock for selected invoices")
    def action_receive(self, request, queryset):
        updated = 0
        with transaction.atomic():
            for invoice in queryset:
                if invoice.status != "CONFIRMED":
                    self.message_user(request, f"Invoice {invoice.invoice_no} must be CONFIRMED before receiving.", messages.ERROR)
                    continue
                
                invoice.receive()
                invoice.save()   # triggers stock_in + Hordak posting
                updated += 1
        self.message_user(request, f"{updated} invoice(s) marked as received.", messages.SUCCESS)

    @admin.action(description="Mark selected invoices as Paid")
    def action_mark_paid(self, request, queryset):
        updated = 0
        with transaction.atomic():
            for invoice in queryset:
                if invoice.payment_status == "PAID":
                    self.message_user(request, f"Invoice {invoice.invoice_no} already paid.", messages.WARNING)
                    continue
                if invoice.outstanding > 0:
                    self.message_user(request, f"Invoice {invoice.invoice_no} still has outstanding {invoice.outstanding}.", messages.ERROR)
                    continue
                invoice.payment_status = "PAID"
                invoice.save(update_fields=["payment_status"])
                updated += 1
        self.message_user(request, f"{updated} invoice(s) marked as paid.", messages.SUCCESS)

    # ---- custom intermediate page ----
    def get_urls(self):
        urls = super().get_urls()
        my_urls = [
            path(
                "settle-breakdown/",
                self.admin_site.admin_view(self.settle_breakdown_view),
                name="purchase_purchaseinvoice_settle_breakdown",
            ),
            path("<int:object_id>/confirm/",  self.admin_site.admin_view(self.obj_confirm_view),  name="purchase_purchaseinvoice_confirm"),
            # path("<int:object_id>/receive/",  self.admin_site.admin_view(self.obj_receive_view),  name="purchase_purchaseinvoice_receive"),
            path("<int:object_id>/settle-breakdown/", self.admin_site.admin_view(self.settle_breakdown_view_obj), name="purchase_purchaseinvoice_settle_breakdown_obj"),
            path(
                "<int:object_id>/receive-partial/",
                self.admin_site.admin_view(self.receive_partial_view),
                name="purchase_purchaseinvoice_receive_partial",
            ),
            path(
                "<int:object_id>/cancel-purchase/",
                self.admin_site.admin_view(self.cancel_purchase_invoices),
                name="purchase_purchaseinvoice_cancel",
            ),
       ]
        return my_urls + urls
    def cancel_purchase_invoices(self, request, object_id):
        inv = get_object_or_404(
            self.get_queryset(request).select_related("supplier","warehouse"),
            pk=object_id
        )
        try:
            with transaction.atomic():
                inv.cancel(reason=f"Cancelled by {request.user}")
              
        except Exception as e:
         
            # messages.error(request, f"{inv.invoice_no or inv.pk}: {e}")
            self.message_user(request, f"{inv.invoice_no or inv.pk}: {e}", level=messages.ERROR)
            return redirect(reverse("admin:purchase_purchaseinvoice_change", args=[inv.pk]))
        self.message_user(request, f"Purchase Invoice {inv.invoice_no} had been cancelled", level=messages.SUCCESS)
        return redirect(reverse("admin:purchase_purchaseinvoice_change", args=[inv.pk]))
    def receive_partial_view(self, request, object_id):
        inv = get_object_or_404(
            self.get_queryset(request).select_related("supplier","warehouse"),
            pk=object_id
        )
        if inv.status not in {"CONFIRMED", "PARTIAL"}:
            self.message_user(request, "Only CONFIRMED/PARTIAL invoices can be received.", level=messages.ERROR)
            return redirect(reverse("admin:purchase_purchaseinvoice_change", args=[inv.pk]))

        # Build initial rows for outstanding items
        outstanding = inv.outstanding_receive_map()
        pi_items = (inv.items
                    .select_related("product")
                    .filter(id__in=[k for k,v in outstanding.items() if v>0]))

        if request.method == "POST":
            formset = GRNLineFormSet(request.POST)
            if formset.is_valid():
                lines = []
                total_rows = 0
                for f in formset:
                    data = f.cleaned_data
                    qty = int(data.get("receive_qty") or 0)
                    if qty <= 0:
                        continue
                    total_rows += 1
                    iid = data["invoice_item_id"]
                    allow = int(outstanding.get(iid, 0))
                    if qty > allow:
                        self.message_user(request, f"Qty {qty} exceeds outstanding {allow} for item #{iid}.", level=messages.ERROR)
                        return redirect(reverse("admin:purchase_purchaseinvoice_change", args=[inv.pk]))
                    lines.append(data)

                if total_rows == 0:
                    self.message_user(request, "Nothing to receive.", level=messages.WARNING)
                    return redirect(reverse("admin:purchase_purchaseinvoice_change", args=[inv.pk]))

                # Create and post GRN
                with transaction.atomic():
                    grn = GoodsReceipt.objects.create(
                        invoice=inv, warehouse=inv.warehouse, date=inv.date, note=f"Partial receive for {inv.invoice_no}"
                    )
                    # Map invoice items for convenience
                    item_map = {it.id: it for it in inv.items.all()}
                    for row in lines:
                        it = item_map[row["invoice_item_id"]]
                        GoodsReceiptItem.objects.create(
                            grn=grn,
                            invoice_item=it,
                            quantity=row["receive_qty"],
                            batch_number=row.get("batch_number") or it.batch_number,
                            expiry_date=row.get("expiry_date") or it.expiry_date,
                            purchase_price=row.get("purchase_price") or it.purchase_price,
                            sale_price=row.get("sale_price") or it.sale_price,
                        )
                    grn.post()

                self.message_user(request, f"Posted GRN {grn.grn_no} with {total_rows} line(s).", level=messages.SUCCESS)
                return redirect(reverse("admin:purchase_purchaseinvoice_change", args=[inv.pk]))
        else:
            initial = []
            for it in pi_items:
                initial.append({
                    "invoice_item_id": it.id,
                    "product_label": str(it.product),
                    "outstanding": outstanding[it.id],
                    "receive_qty": outstanding[it.id],  # default to full outstanding; user may reduce
                    "batch_number": it.batch_number,
                    "expiry_date": it.expiry_date,
                    "purchase_price": it.purchase_price,
                    "sale_price": it.sale_price,
                })
            formset = GRNLineFormSet(initial=initial)

        ctx = {
            **self.admin_site.each_context(request),
            "title": "Partial Receive — Goods Receipt",
            "opts": self.model._meta,
            "inv": inv,
            "formset": formset,
        }
        return render(request, "admin/purchase/purchaseinvoice/receive_partial.html", ctx)
    def settle_breakdown_view(self, request):
        inv_id = request.GET.get("id") or request.POST.get("id")
        try:
            inv = (
                self.get_queryset(request)
                .select_related("supplier", "warehouse")
                .get(pk=inv_id)
            )
        except Exception:
            self.message_user(request, "Invalid selection.", level=messages.ERROR)
            return redirect("admin:purchase_purchaseinvoice_changelist")

        if inv.status not in {"CONFIRMED", "RECEIVED"}:
            self.message_user(
                request, "Only CONFIRMED or RECEIVED invoices can be settled.",
                level=messages.ERROR,
            )
            return redirect("admin:purchase_purchaseinvoice_changelist")

        outstanding = Decimal(inv.outstanding or 0)
        if outstanding <= 0:
            self.message_user(request, "Nothing outstanding to settle.", level=messages.WARNING)
            return redirect("admin:purchase_purchaseinvoice_changelist")

        if request.method == "POST":
            form = PaymentCreditBreakdownForm(request.POST, outstanding=outstanding)
            if form.is_valid():
                pay_amt = form.cleaned_data["pay_amount"]
                cred_amt = form.cleaned_data["credit_amount"]
                note = form.cleaned_data.get("note", "")

                try:
                    with transaction.atomic():
                        inv.settle_with_breakdown(pay_amount=pay_amt, credit_amount=cred_amt, note=note)
                except Exception as exc:
                    self.message_user(request, f"Error: {exc}", level=messages.ERROR)
                    return redirect("admin:purchase_purchaseinvoice_changelist")

                self.message_user(
                    request,
                    _(f"Settled {inv.invoice_no}: payment {pay_amt}, credit {cred_amt}."),
                    level=messages.SUCCESS,
                )
                return redirect("admin:purchase_purchaseinvoice_changelist")
        else:
            # Prefill 50/50 of OUTSTANDING for convenience
            half = (outstanding / Decimal("2")).quantize(Decimal("0.01"))
            form = PaymentCreditBreakdownForm(
                outstanding=outstanding,
                initial={"pay_amount": half, "credit_amount": outstanding - half},
            )

        context = {
            **self.admin_site.each_context(request),
            "title": _("Settle Purchase Invoice – Enter Payment/Credit"),
            "opts": self.model._meta,
            "inv": inv,
            "inv_id": inv.pk,
            "total": inv.grand_total,
            "cash_paid": inv.paid_amount,
            "credited": inv.credited_amount,
            "outstanding": outstanding,
            "form": form,
        }
        return render(request, "admin/purchase/purchaseinvoice/settle_breakdown.html", context)
    
   
    def obj_confirm_view(self, request, object_id):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        inv = get_object_or_404(PurchaseInvoice, pk=object_id)
        if inv.status != "DRAFT":
            self.message_user(request, "Only DRAFT can be confirmed.", level=messages.ERROR)
        else:
            inv.confirm()
            self.message_user(request, "Confirmed.", level=messages.SUCCESS)
        return redirect(reverse("admin:purchase_purchaseinvoice_change", args=[inv.pk]))
    def save_formset(self, request, form, formset, change):
        # Save items first, then recompute totals
        instances = formset.save(commit=False)
        for obj in instances:
            obj.save()
        # delete removed rows
        for obj in formset.deleted_objects:
            obj.delete()
        formset.save_m2m()

        # Now recompute on the parent
        parent = form.instance
        parent.recalc_totals(save=True)

    # def obj_receive_view(self, request, object_id):
    #     if request.method != "POST":
    #         return HttpResponseNotAllowed(["POST"])
    #     inv = get_object_or_404(PurchaseInvoice, pk=object_id)
    #     if inv.status != "CONFIRMED":
    #         self.message_user(request, "Only CONFIRMED can be received.", level=messages.ERROR)
    #     else:
    #         inv.receive()
    #         self.message_user(request, "Received.", level=messages.SUCCESS)
    #     return redirect(reverse("admin:purchase_purchaseinvoice_change", args=[inv.pk]))

    # Object-level settle breakdown (reuses model.settle_with_breakdown)
    def settle_breakdown_view_obj(self, request, object_id):
        inv = get_object_or_404(
            self.get_queryset(request).select_related("supplier", "warehouse"),
            pk=object_id
        )
        if inv.status not in {"CONFIRMED", "RECEIVED"}:
            self.message_user(request, "Only CONFIRMED/RECEIVED can be settled.", level=messages.ERROR)
            return redirect(reverse("admin:purchase_purchaseinvoice_change", args=[inv.pk]))

        outstanding = Decimal(inv.outstanding or 0)
        if outstanding <= 0:
            self.message_user(request, "Nothing outstanding to settle.", level=messages.WARNING)
            return redirect(reverse("admin:purchase_purchaseinvoice_change", args=[inv.pk]))

        if request.method == "POST":
            form = PaymentCreditBreakdownForm(request.POST, outstanding=outstanding)
            if form.is_valid():
                pay_amt = form.cleaned_data["pay_amount"]
                cred_amt = form.cleaned_data["credit_amount"]
                note = form.cleaned_data.get("note", "")
                try:
                    with transaction.atomic():
                        inv.settle_with_breakdown(pay_amount=pay_amt, credit_amount=cred_amt, note=note)
                    self.message_user(request, f"Settled: payment {pay_amt}, credit {cred_amt}.", level=messages.SUCCESS)
                except Exception as exc:
                    self.message_user(request, f"Error: {exc}", level=messages.ERROR)
                return redirect(reverse("admin:purchase_purchaseinvoice_change", args=[inv.pk]))
        else:
            half = (outstanding / Decimal("2")).quantize(Decimal("0.01"))
            form = PaymentCreditBreakdownForm(outstanding=outstanding, initial={"pay_amount": half, "credit_amount": outstanding - half})

        ctx = {
            **self.admin_site.each_context(request),
            "title": "Settle Purchase Invoice – Enter Payment/Credit",
            "opts": self.model._meta,
            "inv": inv,
            "total": inv.grand_total,
            "cash_paid": inv.paid_amount,
            "credited": inv.credited_amount,
            "outstanding": outstanding,
            "form": form,
        }
        return render(request, "admin/purchase/purchaseinvoice/settle_breakdown.html", ctx)

class PurchaseReturnItemInline(admin.TabularInline):
    model = PurchaseReturnItem
    extra = 1
    fields = ("grn_item","product","batch_number","expiry_date","quantity","purchase_price","sale_price","amount")
    raw_id_fields = ("grn_item","product")
    can_delete = True

@admin.action(description="Confirm selected purchase returns (book credit note)")
def action_confirm(modeladmin, request, queryset):
    qs = queryset.select_related("supplier", "warehouse")
    done, skipped = 0, 0

    for pr in qs:
        try:
            if pr.status != "DRAFT":
                skipped += 1
                continue
            # model will post the confirm entry on status transition
            with transaction.atomic():
                old = pr.status
                pr.status = "CONFIRMED"
                pr.save(update_fields=["status"])  # triggers side-effects in save()
            done += 1
        except Exception as e:
            modeladmin.message_user(
                request, f"#{pr.pk} {pr.return_no}: {e}", level=messages.ERROR
            )

    if done:
        messages.success(request, f"Confirmed {done} purchase return(s).")
    if skipped:
        messages.warning(request, f"Skipped {skipped} (not in DRAFT).")


@admin.action(description="Mark returned (stock-out items)")
def action_mark_returned(modeladmin, request, queryset):
    qs = queryset.prefetch_related("items").select_related("warehouse")
    done, skipped, errors = 0, 0, 0

    for pr in qs:
        try:
            if pr.status != "CONFIRMED":
                skipped += 1
                continue
            if not pr.items.exists():
                errors += 1
                modeladmin.message_user(
                    request,
                    f"#{pr.pk} {pr.return_no}: No items to stock-out.",
                    level=messages.ERROR,
                )
                continue
            with transaction.atomic():
                pr.status = "RETURNED"
                pr.save(update_fields=["status"])  # save() calls do_stock_out()
            done += 1
        except Exception as e:
            errors += 1
            modeladmin.message_user(
                request, f"#{pr.pk} {pr.return_no}: {e}", level=messages.ERROR
            )

    if done:
        messages.success(request, f"Marked RETURNED {done} purchase return(s).")
    if skipped:
        messages.warning(request, f"Skipped {skipped} (only CONFIRMED allowed).")


@admin.action(description="Refund outstanding (cash/bank)")
def action_refund_outstanding(modeladmin, request, queryset):
    qs = queryset.select_related("supplier", "warehouse")
    done, skipped, errors = 0, 0, 0

    for pr in qs:
        try:
            if pr.status not in {"CONFIRMED", "RETURNED"}:
                skipped += 1
                continue

            total = Decimal(pr.total_amount or 0)
            paid = Decimal(pr.refunded_amount or 0)
            outstanding = total - paid
            if outstanding <= 0:
                skipped += 1
                continue

            with transaction.atomic():
                pr.status = "REFUNDED"
                pr.save(update_fields=["status"])  # triggers cash refund posting
            done += 1
        except Exception as e:
            errors += 1
            modeladmin.message_user(
                request, f"#{pr.pk} {pr.return_no}: {e}", level=messages.ERROR
            )

    if done:
        messages.success(request, f"Issued cash refund for {done} purchase return(s).")
    if skipped:
        messages.warning(request, f"Skipped {skipped} (no outstanding or wrong status).")


@admin.action(description="Mark credited (no cash, just A/P reduced)")
def action_mark_credited(modeladmin, request, queryset):
    qs = queryset.select_related("supplier")
    done, skipped, errors = 0, 0, 0

    for pr in qs:
        try:
            if pr.status not in {"CONFIRMED", "RETURNED"}:
                skipped += 1
                continue

            total = Decimal(pr.total_amount or 0)
            paid = Decimal(pr.refunded_amount or 0)
            if paid >= total:
                skipped += 1  # already fully refunded
                continue

            with transaction.atomic():
                pr.status = "CREDITED"
                pr.save(update_fields=["status"])  # will sync payment_status & supplier balance
            done += 1
        except Exception as e:
            errors += 1
            modeladmin.message_user(
                request, f"#{pr.pk} {pr.return_no}: {e}", level=messages.ERROR
            )

    if done:
        messages.success(request, f"Marked CREDITED {done} purchase return(s).")
    if skipped:
        messages.warning(request, f"Skipped {skipped} (wrong status or already settled).")


@admin.action(description="Cancel (only DRAFT)")
def action_cancel(modeladmin, request, queryset):
    qs = queryset
    done, skipped = 0, 0

    for pr in qs:
        if pr.status != "DRAFT":
            skipped += 1
            continue
        with transaction.atomic():
            pr.status = "CANCELLED"
            pr.save(update_fields=["status"])
        done += 1

    if done:
        messages.success(request, f"Cancelled {done} purchase return(s).")
    if skipped:
        messages.warning(request, f"Skipped {skipped} (only DRAFT can be cancelled).")
@admin.action(description="Settle with refund/credit (enter amounts)…")
def action_settle_with_breakdown(modeladmin, request, queryset):
    if queryset.count() != 1:
        modeladmin.message_user(request, "Select exactly one purchase return.", level=messages.ERROR)
        return
    pr = queryset.first()
    if pr.status not in {"CONFIRMED", "RETURNED"}:
        modeladmin.message_user(
            request, "Only CONFIRMED or RETURNED purchase returns can be settled.", level=messages.ERROR
        )
        return
    url = reverse("admin:purchases_purchasereturn_settle_breakdown") + f"?id={pr.pk}"
    return HttpResponseRedirect(url)

@admin.register(PurchaseReturn)
class PurchaseReturnAdmin(admin.ModelAdmin):
    list_display = (
        "return_no", "date", "supplier", "warehouse",
        "total_amount", "status", "payment_status",
        "refunded_amount",
    )
    list_filter = ("status", "payment_status", "warehouse", "date")
    search_fields = ("return_no", "supplier__name")
    autocomplete_fields = ("supplier", "warehouse","invoice")
    change_form_template = "admin/purchase/purchasereturn/change_form.html"
    class Media:
        js = ("purchase/purchase_return.js",)  # we’ll add this file below
    inlines = [PurchaseReturnItemInline]
    actions = [
        action_confirm,
        action_mark_returned,
        action_refund_outstanding,
        action_mark_credited,
        action_cancel,
        action_settle_with_breakdown,
    ]
    def get_urls(self):
        urls = super().get_urls()
        my_urls = [
            path(
                "settle-breakdown/",
                self.admin_site.admin_view(self.settle_breakdown_view),
                name="purchases_purchasereturn_settle_breakdown",
            ),
             # Object-level POST endpoints
            path("<int:object_id>/confirm/",    self.admin_site.admin_view(self.obj_confirm_view),    name="purchases_purchasereturn_confirm"),
            path("<int:object_id>/returned/",   self.admin_site.admin_view(self.obj_mark_returned),   name="purchases_purchasereturn_returned"),
            path("<int:object_id>/refund/",     self.admin_site.admin_view(self.obj_refund_outstanding), name="purchases_purchasereturn_refund"),
            path("<int:object_id>/credited/",   self.admin_site.admin_view(self.obj_mark_credited),   name="purchases_purchasereturn_credited"),
            path("<int:object_id>/cancel/",     self.admin_site.admin_view(self.obj_cancel),          name="purchases_purchasereturn_cancel"),
            # Object-level intermediate (GET form) for refund/credit split
            path("<int:object_id>/settle-breakdown/", self.admin_site.admin_view(self.settle_breakdown_view_obj), name="purchases_purchasereturn_settle_breakdown_obj"),
            path(
                "invoice-data/<int:invoice_id>/",
                self.admin_site.admin_view(self.invoice_data_json),
                name="purchases_purchasereturn_invoice_data",
            ),
            path(
                "invoice-grn-data/<int:invoice_id>/",
                self.admin_site.admin_view(self.invoice_grn_data_json),
                name="purchase_purchasereturn_invoice_grn_data",
            ),
        ]
        return my_urls + urls
    
    # ---- JSON provider ----
    def invoice_grn_data_json(self, request, invoice_id: int):
        inv = get_object_or_404(
            PurchaseInvoice.objects.select_related("supplier","warehouse"),
            pk=invoice_id
        )
        remaining = grn_returnable_map(inv)  # {grn_item_id: remaining}

        # Build from POSTED GRNs only
        grn_rows = (
            GoodsReceiptItem.objects
            .select_related("invoice_item__product", "grn")
            .filter(invoice_item__invoice=inv, grn__status="POSTED")
        )

        items = []
        for gri in grn_rows:
            remain = remaining.get(gri.id, 0)
            if remain <= 0:
                continue
            ii = gri.invoice_item
            items.append({
                "grn_item_id": gri.id,
                "product_id": ii.product_id,
                "product_label": str(ii.product),
                "batch_number": gri.batch_number or ii.batch_number or "",
                "expiry_date": (gri.expiry_date or ii.expiry_date).isoformat() if (gri.expiry_date or ii.expiry_date) else "",
                "purchase_price": str((gri.purchase_price or ii.purchase_price) or 0),
                "sale_price": str((gri.sale_price or ii.sale_price) or 0),
                "max_return_qty": remain,
                "default_qty": remain,  # change to 0 if you prefer
            })

        return JsonResponse({
            "invoice": {
                "id": inv.id,
                "invoice_no": inv.invoice_no,
                "date": inv.date.isoformat(),
            },
            "supplier": {"id": inv.supplier_id, "text": str(inv.supplier)},
            "warehouse": {"id": inv.warehouse_id, "text": str(inv.warehouse)},
            "items": items,
        })

    # ---- ensure totals/validation after inlines ----
    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        obj: PurchaseReturn = form.instance
        obj._recompute_totals_from_items()
        # server guard against over-returns
        obj._validate_against_invoice_returnables()
        obj.save(update_fields=["total_amount"])
    def invoice_data_json(self, request, invoice_id: int):
        try:
            inv = (
                PurchaseInvoice.objects
                .select_related("supplier", "warehouse")
                .prefetch_related("items__product")
                .get(pk=invoice_id)
            )
        except PurchaseInvoice.DoesNotExist:
            raise Http404

        # --- normalize helper for batch keys ---
        def norm_batch(b):  # None and whitespace -> ""
            return (b or "").strip()

        # --- RECEIVED per invoice item (use GRN if present; else fall back to ordered+bonus) ---
        received_map = {}
        try:
            from purchase.models import GoodsReceiptItem
            rec = (
                GoodsReceiptItem.objects
                .filter(invoice_item__invoice=inv, grn__status="POSTED")
                .values("invoice_item_id")
                .annotate(qty=Sum("quantity"))
            )
            received_map = {r["invoice_item_id"]: int(r["qty"] or 0) for r in rec}
        except Exception:
            # If GRN model not available, we'll use ordered qty below.
            pass

        # --- Build (product,batch) -> invoice_item_id map to anchor counts to the line on the invoice ---
        by_key = {}
        for it in inv.items.all():
            by_key[(it.product_id, norm_batch(it.batch_number))] = it.id

        # --- Already returned so far (only counting effective PRs) ---
        COUNT_STATUSES = {"CONFIRMED", "RETURNED", "REFUNDED", "CREDITED"}
        returned_by_item = {}
        ret_rows = (
            PurchaseReturnItem.objects
            .filter(
                return_invoice__invoice=inv,
                return_invoice__status__in=COUNT_STATUSES,
            )
            
            .values("product_id", "batch_number")
            .annotate(qty=Sum("quantity"))
        )
        for r in ret_rows:
            key = (r["product_id"], norm_batch(r["batch_number"]))
            inv_item_id = by_key.get(key)
            if inv_item_id:
                returned_by_item[inv_item_id] = returned_by_item.get(inv_item_id, 0) + int(r["qty"] or 0)

        # --- Build payload per invoice item ---
        items_payload = []
        for it in inv.items.all():
            ordered_plus_bonus = int((it.quantity or 0) + (getattr(it, "bonus", 0) or 0))

            # If GRN exists for this invoice_item -> use posted received qty; else assume fully received
           
            received_qty = received_map.get(it.id, ordered_plus_bonus)
                # If you want to allow returns against ordered even when not received yet, use:
                # received_qty = max(received_qty, ordered_plus_bonus)
           

            already_ret = int(returned_by_item.get(it.id, 0))
            returnable = max(received_qty - already_ret, 0)

            if returnable <= 0:
                continue

            items_payload.append({
                "invoice_item_id": it.id,
                "product_id": it.product_id,
                "product_label": str(it.product),
                "batch_number": it.batch_number,
                "expiry_date": it.expiry_date.isoformat() if it.expiry_date else "",
                "purchase_price": str(it.purchase_price or 0),
                "sale_price": str(it.sale_price or 0),
                "max_return_qty": returnable,
                "default_qty": returnable,  # change to 0 if you prefer
            })

        payload = {
            "invoice": {
                "id": inv.id,
                "invoice_no": inv.invoice_no,
                "date": date_format(inv.date, "Y-m-d"),
                "total_amount": str(inv.total_amount or 0),
                "discount": str(inv.discount or 0),
                "tax": str(inv.tax or 0),
                "grand_total": str(inv.grand_total or 0),
            },
            "supplier": {"id": inv.supplier_id, "text": str(inv.supplier)},
            "warehouse": {"id": inv.warehouse_id, "text": str(inv.warehouse)},
            "items": items_payload,
        }

        # Optional debug to help you verify quantities quickly
        if request.GET.get("debug") == "1":
            debug_rows = []
            for it in inv.items.all():
                ordered_plus_bonus = int((it.quantity or 0) + (getattr(it, "bonus", 0) or 0))
                recv = received_map.get(it.id, ordered_plus_bonus if not received_map else 0)
                ret = int(returned_by_item.get(it.id, 0))
                debug_rows.append({
                    "invoice_item_id": it.id,
                    "product": str(it.product),
                    "ordered+bonus": ordered_plus_bonus,
                    "received": recv,
                    "already_returned": ret,
                    "returnable": max((recv if received_map else ordered_plus_bonus) - ret, 0),
                })
            payload["_debug"] = debug_rows

        return JsonResponse(payload)
    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("supplier", "warehouse")
            .prefetch_related("items")
        )
    def settle_breakdown_view(self, request):
        pr_id = request.GET.get("id") or request.POST.get("id")
        try:
            pr = (
                self.get_queryset(request)
                .select_related("supplier", "warehouse")
                .get(pk=pr_id)
            )
        except Exception:
            self.message_user(request, "Invalid selection.", level=messages.ERROR)
            return redirect("admin:purchase_purchasereturn_changelist")

        if pr.status not in {"CONFIRMED", "RETURNED"}:
            self.message_user(
                request, "Only CONFIRMED or RETURNED purchase returns can be settled.",
                level=messages.ERROR,
            )
            return redirect("admin:purchase_purchasereturn_changelist")

        total = Decimal(pr.total_amount or 0)
        already = Decimal(pr.refunded_amount or 0)
        outstanding = (total - already)
        if outstanding <= 0:
            self.message_user(request, "Nothing outstanding to settle.", level=messages.WARNING)
            return redirect("admin:purchase_purchasereturn_changelist")

        if request.method == "POST":
            form = RefundCreditBreakdownForm(request.POST, outstanding=outstanding)
            if form.is_valid():
                refund = form.cleaned_data["refund_amount"]
                credit = form.cleaned_data["credit_amount"]
                note = form.cleaned_data.get("note", "")

                try:
                    with transaction.atomic():
                        # 1) Partial cash refund (if any)
                        if refund > 0:
                            pr.post_cash_refund_amount(refund, note=note)

                        # 2) Decide terminal status
                        #    - If any credit remains, we finalize as CREDITED (fully settled via confirm journal + cash part).
                        #    - Else (only cash), mark REFUNDED.
                        if credit > 0:
                            pr.status = "CREDITED"
                            # Keep your existing convention: treat as fully settled for payment_status
                            pr.refunded_amount = pr._base_total()
                        else:
                            pr.status = "REFUNDED"

                        # Sync payment status and persist
                        pr._sync_payment_status()
                        pr.save(update_fields=["status", "payment_status", "refunded_amount", "refund_txn_id"])
                except Exception as exc:
                    self.message_user(request, f"Error: {exc}", level=messages.ERROR)
                    return redirect("admin:purchase_purchasereturn_changelist")

                self.message_user(
                    request,
                    _(f"Settled {pr.return_no}: refund {refund}, credit {credit}."),
                    level=messages.SUCCESS,
                )
                return redirect("admin:purchase_purchasereturn_changelist")
        else:
            # Prefill 50/50 of the OUTSTANDING (not total)
            half = (outstanding / Decimal("2")).quantize(Decimal("0.01"))
            form = RefundCreditBreakdownForm(
                outstanding=outstanding,
                initial={"refund_amount": half, "credit_amount": outstanding - half},
            )

        context = {
            **self.admin_site.each_context(request),
            "title": _("Settle Purchase Return – Enter Refund/Credit"),
            "opts": self.model._meta,
            "pr": pr,
            "pr_id": pr.pk,
            "total": total,
            "already": already,
            "outstanding": outstanding,
            "form": form,
        }
        return render(request, "admin/purchase/purchasereturn/settle_breakdown.html", context)
    # ---------- Single-object views (reuse your existing logic) ----------
 
    def obj_confirm_view(self, request, object_id):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        pr = get_object_or_404(PurchaseReturn, pk=object_id)
        if pr.status != "DRAFT":
            self.message_user(request, "Only DRAFT can be confirmed.", level=messages.ERROR)
        else:
            with transaction.atomic():
                pr.status = "CONFIRMED"
                pr.save(update_fields=["status"])  # triggers postings in save()
            self.message_user(request, "Confirmed.", level=messages.SUCCESS)
        return redirect(reverse("admin:purchase_purchasereturn_change", args=[pr.pk]))

  
    def obj_mark_returned(self, request, object_id):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        pr = get_object_or_404(PurchaseReturn, pk=object_id)
        if pr.status != "CONFIRMED":
            self.message_user(request, "Only CONFIRMED can be marked RETURNED.", level=messages.ERROR)
        elif not pr.items.exists():
            self.message_user(request, "No items to stock-out.", level=messages.ERROR)
        else:
            with transaction.atomic():
                pr.status = "RETURNED"
                pr.save(update_fields=["status"])  # will stock out
            self.message_user(request, "Marked RETURNED.", level=messages.SUCCESS)
        return redirect(reverse("admin:purchase_purchasereturn_change", args=[pr.pk]))

    
    def obj_refund_outstanding(self, request, object_id):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        pr = get_object_or_404(PurchaseReturn, pk=object_id)
        if pr.status not in {"CONFIRMED", "RETURNED"}:
            self.message_user(request, "Only CONFIRMED/RETURNED can be refunded.", level=messages.ERROR)
        else:
            total = Decimal(pr.total_amount or 0)
            paid = Decimal(pr.refunded_amount or 0)
            if total - paid <= 0:
                self.message_user(request, "Nothing outstanding to refund.", level=messages.WARNING)
            else:
                with transaction.atomic():
                    pr.status = "REFUNDED"
                    pr.save(update_fields=["status"])  # triggers cash refund posting
                self.message_user(request, "Refunded outstanding.", level=messages.SUCCESS)
        return redirect(reverse("admin:purchase_purchasereturn_change", args=[pr.pk]))

    
    def obj_mark_credited(self, request, object_id):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        pr = get_object_or_404(PurchaseReturn, pk=object_id)
        if pr.status not in {"CONFIRMED", "RETURNED"}:
            self.message_user(request, "Only CONFIRMED/RETURNED can be credited.", level=messages.ERROR)
        else:
            total = Decimal(pr.total_amount or 0)
            already = Decimal(pr.refunded_amount or 0)
            if already >= total:
                self.message_user(request, "Already fully settled.", level=messages.WARNING)
            else:
                with transaction.atomic():
                    pr.status = "CREDITED"
                    pr.save(update_fields=["status"])
                self.message_user(request, "Marked CREDITED.", level=messages.SUCCESS)
        return redirect(reverse("admin:purchase_purchasereturn_change", args=[pr.pk]))

  
    def obj_cancel(self, request, object_id):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        pr = get_object_or_404(PurchaseReturn, pk=object_id)
        if pr.status != "DRAFT":
            self.message_user(request, "Only DRAFT can be cancelled.", level=messages.ERROR)
        else:
            with transaction.atomic():
                pr.status = "CANCELLED"
                pr.save(update_fields=["status"])
            self.message_user(request, "Cancelled.", level=messages.SUCCESS)
        return redirect(reverse("admin:purchase_purchasereturn_change", args=[pr.pk]))

    # ---------- Object-level settle breakdown (intermediate form) ----------
    def settle_breakdown_view_obj(self, request, object_id):
       
        pr = get_object_or_404(
            self.get_queryset(request).select_related("supplier", "warehouse"),
            pk=object_id
        )

        if pr.status not in {"CONFIRMED", "RETURNED"}:
            self.message_user(request, "Only CONFIRMED/RETURNED can be settled.", level=messages.ERROR)
            return redirect(reverse("admin:purchase_purchasereturn_change", args=[pr.pk]))

        total = Decimal(pr.total_amount or 0)
        already = Decimal(pr.refunded_amount or 0)
        outstanding = total - already
        if outstanding <= 0:
            self.message_user(request, "Nothing outstanding to settle.", level=messages.WARNING)
            return redirect(reverse("admin:purchase_purchasereturn_change", args=[pr.pk]))

        if request.method == "POST":
            form = RefundCreditBreakdownForm(request.POST, outstanding=outstanding)
            if form.is_valid():
                refund = form.cleaned_data["refund_amount"]
                credit = form.cleaned_data["credit_amount"]
                note = form.cleaned_data.get("note", "")

                try:
                    with transaction.atomic():
                        if refund > 0:
                            pr.post_cash_refund_amount(refund, note=note)
                        if credit > 0:
                            pr.status = "CREDITED"
                            pr.refunded_amount = pr._base_total()
                        else:
                            pr.status = "REFUNDED"
                        pr._sync_payment_status()
                        pr.save(update_fields=["status", "payment_status", "refunded_amount", "refund_txn_id"])
                    self.message_user(request, f"Settled: refund {refund}, credit {credit}.", level=messages.SUCCESS)
                except Exception as exc:
                    self.message_user(request, f"Error: {exc}", level=messages.ERROR)
                return redirect(reverse("admin:purchase_purchasereturn_change", args=[pr.pk]))
        else:
            half = (outstanding / Decimal("2")).quantize(Decimal("0.01"))
            form = RefundCreditBreakdownForm(outstanding=outstanding, initial={"refund_amount": half, "credit_amount": outstanding - half})

        ctx = {
            **self.admin_site.each_context(request),
            "title": "Settle Purchase Return – Enter Refund/Credit",
            "opts": self.model._meta,
            "pr": pr,
            "total": total,
            "already": already,
            "outstanding": outstanding,
            "form": form,
        }
        return render(request, "admin/purchase/purchasereturn/settle_breakdown.html", ctx)
class InvestorTransactionAdmin(admin.ModelAdmin):
    list_display = ("investor", "transaction_type", "amount", "date", "purchase_invoice")
    list_filter = ("transaction_type", "date")
    search_fields = ("investor__name", "notes")


admin.site.register(InvestorTransaction, InvestorTransactionAdmin)
