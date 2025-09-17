from decimal import Decimal
from django import forms
from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from django.db import transaction

from inventory.models import Party
from finance.hordak_posting import post_ar_opening

class OpeningBalanceForm(forms.Form):
    date = forms.DateField(initial=now().date, required=True, label=_("Date"))
    customer = forms.ModelChoiceField(
        queryset=Party.objects.filter(party_type="customer").select_related("chart_of_account"),
        label=_("Customer"),
        required=True,
    )
    amount = forms.DecimalField(
        max_digits=12, decimal_places=2, required=True,
        help_text=_("Positive = customer owes you (A/R). Negative = you owe customer (advance).")
    )
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), label=_("Note"))

    def clean(self):
        cleaned = super().clean()
        cust: Party = cleaned.get("customer")
        if cust and not cust.chart_of_account_id:
            raise forms.ValidationError(_("Selected customer has no chart of account."))
        return cleaned


@staff_member_required
def opening_balance_view_admin(request):
    """
    Admin-friendly screen to post an Opening Balance against a customer (A/R).
    """
    if request.method == "POST":
        form = OpeningBalanceForm(request.POST)
        if form.is_valid():
            date = form.cleaned_data["date"]
            customer: Party = form.cleaned_data["customer"]
            amount: Decimal = form.cleaned_data["amount"]
            note = form.cleaned_data.get("note", "")

            try:
                with transaction.atomic():
                    txn = post_ar_opening(
                        date=date,
                        description=f"Opening AR for {customer.name}. {note}".strip(),
                        customer_account=customer.chart_of_account,
                        amount=amount,
                    )
                    # Update Party.current_balance (same sign convention)
                    customer.current_balance = Decimal(customer.current_balance or 0) + Decimal(amount)
                    customer.save(update_fields=["current_balance"])
                messages.success(
                    request,
                    _(f"Opening balance posted for {customer} (amount {amount}). Transaction #{getattr(txn,'pk',None)}")
                )
                # back to admin index or referrer
                return redirect(reverse("admin:index"))
            except Exception as exc:
                messages.error(request, _(f"Error posting opening balance: {exc}"))
    else:
        form = OpeningBalanceForm()

    context = {
        "title": _("Post Opening Balance (A/R)"),
        "form": form,
        # admin chrome:
        **admin.site.each_context(request),
    }
    return render(request, "admin/finance/opening_balance.html", context)
