from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.contrib import messages
from django.views.decorators.http import require_http_methods

from rest_framework import viewsets,status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from django.db.models import Q,Sum,F

from rest_framework.response import Response
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema

from datetime import date
from decimal import Decimal, InvalidOperation
from django.db import transaction
from finance.models_receipts import CustomerReceipt
from utils.notifications import notify_user_and_party


from .models import (
    SaleInvoice,
    SaleInvoiceItem,
    SaleReturn,
    SaleReturnItem,
    RecoveryLog,
)
from .forms import SaleInvoiceForm, SaleInvoiceItemForm
from .serializers import (
    SaleInvoiceSerializer,
    SaleReturnSerializer,
    SaleReturnItemSerializer,
    RecoveryLogSerializer,
)
from sale.api.serializers import (
    SaleInvoiceWriteSerializer, SaleInvoiceReadSerializer,
    ConfirmSerializer, DeliverPayloadSerializer, PaymentSerializer
)
from utils.stock import stock_out  # your existing helper
from finance.hordak_posting import post_sale, post_customer_receipt

@require_http_methods(["GET"])
def sale_invoice_list(request):
    sales = (
        SaleInvoice.objects.select_related(
            'customer',
            'salesman',
            'booking_man_id',
            'supplying_man_id',
            'delivery_man_id',
            'city_id',
            'area_id',
        ).all()
    )
    return render(request, 'invoice/sale_list.html', {'sales': sales})

@require_http_methods(["GET", "POST"])
def sale_invoice_create(request):
    if request.method == 'POST':
        sale=SaleInvoice()
        form = SaleInvoiceForm(request.POST,instance=sale)
        formset = SaleInvoiceItemForm(request.POST,instance=sale)
        if form.is_valid() and formset.is_valid():
            sale = form.save()
            formset.instance = sale
            formset.save()
            Notification.objects.create(
                user=request.user,
                title="Sale Invoice Created",
                message=f"Sale invoice {sale.invoice_no} was created."
            )
            messages.success(request, "Sale invoice created.")
            return redirect(reverse('sale_detail', args=[sale.pk]))
    else:
        sale=SaleInvoice()
        form = SaleInvoiceForm(instance=sale)
        formset = SaleInvoiceItemForm(instance=sale)
        
    return render(request, 'invoice/sale_form.html', {'form': form, 'formset': formset})

@require_http_methods(["GET", "POST"])
def sale_invoice_edit(request, pk):
    sale = get_object_or_404(SaleInvoice, pk=pk)
    if request.method == 'POST':
        form = SaleInvoiceForm(request.POST, instance=sale)
        formset = SaleInvoiceItemForm(request.POST, instance=sale)
        if form.is_valid() and formset.is_valid():
            form.save()
            formset.save()
            messages.success(request, "Sale invoice updated.")
            return redirect(reverse('sale_detail', args=[sale.pk]))
    else:
        form = SaleInvoiceForm(instance=sale)
        formset = SaleInvoiceItemForm(instance=sale)
    return render(request, 'invoice/sale_form.html', {'form': form, 'formset': formset})

@require_http_methods(["GET"])
def sale_invoice_detail(request, pk):
    invoice = get_object_or_404(
        SaleInvoice.objects.select_related(
            'customer',
            'salesman',
            'booking_man_id',
            'supplying_man_id',
            'delivery_man_id',
            'city_id',
            'area_id',
        ),
        pk=pk,
    )
    return render(request, 'invoice/sale_detail.html', {'sale': invoice})


class SaleInvoiceViewSet(viewsets.ModelViewSet):
    queryset = SaleInvoice.objects.all().prefetch_related('items', 'recovery_logs')
    serializer_class = SaleInvoiceSerializer

    def perform_create(self, serializer):
        invoice = serializer.save()
        Notification.objects.create(
            user=self.request.user,
            title="Sale Invoice Created",
            message=f"Sale invoice {invoice.invoice_no} was created."
        )

    def get_queryset(self):
        qs = super().get_queryset()
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)

        start_date = self.request.query_params.get("startDate")
        if start_date:
            qs = qs.filter(date__gte=start_date)

        end_date = self.request.query_params.get("endDate")
        if end_date:
            qs = qs.filter(date__lte=end_date)

        search = self.request.query_params.get("searchTerm")
        if search:
            qs = qs.filter(
                Q(invoice_no__icontains=search) |
                Q(customer__name__icontains=search)
            )

        return qs

    def perform_create(self, serializer):
        invoice = serializer.save()
        notify_user_and_party(
            user=self.request.user,
            party=invoice.customer,
            title="Sale Invoice Created",
            message=f"Sale invoice {invoice.invoice_no} created.",
        )

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "status",
                OpenApiTypes.STR,
                OpenApiParameter.QUERY,
                description="Filter invoices by status",
                required=False,
            ),
            OpenApiParameter(
                "startDate",
                OpenApiTypes.DATE,
                OpenApiParameter.QUERY,
                description="Filter invoices created on or after this date",
                required=False,
            ),
            OpenApiParameter(
                "endDate",
                OpenApiTypes.DATE,
                OpenApiParameter.QUERY,
                description="Filter invoices created on or before this date",
                required=False,
            ),
            OpenApiParameter(
                "searchTerm",
                OpenApiTypes.STR,
                OpenApiParameter.QUERY,
                description="Search by invoice number or customer name",
                required=False,
            ),
        ]
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @action(detail=False, methods=["get"], url_path="by-number/(?P<invoice_no>[^/.]+)")
    def retrieve_by_number(self, request, invoice_no=None):
        """Retrieve a sale invoice using its invoice_no."""
        invoice = get_object_or_404(
            SaleInvoice.objects.all().prefetch_related("items", "recovery_logs"),
            invoice_no=invoice_no,
        )
        serializer = self.get_serializer(invoice)
        return Response(serializer.data)

    @action(detail=True, methods=["patch"], url_path="status")
    def status(self, request, pk=None):
        """Update invoice status and optional delivery man."""
        invoice = self.get_object()
        new_status = request.data.get("status")
        if not new_status:
            return Response({"detail": "status is required."}, status=status.HTTP_400_BAD_REQUEST)

        invoice.status = new_status
        update_fields = ["status"]

        if "delivery_man_id" in request.data:
            invoice.delivery_man_id_id = request.data.get("delivery_man_id")
            update_fields.append("delivery_man_id")

        invoice.save(update_fields=update_fields)
        serializer = self.get_serializer(invoice)
        return Response(serializer.data)


class SaleReturnViewSet(viewsets.ModelViewSet):
    queryset = SaleReturn.objects.all().prefetch_related('items')
    serializer_class = SaleReturnSerializer

    def perform_create(self, serializer):
        sale_return = serializer.save()

        notify_user_and_party(
            user=self.request.user,
            party=sale_return.customer,
            title="Sale Return Created",
            message=f"Sale return {sale_return.return_no} created.",

        )


class SaleReturnItemViewSet(viewsets.ModelViewSet):
    queryset = SaleReturnItem.objects.all()
    serializer_class = SaleReturnItemSerializer


class RecoveryLogViewSet(viewsets.ModelViewSet):
    queryset = RecoveryLog.objects.all()
    serializer_class = RecoveryLogSerializer


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def add_recovery_payment(request, order_id):
    """Append a payment to the recovery log and update the invoice's paid amount."""
    invoice = get_object_or_404(SaleInvoice, pk=order_id)

    amount = request.data.get("amount")
    notes = request.data.get("notes", "")
    try:
        amount = Decimal(str(amount))
    except (TypeError, InvalidOperation):
        return Response({"detail": "Invalid amount."}, status=400)

    employee = getattr(request.user, "employee", None)
    if hasattr(employee, "first"):
        employee = employee.first()

    RecoveryLog.objects.create(
        invoice=invoice,
        employee=employee,
        date=date.today(),
        notes=notes or f"Payment received: {amount}",
    )

    invoice.paid_amount = (invoice.paid_amount or Decimal("0")) + amount
    if invoice.paid_amount >= invoice.grand_total:
        invoice.status = "Paid"
    invoice.save(update_fields=["paid_amount", "status"])

    serializer = SaleInvoiceSerializer(invoice)
    return Response(serializer.data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def add_recovery_note(request, order_id):
    """Append a recovery note to the log for an invoice."""
    invoice = get_object_or_404(SaleInvoice, pk=order_id)
    notes = request.data.get("notes", "")

    employee = getattr(request.user, "employee", None)
    if hasattr(employee, "first"):
        employee = employee.first()

    RecoveryLog.objects.create(
        invoice=invoice,
        employee=employee,
        date=date.today(),
        notes=notes,
    )

    serializer = SaleInvoiceSerializer(invoice)
    return Response(serializer.data)




COUNT_SR_STATUSES = {"CONFIRMED", "RETURNED", "REFUNDED", "CREDITED"}

class SaleInvoiceViewSetLatest(viewsets.ModelViewSet):
    """
    Flow:
      - create (DRAFT)
      - POST /{id}/confirm
      - POST /{id}/deliver (partial deliveries allowed)
      - POST /{id}/pay (cash/bank receipts)
      - GET  /{id}/returnable (for SR create assistant)
    """
    queryset = SaleInvoice.objects.all().select_related("customer", "warehouse").prefetch_related("items__product", "items__batch")

    def get_serializer_class(self):
        if self.action in {"create", "update", "partial_update"}:
            return SaleInvoiceWriteSerializer
        return SaleInvoiceReadSerializer

    # ---------- Confirm ----------
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def confirm(self, request, pk=None):
        inv = self.get_object()
        if inv.status != "DRAFT":
            return Response({"detail": "Only DRAFT can be confirmed."}, status=400)

        # Post composite sale (paid part + A/R)
        post_sale(
            date=inv.date,
            description=f"Sale Invoice {inv.invoice_no}",
            subtotal=Decimal(inv.total_amount or 0) - Decimal(inv.discount or 0),
            tax=Decimal(inv.tax or 0),
            customer_account=inv.customer.chart_of_account,
            warehouse_sales_account=inv.warehouse.default_sales_account,
            paid_amount=Decimal(inv.paid_amount or 0),
            warehouse=inv.warehouse,
        )

        inv.status = "CONFIRMED"
        # sync payment status
        if Decimal(inv.paid_amount or 0) >= Decimal(inv.grand_total or 0):
            inv.payment_status = "PAID"
        elif Decimal(inv.paid_amount or 0) > 0:
            inv.payment_status = "PARTIAL"
        else:
            inv.payment_status = "UNPAID"
        inv.save(update_fields=["status", "payment_status"])

        return Response(SaleInvoiceReadSerializer(inv).data)

    # ---------- Deliver (partial) ----------
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def deliver(self, request, pk=None):
        inv = self.get_object()
        if inv.status not in {"CONFIRMED", "DELIVERED"}:
            return Response({"detail": "Deliveries allowed only after CONFIRMED."}, status=400)

        payload = DeliverPayloadSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        # map invoice items for quick access
        items = {it.id: it for it in inv.items.select_for_update()}  # lock rows to serialize deliveries

        # validate lines
        for ln in data["lines"]:
            it = items.get(ln["invoice_item_id"])
            if not it:
                return Response({"detail": f"Invoice item {ln['invoice_item_id']} not found."}, status=400)
            if ln["quantity"] > it.remaining_to_deliver:
                return Response({"detail": f"Qty {ln['quantity']} exceeds remaining {it.remaining_to_deliver} for item {it.id}."}, status=400)

        # perform stock-out + update delivered_qty
        for ln in data["lines"]:
            it = items[ln["invoice_item_id"]]
            qty = int(ln["quantity"])
            stock_out(
                product=it.product,
                quantity=qty,
                reason=f"Sale Delivery {inv.invoice_no}",
                batch=it.batch,           # if your stock_out supports batch
                warehouse=inv.warehouse,  # if your helper expects this
            )
            it.delivered_qty = int(it.delivered_qty or 0) + qty
            it.save(update_fields=["delivered_qty"])

        # if all items fully delivered → mark invoice DELIVERED
        if all(i.remaining_to_deliver == 0 for i in items.values()):
            inv.status = "DELIVERED"
            inv.save(update_fields=["status"])

        return Response(SaleInvoiceReadSerializer(inv).data, status=200)

    # ---------- Record a payment (cash/bank) ----------
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def pay(self, request, pk=None):
        inv = self.get_object()
        ser = PaymentSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        amount = Decimal(ser.validated_data["amount"])
        if amount <= 0 or amount > inv.outstanding:
            return Response({"detail": "Invalid amount."}, status=400)

        rcpt = CustomerReceipt.objects.create(
            date=ser.validated_data["date"],
            customer=inv.customer,
            warehouse=inv.warehouse,
            amount=amount,
            description=ser.validated_data.get("description") or f"Receipt for {inv.invoice_no}",
        )
        rcpt.post()                # GL: DR cash, CR A/R; Party.current_balance -= amount
        rcpt.allocate(inv, amount) # Non-posting: increase invoice.paid_amount + status

        return Response(SaleInvoiceReadSerializer(inv).data, status=200)

    # ---------- Cancel (only DRAFT) ----------
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def cancel(self, request, pk=None):
        inv = self.get_object()
        if inv.status != "DRAFT":
            return Response({"detail": "Only DRAFT can be cancelled."}, status=400)
        inv.status = "CANCELLED"
        inv.save(update_fields=["status"])
        return Response(SaleInvoiceReadSerializer(inv).data)

    # ---------- Returnable (for Sale Return creation UI) ----------
    @action(detail=True, methods=["get"])
    def returnable(self, request, pk=None):
        """
        Returnable qty per invoice item = delivered_qty - already_returned_qty
        (only counting SR in effective statuses).
        """
        inv = self.get_object()

        # already returned per (invoice_item)
        from sale.models import SaleReturnItem, SaleReturn  # adjust if app labels differ
        ret_map = (
            SaleReturnItem.objects
            .filter(return_invoice__invoice=inv, return_invoice__status__in=COUNT_SR_STATUSES)
            .values("invoice_item_id")
            .annotate(qty=Sum("quantity"))
        )
        already = {row["invoice_item_id"]: int(row["qty"] or 0) for row in ret_map}

        items_payload = []
        for it in inv.items.all():
            delivered = int(it.delivered_qty or 0)
            returned  = already.get(it.id, 0)
            returnable = max(delivered - returned, 0)
            if returnable <= 0:
                continue
            items_payload.append({
                "invoice_item_id": it.id,
                "product_id": it.product_id,
                "product_name": str(it.product),
                "batch_id": it.batch_id,
                "batch_number": getattr(it.batch, "batch_number", ""),
                "expiry_date": getattr(it.batch, "expiry_date", None),
                "rate": str(it.rate or 0),
                "max_return_qty": returnable,
                "default_qty": returnable,
            })
        return Response({"items": items_payload})