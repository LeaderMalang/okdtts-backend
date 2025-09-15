# # utils/opening_ar.py
# from django_ledger.models import AccountModel, JournalEntryModel, TransactionModel
# from utils.ledger import get_or_create_default_ledger
# from django.utils import timezone
# from decimal import Decimal as D

# def seed_opening_ar(customer, amount, date):
#     ledger = get_or_create_default_ledger()
#     if not ledger or not customer.chart_of_account:
#         raise ValueError("Missing ledger or customer account.")
#     equity = AccountModel.objects.filter(code='3999').first()  # Opening Balance Equity
#     if not equity:
#         raise ValueError("Create account 3999 Opening Balance Equity.")
#     je = JournalEntryModel.objects.create(
#         ledger=ledger, timestamp=timezone.now(),
#         description=f"Opening AR for {customer}"
#     )
#     TransactionModel.objects.bulk_create([
#         TransactionModel(journal_entry=je, account=customer.chart_of_account,
#                          tx_type=TransactionModel.DEBIT, amount=D(amount),
#                          description="Opening A/R"),
#         TransactionModel(journal_entry=je, account=equity,
#                          tx_type=TransactionModel.CREDIT, amount=D(amount),
#                          description="Opening balance equity"),
#     ])
#     # Mirror the running balance used by your app
#     customer.current_balance = (customer.current_balance or D('0')) + D(amount)
#     customer.save(update_fields=['current_balance'])
