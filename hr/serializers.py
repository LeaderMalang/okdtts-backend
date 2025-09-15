from rest_framework import serializers
from .models import (
    Employee,
    EmployeeContract,
    LeaveRequest,
    Attendance,
    SalesTarget,
    Task,
    DeliveryAssignment,
    LeaveBalance,
    PayrollSlip,
)
from user.models import CustomUser


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ["id", "email", "password", "role"]
        extra_kwargs = {"password": {"write_only": True}}

    def create(self, validated_data):
        # use create_user so password is hashed
        return CustomUser.objects.create_user(**validated_data)

class EmployeeSerializer(serializers.ModelSerializer):
    user = UserSerializer()
    class Meta:
        model = Employee
        fields = [
            "id",
            "user",
            "role",
            "name",
            "phone",
            # "email",
            "cnic",
            "address",
            "active",
        ]
    def create(self, validated_data):
        # Pop nested user data
        user_data = validated_data.pop("user")
        user = UserSerializer().create(user_data)

        # Create employee linked to user
        employee = Employee.objects.create(user=user, **validated_data)
        return employee

    def update(self, instance, validated_data):
        user_data = validated_data.pop("user", None)
        if user_data:
            user_serializer = UserSerializer(instance=instance.user, data=user_data, partial=True)
            if user_serializer.is_valid(raise_exception=True):
                user_serializer.save()

        return super().update(instance, validated_data)


class EmployeeContractSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmployeeContract
        fields = [
            "id",
            "employee",
            "start_date",
            "end_date",
            "salary",
            "notes",
        ]


class LeaveRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaveRequest
        fields = [
            "id",
            "employee",
            "leave_type",
            "start_date",
            "end_date",
            "reason",
            "status",
            "applied_on",
            "reviewed_by",
        ]


class AttendanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Attendance
        fields = [
            "id",
            "employee",
            "date",
            "check_in",
            "check_out",
            "is_absent",
            "remarks",
        ]


class SalesTargetSerializer(serializers.ModelSerializer):
    class Meta:
        model = SalesTarget
        fields = ["id", "employee", "month", "target_amount"]


class TaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = Task
        fields = [
            "id",
            "assignment",
            "assigned_to",
            "assigned_by",
            "due_date",
            "status",
            "party",
            "invoice_content_type",
            "invoice_object_id",
            "created_at",
            "updated_at",
        ]


class DeliveryAssignmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliveryAssignment
        fields = [
            "id",
            "employee",
            "sale",
            "assigned_date",
            "status",
            "remarks",
        ]


class LeaveBalanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaveBalance
        fields = ["id", "employee", "annual", "sick", "casual"]


class PayrollSlipSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayrollSlip
        fields = [
            "id",
            "employee",
            "month",
            "base_salary",
            "present_days",
            "absent_days",
            "leaves_paid",
            "deductions",
            "net_salary",
            "voucher",
            "created_on",
        ]
