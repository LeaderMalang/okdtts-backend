# sale/admin_actions.py
from django import forms

class ReceivePaymentForm(forms.Form):
    _selected_action = forms.CharField(widget=forms.MultipleHiddenInput)  # keeps selection
    amount = forms.DecimalField(min_value=0.01, max_digits=12, decimal_places=2)
    payment_method = forms.ChoiceField(choices=(("Cash","Cash"),("Bank","Bank")))
