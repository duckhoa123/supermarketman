"""Card payments through Stripe, so card numbers never touch our servers.

Without STRIPE_SECRET_KEY nothing is charged: orders are marked "test" so the whole flow can be tried.
With a key, a PaymentIntent is created and its client_secret is returned to the app, which confirms the
payment with Stripe's own payment sheet; a Stripe webhook (to add when going live) marks it paid.
"""

from app.config import get_settings
from app.models import CustomerOrder, User
from app.services.notify import http

STRIPE_API = "https://api.stripe.com/v1/payment_intents"


async def _intent(amount_cents: int, currency: str, metadata: dict, email: str | None) -> dict:
    key = get_settings().stripe_secret_key
    form = {"amount": str(amount_cents), "currency": currency.lower(),
            "automatic_payment_methods[enabled]": "true"}
    if email:
        form["receipt_email"] = email
    for k, v in metadata.items():
        form[f"metadata[{k}]"] = str(v)
    resp = await http().post(STRIPE_API, data=form, auth=(key, ""))
    resp.raise_for_status()
    body = resp.json()
    return {"status": "requires_payment", "ref": body["id"], "client_secret": body["client_secret"]}


async def charge(order: CustomerOrder, user: User, currency: str = "CAD") -> dict:
    if not get_settings().stripe_secret_key:
        return {"status": "test", "ref": None, "client_secret": None,
                "message": "Test mode: no card was charged."}
    return await _intent(order.total_cents, currency, {"customer_order_id": order.id, "kind": "order"}, user.email)


async def charge_tip(order: CustomerOrder, user: User, amount_cents: int, currency: str = "CAD") -> dict:
    if not get_settings().stripe_secret_key:
        return {"status": "test", "ref": None, "client_secret": None}
    return await _intent(amount_cents, currency, {"customer_order_id": order.id, "kind": "tip"}, user.email)
