from rest_framework import permissions, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from .models import City, Area, Company, Group, Distributor, Branch, Warehouse
from .serializers import (
    CitySerializer,
    AreaSerializer,
    CompanySerializer,
    GroupSerializer,
    DistributorSerializer,
    BranchSerializer,
    WarehouseSerializer,
)
from inventory.models import Party, Product, Batch, PriceList, PriceListItem
from expense.models import ExpenseCategory
from voucher.models import ChartOfAccount
from rest_framework.decorators import action




class CityViewSet(viewsets.ModelViewSet):
    queryset = City.objects.all()
    serializer_class = CitySerializer
    permission_classes = [permissions.IsAuthenticated]
    @action(detail=True, methods=["get"], url_path="areas")
    def areas(self, request, pk=None):
        qs = Area.objects.filter(city_id=pk).order_by("name")
        data = AreaSerializer(qs, many=True).data
        return Response(data)


class AreaViewSet(viewsets.ModelViewSet):
    queryset = Area.objects.all()
    serializer_class = AreaSerializer
    permission_classes = [permissions.IsAuthenticated]

    # def get_queryset(self):
    #     qs = Area.objects.all()
    #     city_id = self.request.query_params.get("city") or self.request.query_params.get("cityId")
    #     if city_id:
    #         qs = qs.filter(city_id=city_id)
    #     return qs


class CompanyViewSet(viewsets.ModelViewSet):
    queryset = Company.objects.all()
    serializer_class = CompanySerializer
    permission_classes = [permissions.IsAuthenticated]


class GroupViewSet(viewsets.ModelViewSet):
    queryset = Group.objects.all()
    serializer_class = GroupSerializer
    permission_classes = [permissions.IsAuthenticated]


class DistributorViewSet(viewsets.ModelViewSet):
    queryset = Distributor.objects.all()
    serializer_class = DistributorSerializer
    permission_classes = [permissions.IsAuthenticated]


class BranchViewSet(viewsets.ModelViewSet):
    queryset = Branch.objects.all()
    serializer_class = BranchSerializer
    permission_classes = [permissions.IsAuthenticated]


class WarehouseViewSet(viewsets.ModelViewSet):
    queryset = Warehouse.objects.all()
    serializer_class = WarehouseSerializer
    permission_classes = [permissions.IsAuthenticated]


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def management_all(request):
    data = {
        "companies": list(Company.objects.values()),
        "groups": list(Group.objects.values()),
        "distributors": list(Distributor.objects.values()),
        "cities": list(City.objects.values()),
        "areas": list(Area.objects.values()),
        "parties": list(Party.objects.values()),
        "products": list(Product.objects.values()),
        "batches": list(Batch.objects.values()),
        "expense_categories": list(ExpenseCategory.objects.values()),
        "price_lists": list(PriceList.objects.values()),
        "price_list_items": list(PriceListItem.objects.values()),
        "chart_of_accounts": list(ChartOfAccount.objects.values()),
    }
    return Response(data)
