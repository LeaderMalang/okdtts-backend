from django.contrib import admin
from django.http import HttpResponse
from django.template.loader import render_to_string
from xhtml2pdf import pisa

from .models import ExpenseCategory, Expense

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

def print_invoice_pdf(modeladmin, request, queryset):
    if queryset.count() == 1:
        return generate_pdf_invoice(queryset.first())
    return HttpResponse("Please select only one invoice to print.")

print_invoice_pdf.short_description = "Print Invoice PDF"

@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "chart_of_account")

@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ("date", "category", "amount", "payment_account", "voucher")
    readonly_fields = ("voucher",)
    actions = [print_invoice_pdf]
