from django.core.management.base import BaseCommand
from voucher.models import AccountType, ChartOfAccount
from inventory.models import Party
from setting.models import Warehouse, Company
from django.db import transaction

DEFAULT_COA = {
    'ASSET': [
        ('1001', 'Cash'),
        ('1002', 'Bank'),
        ('1003', 'Accounts Receivable'),
        ('1004', 'Inventory'),
        ("1300","Purchase tax receivable"),
        ("1400", "Prepaid Expenses"),
        ("1500", "Fixed Assets"),
        ("1600", "Accumulated Depreciation"),
        ("1700", "Other Assets"),
        ('1005', 'Short-term Investments'),
        ('1006', 'Long-term Investments'),
        ('1007', 'Intangible Assets'),
        ('1008', 'Goodwill'),
        ('1009', 'Deferred Tax Assets'),
        ('1010', 'Other Current Assets'),
    ],
    'LIABILITY': [
        ('2001', 'Accounts Payable'),
        ('2002', 'Loans Payable'),
        ("2100", "Sales tax payable"),
        ("2200", "Withholding tax payable"),
        ("2300", "Payroll tax payable"),
        ("2400", "Other current liabilities"),
        ("2500", "Long-term liabilities"),
        ('2003', 'Accrued Expenses'),
        ('2004', 'Unearned Revenue'),
        ('2005', 'Credit Card Payable'),
    ],
    'INCOME': [
        ('4001', 'Sales Revenue'),
        ('4002', 'Service Income'),
        ('4003', 'Interest Income'),
        ('4004', 'Other Income'),
        ('4005', 'Sales Returns and Allowances'),
        ('4006', 'Discounts Allowed'),
        ('4007', 'Commission Income'),
        ('4008', 'Rental Income'),
        ('4009', 'Gain on Sale of Assets'),
        ('4010', 'Foreign Exchange Gains'),
        ('4011', 'Investment Income'),
        ('4012', 'Royalty Income'),
        ('4013', 'Dividend Income'),
        ('4014', 'Miscellaneous Income'),
        ('4015', 'Rebates and Discounts Received'),
    ],
    'EXPENSE': [
        ('5001', 'Purchase'),
        ('5002', 'Salaries'),
        ('5003', 'Rent'),
        ('5004', 'Utilities'),
        ('5005', 'Depreciation'),
        ('5006', 'Marketing Expenses'),
        ('5007', 'Office Supplies'),
        ('5008', 'Travel Expenses'),
        ('5009', 'Insurance'),
        ('5010', 'Professional Fees'),
        ('5011', 'Repairs and Maintenance'),
        ('5012', 'Other Expenses'),
        ('5013', 'Cost of Goods Sold'),
        ('5014', 'Payroll Expenses'),
        ('5015', 'Bank Charges'),
        ('5016', 'Bad Debts'),
        ('5017', 'Training and Development'),
        ('5018', 'Miscellaneous Expenses'),
        ('5019', 'Foreign Exchange Losses'),
    ],
    'EQUITY': [
        ('3001', 'Owner’s Capital'),
        ('3002', 'Retained Earnings'),
        ('3003', 'Dividends'),
        ('3004', 'Drawings'),
        ('3005', 'Other Equity'),
        ('3006', 'Revaluation Surplus'),
        ('3007', 'Foreign Currency Translation Reserve'),
        ('3008', 'Investment Revaluation Reserve'),
    ],
}

class Command(BaseCommand):
    help = "Initialize default Chart of Accounts and link to Parties & Warehouses"

    @transaction.atomic
    def handle(self, *args, **options):
        type_objs = {}
        for key, label in AccountType.ACCOUNT_TYPES:
            atype, _ = AccountType.objects.get_or_create(name=key)
            type_objs[key] = atype

        for acc_type, entries in DEFAULT_COA.items():
            for code, name in entries:
                ChartOfAccount.objects.get_or_create(
                    code=code,
                    name=name,
                    account_type=type_objs[acc_type]
                )

        for party in Party.objects.all():
            if not party.chart_of_account:
                coa, _ = ChartOfAccount.objects.get_or_create(
                    name=f"{party.name} A/C",
                    code=f"PTY{party.id:04}",
                    account_type=type_objs['ASSET' if party.party_type == 'customer' else 'LIABILITY']
                )
                party.chart_of_account = coa
                party.save()

        for wh in Warehouse.objects.all():
            if not wh.default_sales_account:
                wh.default_sales_account = ChartOfAccount.objects.get(code="4001")
            if not wh.default_purchase_account:
                wh.default_purchase_account = ChartOfAccount.objects.get(code="5001")
            wh.save()

        for comp in Company.objects.all():
            if not comp.payroll_expense_account:
                comp.payroll_expense_account = ChartOfAccount.objects.get(code="5002")
            if not comp.payroll_payment_account:
                comp.payroll_payment_account = ChartOfAccount.objects.get(code="1001")
            comp.save()

        self.stdout.write(self.style.SUCCESS("✅ Accounting initialization complete."))
