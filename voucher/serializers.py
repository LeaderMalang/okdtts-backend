from rest_framework import serializers

from .models import ChartOfAccount, Voucher, VoucherEntry, VoucherType


class ChartOfAccountSerializer(serializers.ModelSerializer):
    accountType = serializers.CharField(source="account_type.name")
    parentId = serializers.IntegerField(source="parent_account_id", allow_null=True)
    isActive = serializers.BooleanField(source="is_active")

    class Meta:
        model = ChartOfAccount
        fields = ["id", "name", "code", "accountType", "parentId", "isActive"]


class VoucherEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = VoucherEntry
        fields = ["account", "debit", "credit", "remarks"]


class JournalVoucherSerializer(serializers.ModelSerializer):
    entries = VoucherEntrySerializer(many=True)

    class Meta:
        model = Voucher
        fields = ["id", "date", "narration", "branch", "entries"]
        read_only_fields = ["id"]

    def validate(self, attrs):
        entries = attrs.get("entries", [])
        debit_total = sum(e["debit"] for e in entries)
        credit_total = sum(e["credit"] for e in entries)
        if debit_total != credit_total:
            raise serializers.ValidationError("Entries are not balanced")
        attrs["amount"] = debit_total
        return attrs

    def create(self, validated_data):
        entries_data = validated_data.pop("entries")
        request = self.context.get("request")
        created_by = request.user if request else None
        voucher_type = VoucherType.objects.get(code=VoucherType.JOURNAL)
        voucher = Voucher.create_with_entries(
            voucher_type=voucher_type,
            date=validated_data["date"],
            narration=validated_data.get("narration", ""),
            created_by=created_by,
            branch=validated_data.get("branch"),
            entries=entries_data,
        )
        return voucher
