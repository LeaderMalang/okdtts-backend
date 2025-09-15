from inventory.models import Batch, StockMovement
from django.utils.timezone import now
from django.core.exceptions import ValidationError
import logging

logger = logging.getLogger(__name__)

LOW_STOCK_THRESHOLD = 5  # you can make this configurable

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
