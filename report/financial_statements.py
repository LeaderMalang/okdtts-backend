"""Utility helpers for financial statement reports.

These services aggregate :class:`~voucher.models.VoucherEntry` balances by
their related :class:`~voucher.models.AccountType` for a given period.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Dict

from django.db.models import Sum

from voucher.models import VoucherEntry


def account_type_balances(*, start_date: date, end_date: date) -> Dict[str, Decimal]:
    """Return net balances grouped by account type.

    Balances are calculated as ``total_debit - total_credit`` for all voucher
    entries whose parent voucher falls within ``start_date`` and ``end_date``
    inclusive. The returned dictionary maps ``AccountType`` ``name`` values to
    their computed totals.
    """

    entries = (
        VoucherEntry.objects.filter(
            voucher__date__gte=start_date, voucher__date__lte=end_date
        )
        .values("account__account_type__name")
        .annotate(total_debit=Sum("debit"), total_credit=Sum("credit"))
    )

    totals: Dict[str, Decimal] = {}
    for row in entries:
        account_type = row["account__account_type__name"]
        totals[account_type] = row["total_debit"] - row["total_credit"]

    return totals


__all__ = ["account_type_balances"]

