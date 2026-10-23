"""Explicit transition tables. Anything not listed is illegal (HTTP 409)."""

SHIPMENT_TRANSITIONS: dict[str, set[str]] = {
    "CREATED": {"IN_TRANSIT", "ON_HOLD"},
    "IN_TRANSIT": {"IN_TRANSIT", "AWAITING_CONSUMER", "ON_HOLD"},
    "AWAITING_CONSUMER": {"ACCEPTED", "DISPUTED", "ON_HOLD"},
    # ON_HOLD: the stage owner may fix it themselves (hold cleared) or escalate to a reviewer.
    "ON_HOLD": {"UNDER_REVIEW", "IN_TRANSIT", "AWAITING_CONSUMER"},
    # UNDER_REVIEW: a reviewer sends it back for a recheck, resumes it, or closes it.
    "UNDER_REVIEW": {"ON_HOLD", "IN_TRANSIT", "AWAITING_CONSUMER", "RESOLVED_REFUND", "RESOLVED_REJECTED",
                     "RESOLVED_REPLACEMENT", "RETURNED"},
    "DISPUTED": {"UNDER_REVIEW"},
    "ACCEPTED": {"DISPUTED"},      # a complaint after delivery: wrong item found when the buyer opens the parcel
    "RESOLVED_REFUND": set(),
    "RESOLVED_REJECTED": set(),
    "RESOLVED_REPLACEMENT": set(),
    "RETURNED": set(),
}

CHECKPOINT_TRANSITIONS: dict[str, set[str]] = {
    "PENDING": {"IN_PROGRESS"},
    "IN_PROGRESS": {"LOCKED", "FAILED"},
    "FAILED": {"IN_PROGRESS"},  # only a reviewer decision reopens a failed stage
    "LOCKED": set(),            # forever
}

PAYMENT_TRANSITIONS: dict[str, set[str]] = {
    "PENDING": {"RESERVED", "DISPUTE_HOLD", "CANCELLED"},   # pay-on-delivery from the wallet: nothing moves until the door
    "RESERVED": {"RELEASED", "PENDING"},                    # buyer paid, waiting for the handover code; PENDING = reversed
    "DISPUTE_HOLD": {"RELEASED", "CANCELLED"},
    "RELEASED": {"REFUNDED"},                               # a complaint upheld after delivery: money goes back to the buyer
    "REFUNDED": set(),
    "CANCELLED": set(),
}

ROLE_ORDER = ["SELLER", "WAREHOUSE", "DELIVERY", "CONSUMER"]

REQUIRED_EVIDENCE = {
    "SELLER": ["product_front", "product_side", "seal_side", "packing_video"],  # the seal is read and registered before the packing video
    "WAREHOUSE": ["seal_side"],
    "DELIVERY": ["seal_side", "parcel"],
    "CONSUMER": ["seal_side"],      # at the door only the seal is checked; the product itself is checked after delivery (unboxing)
}


class IllegalTransition(Exception):
    pass


def check(table: dict[str, set[str]], current: str, target: str, what: str) -> None:
    if target not in table.get(current, set()):
        raise IllegalTransition(f"{what}: cannot go from {current} to {target}")

MAX_ATTEMPTS = 3  # tries the stage owner gets to fix a failed check before it goes to a reviewer
