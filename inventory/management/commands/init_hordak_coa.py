# management/commands/init_hordak_coa.py
from django.core.management.base import BaseCommand
from djmoney.money import Money
from hordak.models import Account
from hordak.utilities import currency

# Pick your base currency (e.g., PKR)
BASE = 'PKR'

ROOTS = {
    'AS':     ['Cash', 'Bank', 'Accounts Receivable', 'Inventory', 'Tax Receivable'],
    'LI': ['Accounts Payable', 'Tax Payable'],
    'EQ':    ['Opening Balances'],
    'IN':    ['Sales'],
    'EX':   ['Purchases', 'Sales Returns', 'Purchase Returns'],
}

class Command(BaseCommand):
    help = "Initialize Hordak chart of accounts"

    def handle(self, *args, **opts):
        # create root buckets
        roots = {}
        for t, children in ROOTS.items():
            root, _ = Account.objects.get_or_create(name=t.title(), parent=None, defaults={'type': t})
            roots[t] = root
            for name in children:
                Account.objects.get_or_create(name=name, parent=root)

        self.stdout.write(self.style.SUCCESS('Hordak COA initialized'))
