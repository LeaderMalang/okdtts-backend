# inventory/management/commands/init_customer_accounts.py
from django.core.management.base import BaseCommand
from django.db import transaction
from django_ledger.models import AccountModel
from inventory.models import Party  # adjust import if your Party is elsewhere

OPENING_EQUITY_CODE = "3999"
OPENING_EQUITY_NAME = "Opening Balance Equity"

def _set_account_type_kwargs(model_cls, desired="EQUITY"):
    """
    Return a dict with the appropriate field for account type/category, if present
    on AccountModel. Different versions use different names (type/account_type/category).
    """
    fields = {f.name for f in model_cls._meta.get_fields() if hasattr(f, "attname")}
    if "type" in fields:
        return {"type": desired}
    if "account_type" in fields:
        return {"account_type": desired}
    if "category" in fields:
        return {"category": desired}
    return {}

def _ensure_opening_balance_equity():
    """
    Ensure 3999 Opening Balance Equity exists.
    """
    type_kwargs = _set_account_type_kwargs(AccountModel, desired="EQUITY")
    acct, created = AccountModel.objects.get_or_create(
        code=OPENING_EQUITY_CODE,
        defaults={
            "name": OPENING_EQUITY_NAME,
            **type_kwargs,
        },
    )
    return acct, created

def _ensure_customer_ar_account(customer: Party):
    """
    Ensure the Party (customer) has an AR account in AccountModel and is linked to Party.chart_of_account.
    Uses ASSET account type if the model exposes a type/category field.
    """
    if getattr(customer, "party_type", None) != "customer":
        return None, False

    if getattr(customer, "chart_of_account_id", None):
        # already set
        return customer.chart_of_account, False

    # Build a stable code and name for the customer account.
    code = f"AR-CUST{customer.pk:05d}"
    name = f"{customer.name} A/R"

    type_kwargs = _set_account_type_kwargs(AccountModel, desired="ASSET")
    account, _ = AccountModel.objects.get_or_create(
        code=code,
        defaults={
            "name": name,
            **type_kwargs,
        },
    )
    # Link it to the party
    customer.chart_of_account = account
    customer.save(update_fields=["chart_of_account"])
    return account, True

class Command(BaseCommand):
    help = "Ensure 3999 Opening Balance Equity exists and every customer has an A/R account."

    @transaction.atomic
    def handle(self, *args, **options):
        ob_acct, created_equity = _ensure_opening_balance_equity()
        self.stdout.write(
            self.style.SUCCESS(
                f"{'Created' if created_equity else 'Exists'}: {OPENING_EQUITY_CODE} - {ob_acct.name}"
            )
        )

        created_count = 0
        linked_count = 0
        for cust in Party.objects.filter(party_type="customer"):
            acct, created = _ensure_customer_ar_account(cust)
            if acct:
                linked_count += 1
            if created:
                created_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Linked accounts for {linked_count} customers "
                f"(new A/R accounts created: {created_count})."
            )
        )
        self.stdout.write(self.style.SUCCESS("✅ Done."))
