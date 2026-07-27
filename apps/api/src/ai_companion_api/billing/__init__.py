"""Billing layer — subscription purchase (Paddle WW / ЮKassa RU).

Hosted-only capability (``feature_billing and is_hosted``). The purchase is a
redirect to the provider's hosted checkout; webhooks are the single source of
truth for subscription state. See ``store.py`` for the persistence layer and
``routers/billing.py`` for the endpoints.
"""

from .store import (
    SEED_PLANS,
    BillingProfileRecord,
    BillingStore,
    InMemoryBillingStore,
    InvoiceRecord,
    PostgresBillingStore,
    make_billing_store,
)

__all__ = [
    "BillingProfileRecord",
    "BillingStore",
    "InMemoryBillingStore",
    "InvoiceRecord",
    "PostgresBillingStore",
    "SEED_PLANS",
    "make_billing_store",
]
