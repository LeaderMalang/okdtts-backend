from decimal import Decimal
from django.db import transaction
from hordak.models import Account, Transaction, Leg
from moneyed import Money
from django.utils import timezone
from django.conf import settings
from contextlib import contextmanager



# You can keep these codes in settings
OPENING_EQUITY_CODE = 11 # Equity
PURCHASE_ACCT_CODE      = 15  # Expense/COGS or Inventory, per your COA
SALES_ACCT_CODE         = 13
SALES_RETURN_ACCT_CODE  = 16  # Sales Returns & Allowances (Income contra) or Expense, your choice
PUR_TAX_REC_CODE        = 6 # Input tax (Asset)
SAL_TAX_PAY_CODE        = 9  # Output tax (Liability)
AR_CODE                 = 4  # Accounts Receivable (Asset)
AP_CODE                 = 8  # Accounts Payable  (Liability)
CASH_CODE               = 2  # Cash (Asset)
BANK_CODE               = 3  # Bank (Asset)
DEFAULT_CCY = getattr(settings, "DEFAULT_CURRENCY", "PKR")


def as_money(value: Decimal, account: Account | None = None) -> Money:
    """
    Ensure value is a Money instance using the account's currency if available,
    otherwise the global HORDAK_DEFAULT_CURRENCY.
    """
    ccy = getattr(account, "currency", None) or DEFAULT_CCY
    return Money(Decimal(value or 0), ccy)


@contextmanager
def hordak_tx(description: str, posted_at=None):
    """
    Helper to open a balancing Transaction; yields the txn so caller can attach Legs.
    """
    txn = Transaction.objects.create(
        description=description,
        date=posted_at or timezone.now().date(),
    )
    try:
        yield txn
        # Hordak will validate balancing on save of Legs/Transaction
    except Exception:
        # If something goes wrong, clean up the unbalanced txn.
        txn.delete()
        raise
def _cash_or_bank(warehouse):
    # Prefer warehouse-specified; fallback to general codes
    if getattr(warehouse, "default_cash_account", None):
        return warehouse.default_cash_account  # must be a Hordak Account or wrapper with .pk
    if getattr(warehouse, "default_bank_account", None):
        return warehouse.default_bank_account
    return Account.objects.get(code=CASH_CODE)

def _acct(obj_or_code):
    if isinstance(obj_or_code, Account):
        return obj_or_code
    return Account.objects.get(id=obj_or_code)



@transaction.atomic
def post_reverse_customer_receipt_partial(*, date, description, customer_account, cash_or_bank_account, amount):
    """
    Reverse part (or all) of a posted customer receipt:
      Original receipt:
        DR Cash/Bank  amount
        CR A/R        amount
      Reversal:
        DR A/R        amount
        CR Cash/Bank  amount
    """
    amount = Decimal(amount or 0)
    if amount <= 0:
        return None
    ar = _acct(customer_account)
    cash = _acct(cash_or_bank_account)

    txn = Transaction.objects.create(date=date, description=description or "Reverse Customer Receipt")
    Leg.objects.create(transaction=txn, account=ar,   debit=as_money(amount, ar))
    Leg.objects.create(transaction=txn, account=cash, credit=as_money(amount, cash))
    return txn


@transaction.atomic
def post_cancel_sale(*, date, description, subtotal, tax, customer_account, warehouse_sales_account=None):
    """
    Reverse the accounting of a CONFIRMED sales invoice (no cash effect).
    Original confirm posted:
        DR A/R (grand)
        CR Sales (subtotal)
        CR Output Tax Payable (tax)
    This reversal posts:
        DR Sales               subtotal
        DR Tax Payable         tax
        CR A/R                 grand
    """
    subtotal = Decimal(subtotal or 0)
    tax      = Decimal(tax or 0)
    grand    = subtotal + tax
    if grand <= 0:
        return None

    sales   = _acct(warehouse_sales_account or SALES_ACCT_CODE)
    tax_pay = _acct(SAL_TAX_PAY_CODE) if tax > 0 else None
    ar      = _acct(customer_account)

    with hordak_tx(description, posted_at=date or timezone.now().date()) as txn:
        # DR Sales (reverse revenue)
        Leg.objects.create(transaction=txn, account=sales, debit=as_money(subtotal, sales))
        # DR Output VAT (reverse liability)
        if tax_pay:
            Leg.objects.create(transaction=txn, account=tax_pay, debit=as_money(tax, tax_pay))
        # CR A/R (remove receivable)
        Leg.objects.create(transaction=txn, account=ar, credit=as_money(grand, ar))
        return txn
    
@transaction.atomic
def post_ar_opening(*, date, description, customer_account, amount):
    """
    Opening receivable (customer owes us):
      DR Accounts Receivable
      CR Opening Equity
    """
    amount = Decimal(amount or 0)
    if amount <= 0:
        raise ValueError("Opening amount must be > 0")
    ar   = _acct(customer_account)
    eq   = _acct(OPENING_EQUITY_CODE)

    with hordak_tx(description, posted_at=date) as txn:
        Leg.objects.create(transaction=txn, account=ar, debit=as_money(amount, ar))
        Leg.objects.create(transaction=txn, account=eq, credit=as_money(amount, eq))
        return txn
@transaction.atomic
def post_purchase(*, date, description, total, discount=Decimal("0"), tax=Decimal("0"),
                  supplier_account, warehouse_purchase_account=None,
                  paid_amount=Decimal("0"), warehouse):
    """
    DR Purchases/Inventory (total - discount)
    DR Purchase Tax Receivable (tax)
    CR Cash/Bank (paid part, if any)
    CR Accounts Payable (outstanding)
    """
    total      = Decimal(total or 0)
    discount   = Decimal(discount or 0)
    tax        = Decimal(tax or 0)
    paid       = Decimal(paid_amount or 0)
    base       = total - discount
    grand      = base + tax
    outstanding = grand - paid

    purch_acct = _acct(warehouse_purchase_account or PURCHASE_ACCT_CODE)
    tax_rec    = _acct(PUR_TAX_REC_CODE) if tax > 0 else None
    cash_bank  = _cash_or_bank(warehouse) if paid > 0 else None
    ap         = _acct(supplier_account)
    
    # txn = Transaction.objects.create(date=date, description=description)
    with hordak_tx(description, posted_at=timezone.now()) as txn:
        # DR Purchase base
        Leg.objects.create(transaction=txn, account=purch_acct, debit=as_money(base,purch_acct) )
        # DR Input tax
        if tax_rec:
            Leg.objects.create(transaction=txn, account=tax_rec, debit=as_money(tax,tax_rec))

        # CR paid part
        if cash_bank and paid > 0:
            Leg.objects.create(transaction=txn, account=cash_bank, credit=as_money(paid,cash_bank))

        # CR A/P for outstanding
        if outstanding > 0:
            Leg.objects.create(transaction=txn, account=ap, credit=as_money(outstanding,ap))

    return txn

@transaction.atomic
def post_sale(*, date, description, subtotal, tax=Decimal("0"),
              customer_account, warehouse_sales_account=None,
              paid_amount=Decimal("0"), warehouse):
    """
    DR Cash/Bank or A/R (paid part & outstanding)
    CR Sales (subtotal)
    CR Sales Tax Payable (tax)
    """
    subtotal  = Decimal(subtotal or 0)
    tax       = Decimal(tax or 0)
    paid      = Decimal(paid_amount or 0)
    grand     = subtotal + tax
    outstanding = grand - paid

    sales     = _acct(warehouse_sales_account or SALES_ACCT_CODE)
    tax_pay   = _acct(SAL_TAX_PAY_CODE) if tax > 0 else None
    cash_bank = _cash_or_bank(warehouse) if paid > 0 else None
    ar        = _acct(customer_account)

    # txn = Transaction.objects.create(date=date, description=description)
    with hordak_tx(description, posted_at=timezone.now()) as txn:
    # DR received now (paid)
        if cash_bank and paid > 0:
            Leg.objects.create(transaction=txn, account=cash_bank, debit=as_money(paid,cash_bank))
        # DR A/R (outstanding)
        if outstanding > 0:
            Leg.objects.create(transaction=txn, account=ar, debit=as_money(outstanding,ar))

        # CR sales revenue
        Leg.objects.create(transaction=txn, account=sales, credit=as_money(subtotal,sales))
        # CR output VAT
        if tax_pay:
            Leg.objects.create(transaction=txn, account=tax_pay, credit=as_money(tax,tax_pay), is_debit=False)

        return txn

@transaction.atomic
def post_sale_return(*, date, description, amount, tax=Decimal("0"),
                     customer_account, warehouse_sales_return_account=None,
                     refund_cash=False, warehouse=None):
    """
    DR Sales Returns (amount - tax)
    DR Tax reversal (tax)
    CR Cash/Bank (if refund) else CR A/R (credit note)
    """
    amount = Decimal(amount or 0)
    tax    = Decimal(tax or 0)
    base   = amount - tax

    sales_ret = _acct(warehouse_sales_return_account or SALES_RETURN_ACCT_CODE)
    tax_pay   = _acct(SAL_TAX_PAY_CODE) if tax > 0 else None
    target    = _cash_or_bank(warehouse) if refund_cash else _acct(customer_account)

    # txn = Transaction.objects.create(date=date, description=description)
    with hordak_tx(description, posted_at=timezone.now()) as txn:
        Leg.objects.create(transaction=txn, account=sales_ret, debit=as_money(base,sales_ret))
        if tax_pay and tax > 0:
            # tax reversal: debit the liability
            Leg.objects.create(transaction=txn, account=tax_pay, debit=as_money(tax,tax_pay))
        # credit: refund or reduce receivable
        Leg.objects.create(transaction=txn, account=target, credit=as_money(amount,target))
        return txn

@transaction.atomic
def post_purchase_return(*, date, description, amount,
                         supplier_account, warehouse_purchase_account=None,
                         cash_refund=False, warehouse=None):
    """
    DR A/P (reduce payable) or DR Cash/Bank (refund received)
    CR Purchases/Inventory (return)
    """
    amount = Decimal(amount or 0)
    purch_acct = _acct(warehouse_purchase_account or PURCHASE_ACCT_CODE)
    source = _cash_or_bank(warehouse) if cash_refund else _acct(supplier_account)

    # txn = Transaction.objects.create(date=date, description=description)
    with hordak_tx(description, posted_at=timezone.now()) as txn:
        # DR: if refund cash -> Cash; else reduce A/P
        Leg.objects.create(transaction=txn, account=source, debit=as_money(amount,source))
        # CR: reduce expense/inventory
        Leg.objects.create(transaction=txn, account=purch_acct, credit=as_money(amount,purch_acct))
        return txn

@transaction.atomic
def post_customer_receipt(*, date, description, customer_account, amount, warehouse):
    """Cash received without invoice (legacy A/R)."""
    amount   = Decimal(amount)
    cash     = _cash_or_bank(warehouse)
    ar       = _acct(customer_account)
    # txn = Transaction.objects.create(date=date, description=description)
    with hordak_tx(description, posted_at=timezone.now()) as txn:
        Leg.objects.create(transaction=txn, account=cash, debit=as_money(amount,cash) )
        Leg.objects.create(transaction=txn, account=ar,   credit=as_money(amount,ar))
        return txn

@transaction.atomic
def post_supplier_payment(*, date, description, supplier_account, amount, warehouse):
    """Cash paid without invoice (legacy A/P)."""
    amount   = Decimal(amount)
    cash     = _cash_or_bank(warehouse)
    ap       = _acct(supplier_account)
    # txn = Transaction.objects.create(date=date, description=description)
    with hordak_tx(description, posted_at=timezone.now()) as txn:
        Leg.objects.create(transaction=txn, account=ap,   debit=as_money(amount,ap))
        Leg.objects.create(transaction=txn, account=cash, credit=as_money(amount,cash))
        return txn


@transaction.atomic
def post_supplier_payment_reverse(*, date, description, supplier_account, amount, warehouse):
    """Cash paid without invoice (legacy A/P)."""
    amount   = Decimal(amount)
    cash     = _cash_or_bank(warehouse)
    ap       = _acct(supplier_account)
    # txn = Transaction.objects.create(date=date, description=description)
    with hordak_tx(description, posted_at=timezone.now()) as txn:
        Leg.objects.create(transaction=txn, account=ap,   credit=as_money(amount,ap))
        Leg.objects.create(transaction=txn, account=cash, debit=as_money(amount,cash))
        return txn




@transaction.atomic
def post_customer_refund(*, date, description, customer_account, amount, warehouse):
    """
    Pay cash to customer against an existing AR credit (credit note).
    DR Accounts Receivable   amount
    CR Cash/Bank             amount
    """
    amount = Decimal(amount or 0)
    if amount <= 0:
        return
    cash = _cash_or_bank(warehouse)
    ar   = _acct(customer_account)
    with hordak_tx(description, posted_at=timezone.now()) as txn:
        Leg.objects.create(transaction=txn, account=ar,   debit=as_money(amount, ar))
        Leg.objects.create(transaction=txn, account=cash, credit=as_money(amount, cash))
        return txn