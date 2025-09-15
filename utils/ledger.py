# from datetime import datetime, time
# from typing import Iterable, Optional

# from django.utils import timezone
# from django_ledger.models import (
#     AccountModel,
#     EntityModel,
#     JournalEntryModel,
#     LedgerModel,
#     TransactionModel,
# )


# def get_or_create_default_ledger() -> Optional[LedgerModel]:
#     """Return the first ledger or create a default one if none exist."""
#     ledger = LedgerModel.objects.filter(name="Default1").first()
#     if ledger:
#         return ledger
#     entity = EntityModel.objects.first()
#     if not entity:
#         return None
#     return LedgerModel.objects.create(name="Default1", entity=entity)


# def create_journal_entry(date, description: str, transactions: Iterable[dict]):
#     """Create a journal entry with the given transactions."""
#     ledger = get_or_create_default_ledger()
#     if not ledger:
#         return None
#     timestamp = timezone.make_aware(datetime.combine(date, time.min))
#     je = JournalEntryModel.objects.create(
#         ledger=ledger,
#         timestamp=timestamp,
#         description=description,
#     )
#     tx_objs = []
#     for tx in transactions:
#         tx_objs.append(
#             TransactionModel(
#                 journal_entry=je,
#                 account=tx["account"],
#                 tx_type=tx["type"],
#                 amount=tx["amount"],
#                 description=tx.get("description", ""),
#             )
#         )
#     TransactionModel.objects.bulk_create(tx_objs)
#     return je


# def post_simple_entry(date, amount, narration, debit_account, credit_account):
#     """Post a simple double-entry transaction."""
#     return create_journal_entry(
#         date,
#         narration,
#         [
#             {"account": debit_account, "type": TransactionModel.DEBIT, "amount": amount},
#             {"account": credit_account, "type": TransactionModel.CREDIT, "amount": amount},
#         ],
#     )


# def post_composite_sale(
#     date,
#     customer_account,
#     sales_account,
#     amount,
#     tax=0,
#     paid_amount=0,
#     cash_account=None,
#     narration="Sale",
# ):
#     """Post ledger entries for a sale invoice."""
#     transactions = [
#         {
#             "account": customer_account,
#             "type": TransactionModel.DEBIT,
#             "amount": amount,
#             "description": "Sale",
#         },
#         {
#             "account": sales_account,
#             "type": TransactionModel.CREDIT,
#             "amount": amount - tax,
#             "description": "Revenue",
#         },
#     ]
#     if tax:
#         tax_account = AccountModel.objects.filter(code="TAX_PAYABLE").first()
#         if tax_account:
#             transactions.append(
#                 {
#                     "account": tax_account,
#                     "type": TransactionModel.CREDIT,
#                     "amount": tax,
#                     "description": "Tax",
#                 }
#             )
#     if paid_amount and cash_account:
#         transactions.append(
#             {
#                 "account": cash_account,
#                 "type": TransactionModel.DEBIT,
#                 "amount": paid_amount,
#                 "description": "Payment",
#             }
#         )
#         transactions.append(
#             {
#                 "account": customer_account,
#                 "type": TransactionModel.CREDIT,
#                 "amount": paid_amount,
#                 "description": "Payment",
#             }
#         )
#     return create_journal_entry(date, narration, transactions)


# def post_composite_purchase(
#     date,
#     supplier_account,
#     purchase_account,
#     amount,
#     tax=0,
#     paid_amount=0,
#     cash_account=None,
#     narration="Purchase",
# ):
#     """Post ledger entries for a purchase invoice."""
#     transactions = [
#         {
#             "account": purchase_account,
#             "type": TransactionModel.DEBIT,
#             "amount": amount - tax,
#             "description": "Purchase",
#         }
#     ]
#     if tax:
#         tax_account = AccountModel.objects.filter(code="TAX_RECEIVABLE").first()
#         if tax_account:
#             transactions.append(
#                 {
#                     "account": tax_account,
#                     "type": TransactionModel.DEBIT,
#                     "amount": tax,
#                     "description": "Tax",
#                 }
#             )
#     outstanding = amount - paid_amount
#     if supplier_account and outstanding > 0:
#         transactions.append(
#             {
#                 "account": supplier_account,
#                 "type": TransactionModel.CREDIT,
#                 "amount": outstanding,
#                 "description": "Payable",
#             }
#         )
#     if paid_amount and cash_account:
#         transactions.append(
#             {
#                 "account": cash_account,
#                 "type": TransactionModel.CREDIT,
#                 "amount": paid_amount,
#                 "description": "Payment",
#             }
#         )
#     return create_journal_entry(date, narration, transactions)


# def post_payroll_entry(date, expense_account, payment_account, amount, narration="Payroll"):
#     """Post a payroll journal entry."""
#     return create_journal_entry(
#         date,
#         narration,
#         [
#             {
#                 "account": expense_account,
#                 "type": TransactionModel.DEBIT,
#                 "amount": amount,
#                 "description": "Payroll Expense",
#             },
#             {
#                 "account": payment_account,
#                 "type": TransactionModel.CREDIT,
#                 "amount": amount,
#                 "description": "Payroll Payment",
#             },
#         ],
#     )


# __all__ = [
#     "get_or_create_default_ledger",
#     "create_journal_entry",
#     "post_simple_entry",
#     "post_composite_sale",
#     "post_composite_purchase",
#     "post_payroll_entry",
# ]
