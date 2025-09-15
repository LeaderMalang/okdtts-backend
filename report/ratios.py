from decimal import Decimal
from django.db.models import Sum, F
from voucher.models import VoucherEntry


def _sum_entries(entries, account_type_name, positive="debit"):
    """Return the summed balance for an account type.

    Parameters
    ----------
    entries: QuerySet[VoucherEntry]
        Entries to aggregate.
    account_type_name: str
        Name of the :class:`~voucher.models.AccountType` to filter on.
    positive: str
        Whether the natural positive side of the account is ``debit`` or ``credit``.
    """
    expr = F("debit") - F("credit") if positive == "debit" else F("credit") - F("debit")
    return (
        entries.filter(account__account_type__name=account_type_name).aggregate(total=Sum(expr))["total"]
        or Decimal("0")
    )


def current_ratio(*, current_assets=None, current_liabilities=None, entries=None):
    """Calculate the current ratio.

    Either ``current_assets`` and ``current_liabilities`` can be provided directly,
    or a queryset of :class:`~voucher.models.VoucherEntry` can be supplied via
    ``entries`` to derive the values from underlying accounting data.
    """
    if entries is not None:
        if current_assets is None:
            current_assets = _sum_entries(entries, "ASSET", positive="debit")
        if current_liabilities is None:
            current_liabilities = _sum_entries(entries, "LIABILITY", positive="credit")
    if not current_liabilities:
        return None
    return float(current_assets) / float(current_liabilities)


def gross_profit_margin(*, gross_profit=None, revenue=None, entries=None):
    """Calculate the gross profit margin.

    Parameters can be supplied directly or derived from ``entries``. When using
    ``entries``, all ``INCOME`` accounts are treated as revenue and all ``EXPENSE``
    accounts as cost of goods sold.
    """
    if entries is not None:
        if revenue is None:
            revenue = _sum_entries(entries, "INCOME", positive="credit")
        if gross_profit is None:
            expenses = _sum_entries(entries, "EXPENSE", positive="debit")
            gross_profit = revenue - expenses
    if not revenue:
        return None
    return float(gross_profit) / float(revenue)
