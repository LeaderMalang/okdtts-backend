from django.contrib import admin
from .models import (
    Employee, EmployeeContract, LeaveRequest, SalesTarget,
    Attendance, LeaveBalance, PayrollSlip, DeliveryAssignment
)
from django.utils.html import format_html
from django.utils.timezone import now
from datetime import timedelta
from django.db.models import Sum
from django.contrib import messages
from django.http import HttpResponse
from django.template.loader import render_to_string
from xhtml2pdf import pisa
from django.db import transaction
@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ('name', 'phone', 'active')
    list_filter = ( 'active',)

@admin.register(EmployeeContract)
class EmployeeContractAdmin(admin.ModelAdmin):
    list_display = ('employee', 'start_date', 'end_date', 'salary')

@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = ('employee', 'leave_type', 'start_date', 'end_date', 'status')
    list_filter = ('status', 'leave_type')
    actions = ['approve_selected', 'reject_selected']

    def approve_selected(self, request, queryset):
        updated = queryset.update(status='APPROVED', reviewed_by=request.user)
        self.message_user(request, f"{updated} leave(s) approved.")
    approve_selected.short_description = "Approve selected leaves"

    def reject_selected(self, request, queryset):
        updated = queryset.update(status='REJECTED', reviewed_by=request.user)
        self.message_user(request, f"{updated} leave(s) rejected.")
    reject_selected.short_description = "Reject selected leaves"

@admin.register(SalesTarget)
class SalesTargetAdmin(admin.ModelAdmin):
    list_display = ('employee', 'month', 'target_amount')
    list_filter = ('month',)

@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ('employee', 'date', 'check_in', 'check_out', 'is_absent')
    list_filter = ('date', 'is_absent')

@admin.register(LeaveBalance)
class LeaveBalanceAdmin(admin.ModelAdmin):
    list_display = ('employee', 'annual', 'sick', 'casual')

# --- PDF Helper ---

def generate_pdf_invoice(invoice):
    context = {
        'invoice': invoice,
        'items': [],
        'invoice_type': invoice.__class__.__name__,
    }
    html = render_to_string("invoices/pdf_invoice.html", context)
    response = HttpResponse(content_type='application/pdf')
    pisa.CreatePDF(html, dest=response)
    return response

# --- Admin Actions ---

# Optional: Your existing single-row PDF print stub
def print_invoice_pdf(modeladmin, request, queryset):
    if queryset.count() == 1:
        obj = queryset.first()
        # Implement and return an HttpResponse/PDF
        return HttpResponse(f"PDF for {obj}")  # stub
    return HttpResponse("Please select only one invoice to print.")
print_invoice_pdf.short_description = "Print Payroll PDF"


@admin.register(PayrollSlip)
class PayrollSlipAdmin(admin.ModelAdmin):
    list_display = (
        "employee", "month", "base_salary", "present_days", "absent_days",
        "deductions", "net_salary", "status", "pdf_link",
    )
    readonly_fields = ("created_on", "accrual_txn", "payment_txn", "status", "pdf_link")
    list_filter = ("month", "status")
    search_fields = ("employee__name",)
    actions = [print_invoice_pdf, "generate_payroll", "confirm_slips", "mark_slips_paid"]

    def pdf_link(self, obj):
        return format_html('<a href="#">Download PDF</a>')
    pdf_link.short_description = "PDF Slip"

    @admin.action(description="Generate Payroll for Current Month")
    def generate_payroll(self, request, queryset):
        """
        Your original generator, kept intact but without voucher logic.
        """
        today = now().date()
        month_start = today.replace(day=1)
        month_end = (month_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)

        employees = Employee.objects.filter(active=True)
        created = 0
        for emp in employees:
            if PayrollSlip.objects.filter(employee=emp, month=month_start).exists():
                continue

            total_attendance = Attendance.objects.filter(employee=emp, date__range=[month_start, month_end])
            present = total_attendance.exclude(is_absent=True).count()
            absent = total_attendance.filter(is_absent=True).count()

            try:
                contract = EmployeeContract.objects.filter(employee=emp).latest('start_date')
            except EmployeeContract.DoesNotExist:
                continue

            daily_salary = contract.salary / 30
            deduction = daily_salary * absent
            net = contract.salary - deduction

            PayrollSlip.objects.create(
                employee=emp,
                month=month_start,
                base_salary=contract.salary,
                present_days=present,
                absent_days=absent,
                deductions=deduction,
                net_salary=net,  # model will recompute consistently anyway
            )
            created += 1
        self.message_user(request, f"{created} payroll slips generated.", level=messages.SUCCESS)

    @admin.action(description="Confirm (Accrue) selected payroll slips")
    def confirm_slips(self, request, queryset):
        processed = skipped = 0
        with transaction.atomic():
            for slip in queryset.select_for_update():
                try:
                    slip.confirm()
                    processed += 1
                except Exception as e:
                    skipped += 1
                    self.message_user(request, f"{slip}: could not confirm ({e})", level=messages.WARNING)
        if processed:
            self.message_user(request, f"Confirmed {processed} slip(s).", level=messages.SUCCESS)
        if skipped:
            self.message_user(request, f"Skipped {skipped} slip(s).", level=messages.INFO)

    @admin.action(description="Mark Paid (Disburse) selected payroll slips")
    def mark_slips_paid(self, request, queryset):
        processed = skipped = 0
        with transaction.atomic():
            for slip in queryset.select_for_update():
                try:
                    slip.mark_paid()
                    processed += 1
                except Exception as e:
                    skipped += 1
                    self.message_user(request, f"{slip}: could not mark paid ({e})", level=messages.WARNING)
        if processed:
            self.message_user(request, f"Marked {processed} slip(s) as PAID.", level=messages.SUCCESS)
        if skipped:
            self.message_user(request, f"Skipped {skipped} slip(s).", level=messages.INFO)
@admin.register(DeliveryAssignment)
class DeliveryAssignmentAdmin(admin.ModelAdmin):
    list_display = ('employee', 'sale', 'assigned_date', 'status')
    list_filter = ('status',)
