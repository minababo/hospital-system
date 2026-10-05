from django.db.models import Sum

from pharmacy.models import StockBatch


def assert_ledger_balanced():
    """The ledger invariant: for every batch, its movements add up to quantity_on_hand."""
    for batch in StockBatch.objects.annotate(total=Sum("movements__quantity")):
        assert (batch.total or 0) == batch.quantity_on_hand, (
            f"{batch}: movements sum to {batch.total}, on hand is {batch.quantity_on_hand}"
        )
