from decimal import Decimal, ROUND_HALF_UP
from django import forms
from django.forms import formset_factory
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