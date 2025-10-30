from decimal import Decimal, ROUND_HALF_UP
from django import forms
from django.forms import formset_factory
from django.forms.models import BaseInlineFormSet
from .models import PurchaseInvoiceItem, PurchaseInvoice
from setting.models import Warehouse
from django.core.exceptions import ValidationError
import uuid
from django.utils.dateparse import parse_date
from datetime import datetime
Q = Decimal("0.01")

class RefundCreditBreakdownForm(forms.Form):
    refund_amount = forms.DecimalField(
        max_digits=12, decimal_places=2, min_value=0, label="Cash/Bank Refund Amount"
    )
    credit_amount = forms.DecimalField(
        max_digits=12, decimal_places=2, min_value=0, label="Supplier Credit Amount"
    )
    note = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 2}),
        label="Internal Note (optional)"
    )

    def __init__(self, *args, outstanding: Decimal, **kwargs):
        super().__init__(*args, **kwargs)
        self.outstanding = Decimal(outstanding or 0).quantize(Q)

    def clean(self):
        cleaned = super().clean()
        refund = Decimal(cleaned.get("refund_amount") or 0).quantize(Q, rounding=ROUND_HALF_UP)
        credit = Decimal(cleaned.get("credit_amount") or 0).quantize(Q, rounding=ROUND_HALF_UP)
        if refund + credit != self.outstanding:
            raise forms.ValidationError(
                f"Refund + Credit must equal outstanding total {self.outstanding}."
            )
        cleaned["refund_amount"] = refund
        cleaned["credit_amount"] = credit
        return cleaned




class PaymentCreditBreakdownForm(forms.Form):
    pay_amount = forms.DecimalField(
        max_digits=12, decimal_places=2, min_value=0, label="Cash/Bank Payment Amount"
    )
    credit_amount = forms.DecimalField(
        max_digits=12, decimal_places=2, min_value=0, label="Credit Set-off Amount"
    )
    note = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 2}),
        label="Internal Note (optional)"
    )

    def __init__(self, *args, outstanding: Decimal, **kwargs):
        super().__init__(*args, **kwargs)
        self.outstanding = Decimal(outstanding or 0).quantize(Q)

    def clean(self):
        cleaned = super().clean()
        pay = Decimal(cleaned.get("pay_amount") or 0).quantize(Q, rounding=ROUND_HALF_UP)
        cred = Decimal(cleaned.get("credit_amount") or 0).quantize(Q, rounding=ROUND_HALF_UP)
        if pay + cred != self.outstanding:
            raise forms.ValidationError(f"Payment + Credit must equal outstanding total {self.outstanding}.")
        cleaned["pay_amount"] = pay
        cleaned["credit_amount"] = cred
        return cleaned



class GRNLineForm(forms.Form):
    invoice_item_id = forms.IntegerField(widget=forms.HiddenInput)
    product_label   = forms.CharField(disabled=True, required=False, label="Product")
    outstanding     = forms.IntegerField(disabled=True, required=False, label="Outstanding")
    receive_qty     = forms.IntegerField(min_value=0, label="Receive Qty")
    batch_number    = forms.CharField(required=False)
    expiry_date     = forms.DateField(required=False, widget=forms.DateInput(attrs={"type":"date"}))
    purchase_price  = forms.DecimalField(required=False, max_digits=10, decimal_places=2)
    sale_price      = forms.DecimalField(required=False, max_digits=10, decimal_places=2)

GRNLineFormSet = formset_factory(GRNLineForm, extra=0)

class DateInput(forms.DateInput):
    input_type = "date"

class PurchaseInvoiceForm(forms.ModelForm):
    date = forms.CharField()
    class Meta:
        model = PurchaseInvoice
        fields = [
            "invoice_no", "date", "supplier", "warehouse",
            "total_amount","grand_total",
            # "paid_amount", "credited_amount",
            "status", "payment_status",
        ]
    # widgets = {
    #     "date": DateInput(),
    # }
    help_texts = {
        "date": "please enter like 12/05/2024 or 5 Dec 2024.",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # ✅ Auto-select first warehouse as default (if not editing an existing record)
        if not self.instance.pk and Warehouse.objects.exists():
            self.fields["warehouse"].initial = Warehouse.objects.first()
    def clean_date(self):
        raw_date = self.cleaned_data.get("date")

        # Try multiple formats manually for flexibility
        for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d-%m-%y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y"):
            try:
                return datetime.strptime(raw_date, fmt).date()
            except ValueError:
                continue

        # Fallback — Django smart parser
        parsed = parse_date(raw_date)
        if parsed:
            return parsed

        raise forms.ValidationError("Invalid date — please enter like 12/05/2024 or 5 Dec 2024")
    def clean(self):
        cleaned = super().clean()
        total = cleaned.get("total_amount") or Decimal("0")
        discount = cleaned.get("discount") or Decimal("0")
        tax = cleaned.get("tax") or Decimal("0")
        paid = cleaned.get("paid_amount") or Decimal("0")
        credited = cleaned.get("credited_amount") or Decimal("0")

        # Basic validations
        for fld in ("total_amount", "discount", "tax", "paid_amount", "credited_amount"):
            if cleaned.get(fld) is not None and cleaned[fld] < 0:
                raise ValidationError({fld: "Must be ≥ 0."})

        if discount > total:
            raise ValidationError({"discount": "Discount cannot exceed total amount."})

        # Compute grand_total (server-side truth)
        grand_total = total - discount + tax
        cleaned["grand_total"] = grand_total

        # Derive payment_status
        due = grand_total - paid - credited
        if due <= 0:
            cleaned["payment_status"] = "PAID"
        elif paid > 0 or credited > 0:
            cleaned["payment_status"] = "PARTIAL"
        else:
            cleaned["payment_status"] = "UNPAID"

        return cleaned

    def save(self, commit=True):
        obj: PurchaseInvoice = super().save(commit=False)

        # Ensure grand_total derived from cleaned data
        obj.grand_total = self.cleaned_data["grand_total"]
        obj.payment_status = self.cleaned_data["payment_status"]

        # Auto-generate invoice_no if empty
        if not obj.invoice_no:
            obj.invoice_no = f"PI-{obj.date.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

        if commit:
            obj.save()
        return obj

class PurchaseInvoiceItemForm(forms.ModelForm):
    line_total = forms.DecimalField(max_digits=14, decimal_places=2, required=False, disabled=True, label="Line Total")
    expiry_date = forms.CharField(label="expiry date", required=True)
    class Meta:
        model = PurchaseInvoiceItem
        fields = ("product", "quantity", "purchase_price", "sale_price", "batch_number", "expiry_date")
    help_texts = {
        "expiry_date": "please enter like 12/05/2025 or 5 Dec 2025.",
    }
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ins = self.instance if hasattr(self, "instance") else None
        if ins and getattr(ins, "pk", None):
            # Prefill from saved amount (authoritative)
            amt = getattr(ins, "amount", None)
            if amt is None:
                # fallback compute if amount wasn’t set for some reason
                q = Decimal(ins.quantity or 0)
                p = Decimal(ins.purchase_price or 0)
                amt = (q * p).quantize(Decimal("0.01"))
            self.fields["line_total"].initial = amt
        else:
            # new/extra rows default 0.00
            self.fields["line_total"].initial = Decimal("0.00")
    
    def clean_expiry_date(self):
        raw_date = self.cleaned_data.get("expiry_date")

        # Try multiple formats manually for flexibility
        for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d-%m-%y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y"):
            try:
                return datetime.strptime(raw_date, fmt).date()
            except ValueError:
                continue

        # Fallback — Django smart parser
        parsed = parse_date(raw_date)
        if parsed:
            return parsed

        raise forms.ValidationError("Invalid date — please enter like 12/05/2024 or 5 Dec 2024")
    def clean(self):
        cd = super().clean()
        qty = Decimal(cd.get("quantity") or 0)
        price = Decimal(cd.get("purchase_price") or 0)
        if qty < 0 or price < 0:
            raise forms.ValidationError("Quantity and Purchase Price must be non-negative.")
        cd["line_total"] = (qty * price).quantize(Decimal("0.01"))
        return cd


class PurchaseInvoiceItemFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        # Optional: cross-row validation if needed
        return self