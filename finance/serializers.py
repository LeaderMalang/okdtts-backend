from rest_framework import serializers

from .models import FinancialYear, PaymentSchedule, PaymentTerm


class PaymentTermSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentTerm
        fields = '__all__'


class FinancialYearSerializer(serializers.ModelSerializer):
    class Meta:
        model = FinancialYear
        fields = '__all__'


class PaymentScheduleSerializer(serializers.ModelSerializer):
    voucher = serializers.SerializerMethodField(read_only=True)

    def get_voucher(self, obj):
        if obj.voucher:
            return {
                "id": obj.voucher.id,
                "voucher_type": obj.voucher.voucher_type.code,
                "date": obj.voucher.date,
                "amount": obj.voucher.amount,
                "narration": obj.voucher.narration,
                "status": obj.voucher.status,
            }
        return None

    class Meta:
        model = PaymentSchedule
        fields = '__all__'
        read_only_fields = ('voucher',)
