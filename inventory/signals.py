# inventory/signals.py
from __future__ import annotations

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.conf import settings
from hordak.models import Account
from .models import Party

def _get_parent_account(party_type: str) -> Account:
    """
    Return the parent Hordak account (A/R for customers, A/P for suppliers).
    """
    id = 4 if party_type == "customer" else 8
    # name = 'Accounts Receivable' if party_type == "customer" else "Accounts Payable"
    return Account.objects.get(id=id)  # let it raise if missing (visible error)


def _ensure_unique_child_code(parent: Account, desired: str) -> str:
    """
    Ensure unique child code under Hordak. If taken, suffix -1, -2, ...
    """
    code = desired
    i = 1
    while Account.objects.filter(code=code).exists():
        code = f"{desired}-{i}"
        i += 1
    return code


@receiver(pre_save, sender=Party)
def _stash_old_party_type(sender, instance: Party, **kwargs):
    """
    Keep the previous party_type to detect type changes on post_save.
    """
    if instance.pk:
        try:
            old = Party.objects.get(pk=instance.pk)
            instance._old_party_type = old.party_type
        except Party.DoesNotExist:
            instance._old_party_type = None
    else:
        instance._old_party_type = None


@receiver(post_save, sender=Party)
def _auto_create_or_fix_hordak_account(sender, instance: Party, created: bool, **kwargs):
    """
    - On create: if no chart_of_account, create under appropriate parent (A/R or A/P).
    - On update: if party_type changed, reparent the existing account to the correct parent.
                 (Alternatively, you could create a new account and reassign, but reparent is simpler.)
    """
    # We only care about customers/suppliers here
    if instance.party_type not in {"customer", "supplier"}:
        return

    parent = _get_parent_account(instance.party_type)

    # 1) Creating a new Party → create a child Account if none assigned
    if created and not instance.chart_of_account_id:
        desired_code = f"{parent.code}-{instance.pk}"
        # code = _ensure_unique_child_code(parent, desired_code)
        acct = Account.objects.create(
            name=f"{instance.name}",
            code="-",
            parent=parent,
            # currency=getattr(parent, "currency", "PKR"),
        )
        # Assign without re-triggering signal logic
        Party.objects.filter(pk=instance.pk).update(chart_of_account=acct)
        return

    # 2) Updating → if type changed, re-parent existing account
    if not created and getattr(instance, "_old_party_type", None) and instance._old_party_type != instance.party_type:
        # Must have an account to move; if missing, create one like on create
        if not instance.chart_of_account_id:
            desired_code = f"{parent.code}-{instance.pk}"
            # code = _ensure_unique_child_code(parent, desired_code)
            acct = Account.objects.create(
                name=f"{instance.name}",
                code="-",
                parent=parent,
                # currency=getattr(parent, "currency", "PKR"),
            )
            Party.objects.filter(pk=instance.pk).update(chart_of_account=acct)
            return

        acct = instance.chart_of_account
        if acct.parent_id != parent.id:
            acct.parent = parent
            # Optional: keep currency aligned with parent
            if hasattr(acct, "currency") and hasattr(parent, "currency"):
                acct.currency = parent.currency
            acct.save(update_fields=["parent"] + (["currency"] if hasattr(acct, "currency") and hasattr(parent, "currency") else []))
