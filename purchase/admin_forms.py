from decimal import Decimal, ROUND_HALF_UP
from django import forms
from django.forms import formset_factory
from django.forms.models import BaseInlineFormSet
from .models import PurchaseInvoiceItem
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



class PurchaseInvoiceItemForm(forms.ModelForm):
    line_total = forms.DecimalField(max_digits=14, decimal_places=2, required=False, disabled=True, label="Line Total")

    class Meta:
        model = PurchaseInvoiceItem
        fields = ("product", "quantity", "purchase_price", "sale_price", "batch_number", "expiry_date")
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