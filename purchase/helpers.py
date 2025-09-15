from django.db.models import Sum
def grn_returnable_map(invoice, *, exclude_pr_id=None):
    """
    {grn_item_id: remaining_qty}
    remaining = GRN.quantity - sum(PRItem.quantity on effective PRs)
    """
    from purchase.models import GoodsReceiptItem, PurchaseReturnItem

    # All POSTED GRN lines for this invoice
    grn_rows = (
        GoodsReceiptItem.objects
        .filter(invoice_item__invoice=invoice, grn__status="POSTED")
        .values("id", "quantity")
    )
    # Already returned against those GRNs
    returned_qs = PurchaseReturnItem.objects.filter(
        return_invoice__invoice=invoice,
        return_invoice__status__in={"CONFIRMED", "RETURNED", "REFUNDED", "CREDITED"},
    )
    if exclude_pr_id:
        returned_qs = returned_qs.exclude(return_invoice_id=exclude_pr_id)

    returned = returned_qs.values("grn_item_id").annotate(qty=Sum("quantity"))
    returned_map = {r["grn_item_id"]: int(r["qty"] or 0) for r in returned}

    out = {}
    for row in grn_rows:
        gid = row["id"]
        received = int(row["quantity"] or 0)
        already = int(returned_map.get(gid, 0))
        out[gid] = max(received - already, 0)
    return out
