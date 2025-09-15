from rest_framework import viewsets

from inventory.models import PriceList, PriceListItem


from .serializers import PriceListSerializer, PriceListItemSerializer


class PriceListViewSet(viewsets.ModelViewSet):
    queryset = PriceList.objects.all()
    serializer_class = PriceListSerializer


class PriceListItemViewSet(viewsets.ModelViewSet):
    queryset = PriceListItem.objects.all()
    serializer_class = PriceListItemSerializer
