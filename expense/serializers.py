from rest_framework import serializers
from .models import ExpenseCategory, Expense


class ExpenseCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ExpenseCategory

        fields = "__all__"



class ExpenseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Expense

        fields = "__all__"

