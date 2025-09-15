from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import (
    Attendance,
    DeliveryAssignment,
    Employee,
    EmployeeContract,
    LeaveBalance,
    LeaveRequest,
    PayrollSlip,
    SalesTarget,
    Task,
)
from .serializers import (
    AttendanceSerializer,
    DeliveryAssignmentSerializer,
    EmployeeContractSerializer,
    EmployeeSerializer,
    LeaveBalanceSerializer,
    LeaveRequestSerializer,
    PayrollSlipSerializer,
    SalesTargetSerializer,
    TaskSerializer,
)


class BaseViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    

class EmployeeViewSet(BaseViewSet):
    queryset = Employee.objects.all()
    serializer_class = EmployeeSerializer


class EmployeeContractViewSet(BaseViewSet):
    queryset = EmployeeContract.objects.all()
    serializer_class = EmployeeContractSerializer


class LeaveRequestViewSet(BaseViewSet):
    queryset = LeaveRequest.objects.all()
    serializer_class = LeaveRequestSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        employee_id = self.request.query_params.get("employee")
        if employee_id:
            qs = qs.filter(employee_id=employee_id)
        return qs

    def _deduct_balance_if_approved(self, instance, previous_status=None):
        if instance.status == "APPROVED" and previous_status != "APPROVED":
            days = (instance.end_date - instance.start_date).days + 1
            leave_balance, _ = LeaveBalance.objects.get_or_create(
                employee=instance.employee
            )
            leave_balance.deduct_leave(instance.leave_type, days)

    def perform_create(self, serializer):
        instance = serializer.save()
        self._deduct_balance_if_approved(instance)

    def perform_update(self, serializer):
        previous_status = serializer.instance.status
        instance = serializer.save()
        self._deduct_balance_if_approved(instance, previous_status)

    @action(detail=True, methods=["patch"], url_path="status")
    def update_status(self, request, pk=None):
        instance = self.get_object()
        status_value = request.data.get("status")
        serializer = self.get_serializer(
            instance, data={"status": status_value}, partial=True
        )
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response(serializer.data)


class AttendanceViewSet(BaseViewSet):
    queryset = Attendance.objects.all()
    serializer_class = AttendanceSerializer


class SalesTargetViewSet(BaseViewSet):
    queryset = SalesTarget.objects.all()
    serializer_class = SalesTargetSerializer


class TaskViewSet(BaseViewSet):
    queryset = Task.objects.all()
    serializer_class = TaskSerializer


class DeliveryAssignmentViewSet(BaseViewSet):
    queryset = DeliveryAssignment.objects.all()
    serializer_class = DeliveryAssignmentSerializer


class LeaveBalanceViewSet(BaseViewSet):
    queryset = LeaveBalance.objects.all()
    serializer_class = LeaveBalanceSerializer


class PayrollSlipViewSet(BaseViewSet):
    queryset = PayrollSlip.objects.all()
    serializer_class = PayrollSlipSerializer
