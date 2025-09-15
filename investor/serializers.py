from rest_framework import serializers
from inventory.models import Party
from .models import InvestorTransaction


class InvestorTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvestorTransaction
        fields = '__all__'


class PartySerializer(serializers.ModelSerializer):
    class Meta:
        model = Party
        fields = '__all__'
