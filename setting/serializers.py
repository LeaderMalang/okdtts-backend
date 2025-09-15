from rest_framework import serializers

from .models import City, Area, Company, Group, Distributor, Branch, Warehouse



class CityFieldSerializer(serializers.ModelSerializer):
    class Meta:
        model = City
        fields = ("id", "name")

class CitySerializer(serializers.ModelSerializer):
    class Meta:
        model = City
        fields = "__all__"


class AreaSerializer(serializers.ModelSerializer):
    # Read-only nested city; keep city_id for writes
    city = CitySerializer(read_only=True)
    city_id = serializers.PrimaryKeyRelatedField(
        source="city", queryset=City.objects.all(), write_only=True
    )

    class Meta:
        model = Area
        fields = ("id", "name", "city", "city_id")


class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = "__all__"


class GroupSerializer(serializers.ModelSerializer):
    class Meta:
        model = Group
        fields = "__all__"


class DistributorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Distributor
        fields = "__all__"


class BranchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Branch
        fields = "__all__"


class WarehouseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Warehouse
        fields = "__all__"
