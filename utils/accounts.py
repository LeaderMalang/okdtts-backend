# # utils/accounts.py
# from django_ledger.models import AccountModel
# from inventory.models import Party

# def ensure_customer_account(customer: Party):
#     from inventory.management.commands.init_customer_accounts import (
#         _ensure_customer_ar_account,
#         _ensure_opening_balance_equity,
#     )
#     _ensure_opening_balance_equity()  # make sure 3999 exists at least once
#     acct, _ = _ensure_customer_ar_account(customer)
#     return acct
