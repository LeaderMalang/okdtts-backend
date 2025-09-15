from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum

from voucher.models import (
    AccountType,
    ChartOfAccount,
    FinancialYear,
    Voucher,
    VoucherEntry,
    VoucherType,
)


class Command(BaseCommand):
    """Close the active financial year and carry forward the balance."""

    def add_arguments(self, parser):
        parser.add_argument(
            "equity_account",
            type=int,
            help="ID of the equity account (e.g. retained earnings)",
        )
        parser.add_argument(
            "--open-next",
            action="store_true",
            dest="open_next",
            help="Open the next financial year",
        )

    def handle(self, *args, **options):
        equity_account_id = options["equity_account"]
        open_next = options["open_next"]

        try:
            year = FinancialYear.objects.get(is_closed=False)
        except FinancialYear.DoesNotExist:  # pragma: no cover - guard clause
            raise CommandError("No active financial year found")

        equity_account = ChartOfAccount.objects.get(pk=equity_account_id)

        income_type = AccountType.objects.get(name="INCOME")
        expense_type = AccountType.objects.get(name="EXPENSE")

        voucher_type, _ = VoucherType.objects.get_or_create(
            code=VoucherType.JOURNAL, defaults={"name": "Journal"}
        )

        entries = []
        net_income = Decimal("0")

        accounts = ChartOfAccount.objects.filter(
            account_type__in=[income_type, expense_type]
        )
        for account in accounts:
            totals = VoucherEntry.objects.filter(
                account=account,
                voucher__date__range=(year.start_date, year.end_date),
            ).aggregate(debit=Sum("debit"), credit=Sum("credit"))
            debit = totals["debit"] or Decimal("0")
            credit = totals["credit"] or Decimal("0")

            if account.account_type_id == income_type.id:
                balance = credit - debit
                net_income += balance
                if balance > 0:
                    entries.append({"account": account, "debit": balance, "credit": Decimal("0")})
                elif balance < 0:
                    entries.append({"account": account, "credit": -balance, "debit": Decimal("0")})
            else:  # expense account
                balance = debit - credit
                net_income -= balance
                if balance > 0:
                    entries.append({"account": account, "credit": balance, "debit": Decimal("0")})
                elif balance < 0:
                    entries.append({"account": account, "debit": -balance, "credit": Decimal("0")})

        if net_income > 0:
            entries.append({"account": equity_account, "credit": net_income, "debit": Decimal("0")})
        elif net_income < 0:
            entries.append({"account": equity_account, "debit": -net_income, "credit": Decimal("0")})

        if entries:
            Voucher.create_with_entries(
                voucher_type=voucher_type,
                date=year.end_date,
                narration=f"Year end closing {year}",
                created_by=None,
                entries=entries,
            )

        year.is_closed = True
        year.save()

        if open_next:
            duration = year.end_date - year.start_date
            next_start = year.end_date + timedelta(days=1)
            next_end = next_start + duration
            FinancialYear.objects.create(start_date=next_start, end_date=next_end)

        self.stdout.write(self.style.SUCCESS("Financial year closed"))
