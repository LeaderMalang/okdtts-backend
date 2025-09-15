from django.core.management.base import BaseCommand
from django.db import transaction
from voucher.models import VoucherType

# (code, name, description)
DEFAULT_VOUCHER_TYPES = [
    ("PUR",  "Purchase",                   "Vendor bill / purchase invoice"),
    ("SAL",  "Sale",                       "Customer invoice / sales voucher"),
    ("PRN",  "Purchase Return",            "Return to supplier"),
    ("SRN",  "Sale Return",                "Customer return / credit note"),
    ("PMT",  "Payment",                    "Cash/Bank payment to supplier or expense"),
    ("RCV",  "Receipt",                    "Cash/Bank receipt from customer or income"),
    ("JRN",  "Journal",                    "General journal entry / adjustments"),
    ("CN",   "Credit Note",                "Credit note issued to customer"),
    ("DN",   "Debit Note",                 "Debit note received from supplier"),
    ("EXP",  "Expense",                    "Misc. expense voucher"),
    ("PAYR", "Payroll",                    "Payroll posting voucher"),
]

class Command(BaseCommand):
    help = "Initialize default Voucher Types (idempotent)."

    @transaction.atomic
    def handle(self, *args, **options):
        created = 0
        for code, name, desc in DEFAULT_VOUCHER_TYPES:
            obj, was_created = VoucherType.objects.get_or_create(
                code=code,
                defaults={"name": name, "description": desc},
            )
            # if already exists but name/description changed later, keep them in sync
            if not was_created:
                updated = False
                if obj.name != name:
                    obj.name = name
                    updated = True
                if obj.description != desc:
                    obj.description = desc
                    updated = True
                if updated:
                    obj.save(update_fields=["name", "description"])
            else:
                created += 1

        self.stdout.write(self.style.SUCCESS(
            f"✅ Voucher types initialized. Created: {created}, Total present: {VoucherType.objects.count()}"
        ))
