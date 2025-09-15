from voucher.models import Voucher, VoucherEntry, VoucherType,ChartOfAccount
from finance.models import FinancialYear
from django.contrib.auth import get_user_model
from decimal import Decimal
from setting.constants import TAX_PAYABLE_ACCOUNT_CODE, TAX_RECEIVABLE_ACCOUNT_CODE
User = get_user_model()




def build_composite_purchase_return_entries(
    *,
    purchase_return_account,         # COA (contra expense): e.g. 5102 Purchase Returns
    supplier_account,                # COA (A/P) for supplier
    cash_or_bank_account,            # COA Cash/Bank when supplier refunds immediately
    total_amount: Decimal,           # gross return value (incl. tax)
    tax: Decimal,                    # tax portion to reverse (input tax)
    refund_now: bool,                # True => DR cash/bank ; False => DR A/P (credit note)
):
    """
    DR Cash/Bank (refund) OR DR A/P (credit note)
    CR Purchase Returns (net), CR Input Tax (tax)  -> reverses purchase
    """
    total_amount = Decimal(total_amount or 0)
    tax = Decimal(tax or 0)
    net = total_amount - tax
    if net < 0 or tax < 0:
        raise ValueError("Amounts cannot be negative.")

    entries = []

    # Debit side: either receive cash back or reduce the supplier liability
    dr_row = {
        "account": cash_or_bank_account if refund_now else supplier_account,
        "debit": total_amount,
        "credit": 0,
        "remarks": "Supplier cash refund" if refund_now else "Supplier credit note",
    }
    entries.append(dr_row)

    # Credits: reverse expense and input tax (asset)
    entries.append({"account": purchase_return_account, "debit": 0, "credit": net, "remarks": "Purchase return (net)"})
    if tax > 0:
        tax_in = ChartOfAccount.objects.get(code=TAX_RECEIVABLE_ACCOUNT_CODE)
        entries.append({"account": tax_in, "debit": 0, "credit": tax, "remarks": "Reverse input tax"})

    # Balance check
    dr = sum(Decimal(e["debit"]) for e in entries)
    cr = sum(Decimal(e["credit"]) for e in entries)
    if dr != cr:
        raise ValueError(f"Purchase return entries not balanced (DR={dr} CR={cr})")
    return entries


def post_composite_purchase_return_voucher(
    *,
    date,
    return_no: str,
    total_amount: Decimal,
    tax: Decimal,
    purchase_return_account: ChartOfAccount,
    supplier_account: ChartOfAccount,
    cash_or_bank_account: ChartOfAccount | None,
    refund_now: bool,
    created_by=None,
    branch=None,
):
    """Creates one voucher (type 'PRN' – Purchase Return) with balanced lines."""
    vt, _ = VoucherType.objects.get_or_create(code="PRN", defaults={"name": "Purchase Return"})
    entries = build_composite_purchase_return_entries(
        purchase_return_account=purchase_return_account,
        supplier_account=supplier_account,
        cash_or_bank_account=cash_or_bank_account,
        total_amount=total_amount,
        tax=tax,
        refund_now=refund_now,
    )
    v = Voucher.create_with_entries(
        voucher_type=vt,
        date=date,
        narration=f"Auto-voucher (composite) for Purchase Return {return_no}",
        created_by=created_by,
        entries=entries,
        branch=branch,
    )
    return v


def build_composite_sales_return_entries(
    *,
    sales_return_account,          # COA (contra revenue): e.g. 4102 Sales Returns
    customer_account,              # COA (A/R) of customer
    cash_or_bank_account,          # COA Cash/Bank when refunding
    total_amount: Decimal,         # gross return value (incl. tax)
    tax: Decimal,                  # tax portion being reversed
    refund_now: bool,              # True => CR cash/bank; False => CR A/R (credit note)
):
    """
    DR Sales Returns (net), DR Output Tax (tax), CR Cash/Bank or CR A/R (gross).
    """
    total_amount = Decimal(total_amount or 0)
    tax = Decimal(tax or 0)
    net = total_amount - tax
    if net < 0 or tax < 0:
        raise ValueError("Amounts cannot be negative.")

    entries = []
    # Debits (reverse revenue & tax payable)
    entries.append({"account": sales_return_account, "debit": net, "credit": 0, "remarks": "Sales return (net)"})
    if tax > 0:
        tax_out = ChartOfAccount.objects.get(code=TAX_PAYABLE_ACCOUNT_CODE)
        entries.append({"account": tax_out, "debit": tax, "credit": 0, "remarks": "Reverse output tax"})

    # Credit either Cash/Bank (refund) or A/R (credit note)
    credit_row = {
        "account": cash_or_bank_account if refund_now else customer_account,
        "debit": 0,
        "credit": total_amount,
        "remarks": "Cash refund" if refund_now else "Credit note to customer",
    }
    entries.append(credit_row)

    # Balance check
    dr = sum(Decimal(e["debit"]) for e in entries)
    cr = sum(Decimal(e["credit"]) for e in entries)
    if dr != cr:
        raise ValueError(f"Sales return entries not balanced (DR={dr} CR={cr})")
    return entries


def post_composite_sales_return_voucher(
    *,
    date,
    return_no: str,
    total_amount: Decimal,
    tax: Decimal,
    sales_return_account: ChartOfAccount,
    customer_account: ChartOfAccount,
    cash_or_bank_account: ChartOfAccount | None,
    refund_now: bool,
    created_by=None,
    branch=None,
):
    """
    Creates one voucher (type 'SRN' – Sale Return) with balanced lines.
    """
    vt, _ = VoucherType.objects.get_or_create(code="SRN", defaults={"name": "Sale Return"})
    entries = build_composite_sales_return_entries(
        sales_return_account=sales_return_account,
        customer_account=customer_account,
        cash_or_bank_account=cash_or_bank_account,
        total_amount=total_amount,
        tax=tax,
        refund_now=refund_now,
    )
    v = Voucher.create_with_entries(
        voucher_type=vt,
        date=date,
        narration=f"Auto-voucher (composite) for Sale Return {return_no}",
        created_by=created_by,
        entries=entries,
        branch=branch,
    )
    return v

def build_composite_purchase_entries(
    *,
    purchase_account,           # COA for inventory/purchases
    supplier_account,           # COA for supplier (for credit portion)
    cash_or_bank_account,       # COA for cash/bank (for cash portion)
    grand_total: Decimal,
    tax: Decimal,               # tax amount
    paid_amount: Decimal,       # cash portion (0 if pure credit)
):
    """
    Returns a balanced list of voucher rows for a composite posting.
    - DR Purchase (net)
    - DR Input Tax (if any)
    - CR Supplier (grand_total - paid_amount)
    - CR Cash/Bank (paid_amount)
    """
    paid_amount = Decimal(paid_amount or 0)
    tax = Decimal(tax or 0)
    grand_total = Decimal(grand_total)

    if paid_amount < 0 or paid_amount > grand_total:
        raise ValueError("paid_amount must be between 0 and grand_total.")

    entries = []
    net_amount = grand_total - tax  # debit to purchases/inventory

    # Debits
    entries.append({"account": purchase_account, "debit": net_amount, "credit": 0, "remarks": "Purchase (net)"})
    if tax > 0:
        tax_account = ChartOfAccount.objects.get(code=TAX_RECEIVABLE_ACCOUNT_CODE)
        entries.append({"account": tax_account, "debit": tax, "credit": 0, "remarks": "Input Tax"})

    # Credits
    supplier_credit = grand_total - paid_amount
    if supplier_credit > 0:
        entries.append({"account": supplier_account, "debit": 0, "credit": supplier_credit, "remarks": "Credit to Supplier"})

    if paid_amount > 0:
        if not cash_or_bank_account:
            raise ValueError("Cash/Bank account required when paid_amount > 0.")
        entries.append({"account": cash_or_bank_account, "debit": 0, "credit": paid_amount, "remarks": "Cash/Bank payment"})

    # Sanity check
    total_dr = sum(Decimal(e["debit"]) for e in entries)
    total_cr = sum(Decimal(e["credit"]) for e in entries)
    if total_dr != total_cr:
        raise ValueError(f"Composite entries not balanced (DR={total_dr} CR={total_cr})")

    return entries


def post_composite_purchase_voucher(
    *,
    date,
    invoice_no: str,
    grand_total: Decimal,
    tax: Decimal,
    paid_amount: Decimal,
    purchase_account: ChartOfAccount,
    supplier_account: ChartOfAccount,
    cash_or_bank_account: ChartOfAccount | None,
    created_by=None,
    branch=None,
):
    """
    Creates one voucher (type 'PUR') with all entries combined.
    """
    vt, _ = VoucherType.objects.get_or_create(code="PUR", defaults={"name": "Purchase"})
    entries = build_composite_purchase_entries(
        purchase_account=purchase_account,
        supplier_account=supplier_account,
        cash_or_bank_account=cash_or_bank_account,
        grand_total=grand_total,
        tax=tax,
        paid_amount=paid_amount,
    )
    v = Voucher.create_with_entries(
        voucher_type=vt,
        date=date,
        narration=f"Auto-voucher (composite) for Purchase Invoice {invoice_no}",
        created_by=created_by,
        entries=entries,
        branch=branch,
    )
    return v

def build_composite_sales_entries(
    *,
    sales_account,               # COA (income) for sales
    customer_account,            # COA (A/R) for customer (for credit portion)
    cash_or_bank_account,        # COA for cash/bank (for cash portion)
    grand_total: Decimal,
    tax: Decimal,                # tax amount
    paid_amount: Decimal,        # cash portion (0 if pure credit)
):
    """
    Returns balanced voucher rows for a composite sales posting.
      DR Cash/Bank (paid_amount)
      DR Customer (grand_total - paid_amount)
      CR Sales (grand_total - tax)
      CR Output Tax (tax)
    """
    paid_amount = Decimal(paid_amount or 0)
    tax = Decimal(tax or 0)
    grand_total = Decimal(grand_total)

    if paid_amount < 0 or paid_amount > grand_total:
        raise ValueError("paid_amount must be between 0 and grand_total.")

    entries = []

    # Credits first (income + tax)
    net_sales = grand_total - tax
    entries.append({"account": sales_account, "debit": 0, "credit": net_sales, "remarks": "Sales (net)"})
    if tax > 0:
        tax_out = ChartOfAccount.objects.get(code=TAX_PAYABLE_ACCOUNT_CODE)
        entries.append({"account": tax_out, "debit": 0, "credit": tax, "remarks": "Output Tax"})

    # Debits split between cash and receivable
    if paid_amount > 0:
        if not cash_or_bank_account:
            raise ValueError("Cash/Bank account required when paid_amount > 0.")
        entries.append({"account": cash_or_bank_account, "debit": paid_amount, "credit": 0, "remarks": "Cash/Bank receipt"})

    ar_amount = grand_total - paid_amount
    if ar_amount > 0:
        entries.append({"account": customer_account, "debit": ar_amount, "credit": 0, "remarks": "Accounts Receivable"})

    # Sanity check
    total_dr = sum(Decimal(e["debit"]) for e in entries)
    total_cr = sum(Decimal(e["credit"]) for e in entries)
    if total_dr != total_cr:
        raise ValueError(f"Composite sales entries not balanced (DR={total_dr} CR={total_cr})")

    return entries


def post_composite_sales_voucher(
    *,
    date,
    invoice_no: str,
    grand_total: Decimal,
    tax: Decimal,
    paid_amount: Decimal,
    sales_account: ChartOfAccount,
    customer_account: ChartOfAccount,
    cash_or_bank_account: ChartOfAccount | None,
    created_by=None,
    branch=None,
):
    """
    Creates one voucher (type 'SAL') with all sales entries combined.
    """
    vt, _ = VoucherType.objects.get_or_create(code="SAL", defaults={"name": "Sale"})
    entries = build_composite_sales_entries(
        sales_account=sales_account,
        customer_account=customer_account,
        cash_or_bank_account=cash_or_bank_account,
        grand_total=grand_total,
        tax=tax,
        paid_amount=paid_amount,
    )
    v = Voucher.create_with_entries(
        voucher_type=vt,
        date=date,
        narration=f"Auto-voucher (composite) for Sale Invoice {invoice_no}",
        created_by=created_by,
        entries=entries,
        branch=branch,
    )
    return v


def create_voucher_for_transaction(
    *,
    voucher_type_code,
    date,
    amount,
    narration,
    debit_account,
    credit_account,
    created_by=None,
    branch=None,
    financial_year=None,
):
    """
    Creates a voucher and two entries (debit & credit) for double-entry accounting.

    Params:
        - voucher_type_code (e.g., 'PUR', 'SAL')
        - date
        - amount
        - narration
        - debit_account (ChartOfAccount instance)
        - credit_account (ChartOfAccount instance)
        - created_by (User)
        - branch (optional)

    Returns: Voucher instance
    """

    voucher_type = VoucherType.objects.get(code=voucher_type_code)
    financial_year = financial_year or FinancialYear.get_active()

    voucher = Voucher.objects.create(
        voucher_type=voucher_type,
        date=date,
        amount=amount,
        narration=narration,
        created_by=created_by or User.objects.first(),
        branch=branch,
        status='PENDING',
        financial_year=financial_year,
    )

    VoucherEntry.objects.create(
        voucher=voucher,
        account=debit_account,
        debit=amount,
        credit=0,
        remarks=f"Debit {debit_account.name}"
    )

    VoucherEntry.objects.create(
        voucher=voucher,
        account=credit_account,
        debit=0,
        credit=amount,
        remarks=f"Credit {credit_account.name}"
    )

    return voucher
