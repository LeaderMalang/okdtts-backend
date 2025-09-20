from inventory.models import Batch, StockMovement
from django.utils.timezone import now
from django.core.exceptions import ValidationError
import logging
from django.db import transaction
logger = logging.getLogger(__name__)

LOW_STOCK_THRESHOLD = 5  # you can make this configurable


def _warn_low_stock(batch):
    if batch.quantity < LOW_STOCK_THRESHOLD:
        logger.warning(f"Low stock: {batch.product.name} in batch {batch.batch_number} (qty={batch.quantity})")



@transaction.atomic
def stock_out_exact_batch(*, product, batch_number, quantity, warehouse, reason="", allow_underflow=False):
    """
    Strictly remove from the specified batch in the specified warehouse.
    Used for: Purchase Invoice cancellation, Purchase Return, GRN reversal.
    """
    qty = int(quantity)
    if qty <= 0:
        raise ValidationError("Quantity must be > 0")

    # Lock the row to avoid race conditions
    try:
        batch = (Batch.objects
                 .select_for_update()
                 .get(product=product, warehouse=warehouse, batch_number=batch_number))
    except Batch.DoesNotExist:
        raise ValidationError(f"Batch not found for product={product} batch={batch_number} warehouse={warehouse}")

    if not allow_underflow and batch.quantity < qty:
        raise ValidationError(
            f"Insufficient qty in batch {batch.batch_number}: have {batch.quantity}, need {qty}"
        )

    # Apply change (allow_underflow only for forced administrative reversals)
    batch.quantity = batch.quantity - qty
    batch.save(update_fields=["quantity"])

    _warn_low_stock(batch)

    StockMovement.objects.create(
        batch=batch,
        movement_type="OUT",
        quantity=qty,
        reason=reason or f"Stock-out exact batch {batch.batch_number}",
        timestamp=now()
    )
    return batch


@transaction.atomic
def stock_out_multi(*, items, warehouse, reason="", allow_underflow=False):
    """
    Stock-out multiple exact batches in one transaction.
    items = [(product, batch_number, qty), ...]
    """
    updated = []
    for product, batch_number, qty in items:
        b = stock_out_exact_batch(
            product=product,
            batch_number=batch_number,
            quantity=qty,
            warehouse=warehouse,
            reason=reason,
            allow_underflow=allow_underflow,
        )
        updated.append(b)
    return updated


@transaction.atomic
def stock_out_new(product, quantity, reason="", warehouse=None, batch_number=None, allow_underflow=False):
    """
    Backward-compatible API:
      - If batch_number provided => exact batch stock-out (strict)
      - Else => legacy FEFO (expiry-date order) stock-out (single-batch)
    NOTE: For PI cancel / Purchase Return ALWAYS pass batch_number (+ warehouse).
    """
    qty = int(quantity)
    if qty <= 0:
        raise ValidationError("Quantity must be > 0")

    if batch_number:
        if warehouse is None:
            raise ValidationError("warehouse is required when using batch_number")
        return stock_out_exact_batch(
            product=product,
            batch_number=batch_number,
            quantity=qty,
            warehouse=warehouse,
            reason=reason,
            allow_underflow=allow_underflow,
        )

    # ---- Legacy FEFO path (only when no batch is specified) ----
    qs = Batch.objects.filter(product=product)
    if warehouse is not None:
        qs = qs.filter(warehouse=warehouse)
    # Lock a candidate row
    batches = qs.select_for_update().filter(quantity__gte=qty).order_by("expiry_date")

    if not batches.exists():
        logger.error(f"Out of stock: {product.name} (need {qty})")
        raise ValidationError(f"Insufficient stock for {product.name}")

    batch = batches.first()
    batch.quantity -= qty
    if not allow_underflow and batch.quantity < 0:
        raise ValidationError(f"Underflow on batch {batch.batch_number}")
    batch.save(update_fields=["quantity"])

    _warn_low_stock(batch)

    StockMovement.objects.create(
        batch=batch,
        movement_type="OUT",
        quantity=qty,
        reason=reason or "Stock-out FEFO",
        timestamp=now()
    )
    return batch
# Stock In
def stock_in(product, quantity, batch_number, expiry_date, purchase_price, sale_price, reason,warehouse):
    # Check for duplicate batch
    if Batch.objects.filter(product=product, batch_number=batch_number).exists():
        raise ValidationError(f"Batch {batch_number} for {product.name} already exists.")

    if expiry_date < now().date():
        logger.warning(f"Attempt to stock expired batch: {batch_number} for {product.name}")

    batch = Batch.objects.create(
        product=product,
        batch_number=batch_number,
        expiry_date=expiry_date,
        purchase_price=purchase_price,
        sale_price=sale_price,
        quantity=quantity,
        warehouse=warehouse,
    )

    StockMovement.objects.create(
        batch=batch,
        movement_type='IN',
        quantity=quantity,
        reason=reason,
        timestamp=now()
    )
    return batch

# Stock Out (for Sale or Return)
def stock_out(product, quantity, reason):
    batches = Batch.objects.filter(product=product, quantity__gte=quantity).order_by('expiry_date')

    if not batches.exists():
        logger.error(f"Out of stock: {product.name}")
        raise ValidationError(f"Insufficient stock for {product.name}")

    batch = batches.first()
    batch.quantity -= quantity
    batch.save()

    if batch.quantity < LOW_STOCK_THRESHOLD:
        logger.warning(f"Low stock alert: {product.name} in batch {batch.batch_number}")

    StockMovement.objects.create(
        batch=batch,
        movement_type='OUT',
        quantity=quantity,
        reason=reason,
        timestamp=now()
    )
    return batch

# Return Handling (adds stock back)
def stock_return(product, quantity, batch_number, reason):
    try:
        batch = Batch.objects.get(product=product, batch_number=batch_number)
    except Batch.DoesNotExist:
        raise ValidationError(f"Batch {batch_number} not found for return.")

    batch.quantity += quantity
    batch.save()

    StockMovement.objects.create(
        batch=batch,
        movement_type='IN',
        quantity=quantity,
        reason=f"Return: {reason}",
        timestamp=now()
    )
    return batch
