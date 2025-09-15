from django.shortcuts import get_object_or_404
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.db.models import Sum,Q
from .models import PriceList, Batch, Product, Party
from .mypagination import MyCustomPagination
from rest_framework.decorators import api_view
from rest_framework.response import Response


@api_view(["GET"])
def price_list_list(request):
    data = list(PriceList.objects.values('id', 'name', 'description'))
    return Response({'price_lists': data})


@api_view(["GET"])
def price_list_detail(request, pk):
    price_list = get_object_or_404(PriceList, pk=pk)
    items = price_list.items.select_related('product').values(
        'product__id', 'product__name', 'custom_price'
    )
    data = {
        'id': price_list.id,
        'name': price_list.name,
        'description': price_list.description,
        'items': list(items)
    }
    return Response(data)


@api_view(["GET"])
def inventory_levels(request):
    """Return aggregated stock levels per product."""
    levels = (
        Batch.objects.values("product__id", "product__name")
        .annotate(total_stock=Sum("quantity"))
        .order_by("product__id")
    )
    data = [
        {
            "product": {"id": item["product__id"], "name": item["product__name"]},
            "totalStock": item["total_stock"] or 0,
        }
        for item in levels
    ]
    return Response({"levels": data})


@api_view(["GET"])
def product_list(request):
    """Return all products with camelCase keys."""
    q = (request.GET.get("q") or "").strip()
    qs = (Product.objects.order_by("name"))
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(barcode__icontains=q))

    paginator = MyCustomPagination()
    page = paginator.paginate_queryset(qs, request)
    data = [
        {
            "id": p.id, "name": p.name, "barcode": p.barcode,
            "stock": p.stock or 0, "tradePrice": float(p.trade_price),
            "retailPrice": float(p.retail_price),"image_1":p.image_1.url if p.image_1 else None,
            "packing": p.packing,
            "image_2":p.image_2.url if p.image_2 else None,
            "salesTaxRatio": float(p.sales_tax_ratio),
            "fedTaxRatio": float(p.fed_tax_ratio),
            "disableSalePurchase": p.disable_sale_purchase,
        } for p in page
    ]
    return paginator.get_paginated_response(data)


@api_view(["GET"])
def party_list(request):
    """
    Query params:
      q=...                  (search in name, phone, proprietor)
      partyType=customer     (or supplier, etc. case-insensitive)
      cityId=123
      areaId=456
      page=1, page_size=25   (or whatever your MyCustomPagination uses)
    """
    q = (request.GET.get("q") or "").strip()
    party_type = (request.GET.get("partyType") or "").strip()
    city_id = (request.GET.get("cityId") or "").strip()
    area_id = (request.GET.get("areaId") or "").strip()

    qs = Party.objects.all().order_by("name")

    if party_type:
        qs = qs.filter(party_type__iexact=party_type)

    if city_id:
        # If city is an FK id, filtering on *_id is fastest.
        qs = qs.filter(city_id=city_id)

    if area_id:
        qs = qs.filter(area_id=area_id)

    if q:
        qs = qs.filter(
            Q(name__icontains=q) |
            Q(phone__icontains=q) |
            Q(proprietor__icontains=q)
        )

    paginator = MyCustomPagination()
    page = paginator.paginate_queryset(qs, request)

    data = [
        {
            "id": p.id,
            "name": p.name,
            "address": p.address,
            "phone": p.phone,
            "partyType": p.party_type,
            "cityId": p.city_id,          # uses FK id directly
            "areaId": p.area_id,          # uses FK id directly
            "proprietor": p.proprietor,
            "licenseNo": p.license_no,
            "licenseExpiry": p.license_expiry,
            "category": p.category,
            "latitude": p.latitude,
            "longitude": p.longitude,
            "creditLimit": float(p.credit_limit) if p.credit_limit is not None else None,
            "currentBalance": float(p.current_balance) if p.current_balance is not None else None,
            "priceListId": int(p.price_list) if p.price_list and str(p.price_list).isdigit() else None,
        }
        for p in page
    ]

    return paginator.get_paginated_response(data)
