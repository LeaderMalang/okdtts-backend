from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Order
from .serializers import OrderSerializer
from sale.serializers import SaleInvoiceSerializer
from setting.models import Warehouse


class OrderViewSet(viewsets.ModelViewSet):
    lookup_field = "pk"
    lookup_value_regex = r"\d+"  # ensure /orders/status/ can't be misrouted as a pk
    queryset = Order.objects.all().prefetch_related("items")
    serializer_class = OrderSerializer
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
                Q(order_no__icontains=search) |
                Q(customer__name__icontains=search)
            )

        return qs
    @action(
        detail=False,
        methods=["get"],
        url_path="customer/(?P<customer_id>[^/.]+)",
    )
    def list_by_customer(self, request, customer_id=None):
        queryset = self.queryset.filter(customer_id=customer_id)

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)


        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)
    

    @action(
        detail=False,
        methods=["get"],
        url_path="salesman/(?P<salesman_id>[^/.]+)/customer/(?P<customer_id>[^/.]+)",
    )
    def list_by_salesman(self, request, salesman_id=None,customer_id=None):
        queryset = self.queryset.filter(salesman_id=salesman_id,customer_id=customer_id)

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)


        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        order = self.get_object()
        warehouse = Warehouse.objects.get(pk=request.data.get("warehouse"))
        payment_method = request.data.get("payment_method")
        payment_terms = request.data.get("payment_terms")
        invoice = order.confirm(warehouse, payment_method,payment_terms)
        serializer = SaleInvoiceSerializer(invoice)
        return Response(serializer.data)
    @action(detail=True, methods=["patch"], url_path="status")
    def set_status(self, request, pk=None):
        """
        Update a single order's status by ID.
        Body: {"status": "<new_status>", "note": "optional note"}
        """
        order = self.get_object()
        new_status = (request.data.get("status") or "").strip()
        if not new_status:
            return Response({"detail": "status is required"}, status=http_status.HTTP_400_BAD_REQUEST)

        # Validate against model choices if present
        if hasattr(Order, "STATUS_CHOICES") and Order.STATUS_CHOICES:
            # Accept case-insensitive input
            allowed = {k.upper(): k for k, _ in Order.STATUS_CHOICES}
            if new_status.upper() not in allowed:
                return Response(
                    {"detail": f"Invalid status. Allowed: {', '.join(allowed.values())}"},
                    status=http_status.HTTP_400_BAD_REQUEST,
                )
            new_status = allowed[new_status.upper()]

        # Persist
        fields_to_update = ["status"]
        order.status = new_status

        # # Optional note/audit, only if your model has such field(s)
        # note = request.data.get("note")
        # if hasattr(order, "status_note") and note:
        #     order.status_note = str(note)
        #     fields_to_update.append("status_note")
        # if hasattr(order, "status_changed_at"):
        #     from django.utils import timezone
        #     order.status_changed_at = timezone.now()
        #     fields_to_update.append("status_changed_at")

        order.save(update_fields=fields_to_update)
        return Response(self.get_serializer(order).data)