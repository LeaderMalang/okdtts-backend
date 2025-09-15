from django import forms
from django.forms.models import inlineformset_factory

from .models import SaleInvoice, SaleInvoiceItem

from .models import SaleReturn, SaleReturnItem

class SaleReturnAdminForm(forms.ModelForm):
    class Meta:
        model = SaleReturn
        fields = ["return_no", "date", "invoice", "customer", "warehouse",  "total_amount",]
        widgets = {
            "total_amount": forms.NumberInput(attrs={"readonly": "readonly"}),
        }

    def __init__(self, *args, **kwargs):
        
        # allow admin to pass request for GET initial (optional)
        self.request = kwargs.pop("request", None)
        super().__init__(*args, **kwargs)

        # If creating new and invoice is preselected via ?invoice=<id>, set initials
        if not self.instance.pk:
            invoice_id = None
            if self.request:
                invoice_id = self.request.GET.get("invoice") or self.request.POST.get("invoice")
            inv = None
            try:
                inv = self.instance.invoice or (invoice_id and self._meta.model._meta.apps.get_model('sale','SaleInvoice').objects.filter(pk=invoice_id).select_related('customer','warehouse').first())
            except Exception:
                inv = None

            if inv:
                self.fields["customer"].initial = inv.customer_id
                self.fields["warehouse"].initial = inv.warehouse_id
                # self.fields["payment_method"].initial = inv.payment_method

    def clean(self):
        cleaned = super().clean()
        inv = cleaned.get("invoice")
        # Always mirror key fields from invoice (admin can still change if you want, else set them read‑only in ModelAdmin)
        if inv:
            cleaned["customer"] = inv.customer
            cleaned["warehouse"] = inv.warehouse
            cleaned["payment_method"] = inv.payment_method
        return cleaned
class SaleInvoiceForm(forms.ModelForm):
    class Meta:
        model = SaleInvoice
        fields = (
            'invoice_no',
            'date',
            'customer',
            'warehouse',

            'total_amount',
            'discount',
            'tax',
            'paid_amount',
            'status',
        )

    def clean(self):
        cleaned_data = super().clean()
        total = cleaned_data.get('total_amount') or 0
        discount = cleaned_data.get('discount') or 0
        tax = cleaned_data.get('tax') or 0
        grand_total = total - discount + tax
        paid = cleaned_data.get('paid_amount') or 0
        if paid > grand_total:
            self.add_error('paid_amount', 'Paid amount cannot exceed grand total.')
        return cleaned_data


        

SaleInvoiceItemForm = inlineformset_factory(
    SaleInvoice,
    SaleInvoiceItem,
    fields=('product', 'quantity', 'rate', 'amount'),
    extra=1,
    can_delete=True,
)


