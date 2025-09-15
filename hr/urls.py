from rest_framework.routers import DefaultRouter

from .views import (
    AttendanceViewSet,
    DeliveryAssignmentViewSet,
    EmployeeContractViewSet,
    EmployeeViewSet,
    LeaveBalanceViewSet,
    LeaveRequestViewSet,
    PayrollSlipViewSet,
    SalesTargetViewSet,
    TaskViewSet,
)

router = DefaultRouter()
router.register(r'employees', EmployeeViewSet)
router.register(r'contracts', EmployeeContractViewSet)
router.register(r'leave-requests', LeaveRequestViewSet)
router.register(r'attendance', AttendanceViewSet)
router.register(r'sales-targets', SalesTargetViewSet)
router.register(r'tasks', TaskViewSet)
router.register(r'delivery-assignments', DeliveryAssignmentViewSet)
router.register(r'leave-balances', LeaveBalanceViewSet)
router.register(r'payroll-slips', PayrollSlipViewSet)

urlpatterns = router.urls
