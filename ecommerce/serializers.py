from rest_framework import serializers

from .models import Order, OrderItem
from inventory.models import Product,Party
from hr.models import Employee



class EmployeeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Employee
        fields = ["id", "name"]
class PartySerializer(serializers.ModelSerializer):
    class Meta:
        model = Party
        fields = ["id", "name"]

class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ["id", "name"]


class OrderItemSerializer(serializers.ModelSerializer):
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all())
    
    class Meta:
        model = OrderItem
        fields = [
            "id",
            "product",
            "quantity",
            "price",
            "bid_price",
            "amount",
        ]

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        rep["product"] = ProductSerializer(instance.product).data
        return rep


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True)
    customer = serializers.PrimaryKeyRelatedField(queryset=Party.objects.all(), required=False)
    salesman = serializers.PrimaryKeyRelatedField(queryset=Employee.objects.all(), required=False,allow_null=True)
    class Meta:
        model = Order
        fields = [
            "id",
            "order_no",
            "date",
            "customer",
            "salesman",
            "status",
            "total_amount",
            "paid_amount",
            "address",
            "items",
        ]
        extra_kwargs = {
            "salesman": {"required": False, "allow_null": True},
        }

    def create(self, validated_data):
        items_data = validated_data.pop("items", [])
        order = Order.objects.create(**validated_data)
        for item_data in items_data:
            OrderItem.objects.create(order=order, **item_data)
        return order
    def update(self, instance, validated_data):
        # keep existing salesman/customer if not provided
        if "salesman" not in validated_data:
            validated_data["salesman"] = instance.salesman
        if "customer" not in validated_data:
            validated_data["customer"] = instance.customer

        items_data = validated_data.pop("items", None)
        for attr, val in validated_data.items():
            setattr(instance, attr, val)
        instance.save()

        # update nested items only if explicitly sent
        if items_data is not None:
            instance.items.all().delete()
            for item_data in items_data:
                OrderItem.objects.create(order=instance, **item_data)

        return instance
    def to_representation(self, instance):
        rep = super().to_representation(instance)
        rep["customer"] = PartySerializer(instance.customer).data
        rep["salesman"] = EmployeeSerializer(instance.salesman).data
        return rep