from decimal import Decimal
from voucher.models import VoucherEntry


def ledger_postings(voucher):
    """Return a mapping of account id to (debit, credit) for a voucher."""
    return {
        entry.account_id: (entry.debit, entry.credit)
        for entry in VoucherEntry.objects.filter(voucher=voucher)
    }


def assert_ledger_entries(testcase, voucher, expected):
    """Assert voucher has expected ledger postings.

    ``expected`` should be an iterable of tuples:
    ``(account, debit, credit)`` where amounts can be ``Decimal`` or numeric.
    """
    postings = ledger_postings(voucher)
    testcase.assertEqual(
        len(postings), len(expected),
        msg="Number of voucher entries does not match expected"
    )
    for account, debit, credit in expected:
        entry = postings.get(account.id)
        testcase.assertIsNotNone(entry, f"No entry for account {account}")
        testcase.assertEqual(entry[0], Decimal(debit))
        testcase.assertEqual(entry[1], Decimal(credit))
