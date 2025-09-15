from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.contrib import messages
from django.views.decorators.http import require_http_methods

from rest_framework import viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from django.db.models import Q

from rest_framework.response import Response
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema

from datetime import date
from decimal import Decimal, InvalidOperation


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
