"""Distances, travel times, delivery fees and opening hours. Pure functions, no third-party imports.

Road distance is estimated as straight-line distance x ROAD_FACTOR, which is close enough for fees and
ETAs inside one city. Swap in a routing API later if drivers need turn-by-turn accuracy.
"""

import math
from dataclasses import dataclass
from datetime import datetime

EARTH_KM = 6371.0
ROAD_FACTOR = 1.3
# Average door-to-door speeds in city traffic, including lights and stops.
SPEED_KMH = {"e_bike": 18.0, "bike": 15.0, "car": 22.0}
HANDOFF_MIN = 4  # parking/locking up and handing over, at each end
BC_FEE_CAP_BP = 2000  # BC: delivery companies may charge a restaurant at most 20% of the order value
WEEKDAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(a))


def road_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    return round(haversine_km(lat1, lng1, lat2, lng2) * ROAD_FACTOR, 2)


def travel_minutes(km: float, vehicle: str = "e_bike") -> int:
    return max(1, math.ceil(km / SPEED_KMH.get(vehicle, SPEED_KMH["e_bike"]) * 60))


def delivery_fee_cents(km: float, base_cents: int, base_km: float, per_km_cents: int) -> int:
    """Flat fee up to base_km, then per started km beyond it."""
    extra = max(0.0, km - base_km)
    return base_cents + math.ceil(round(extra, 6)) * per_km_cents


def restaurant_fee_cents(subtotal_cents: int, fee_bp: int) -> int:
    """The platform's fee to the restaurant, never above BC's 20% cap."""
    return round(subtotal_cents * min(fee_bp, BC_FEE_CAP_BP) / 10_000)


def within_km(lat: float, lng: float, center_lat: float, center_lng: float, radius_km: float) -> bool:
    return haversine_km(lat, lng, center_lat, center_lng) <= radius_km


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def is_open(hours: dict | None, local: datetime) -> bool | None:
    """True/False from an opening-hours dict ({"mon": [["11:30","22:00"]], ...}); None if hours are unknown.

    A range whose closing time is before its opening time runs past midnight (e.g. ["18:00", "02:00"]).
    """
    if not hours:
        return None
    now = local.hour * 60 + local.minute
    today = WEEKDAY_KEYS[local.weekday()]
    yesterday = WEEKDAY_KEYS[(local.weekday() - 1) % 7]
    for start, end in hours.get(today, []):
        s, e = _minutes(start), _minutes(end)
        if (s <= now < e) if e > s else (now >= s):
            return True
    for start, end in hours.get(yesterday, []):
        s, e = _minutes(start), _minutes(end)
        if e <= s and now < e:  # yesterday's late range still running
            return True
    return False


# --- Dispatch -------------------------------------------------------------------------------------


@dataclass
class DriverState:
    id: str
    lat: float
    lng: float
    vehicle: str = "e_bike"
    active: int = 0  # deliveries assigned or picked up, not yet delivered
    heading_to: tuple[str, ...] = ()  # location ids of pickups already assigned but not collected


def choose_driver(pickup_location_id: str, pickup_lat: float, pickup_lng: float, drivers: list[DriverState],
                  max_active: int, max_pickup_km: float) -> tuple[DriverState, str] | None:
    """Pick a driver for a delivery that is ready for pickup.

    1. A driver already on the way to the same restaurant takes it too (one trip, two orders).
    2. Otherwise the nearest driver with spare capacity, preferring those with fewer active jobs.
    Returns (driver, reason) or None when nobody is free.
    """
    free = [d for d in drivers if d.active < max_active]
    batch = [d for d in free if pickup_location_id in d.heading_to]
    if batch:
        best = min(batch, key=lambda d: haversine_km(d.lat, d.lng, pickup_lat, pickup_lng))
        return best, "batched with another order from the same restaurant"
    near = [(haversine_km(d.lat, d.lng, pickup_lat, pickup_lng), d) for d in free]
    near = [(km, d) for km, d in near if km <= max_pickup_km]
    if not near:
        return None
    km, best = min(near, key=lambda x: (x[1].active, x[0]))
    return best, f"nearest free driver, {km:.1f} km from the restaurant"


def delivery_eta_minutes(driver_to_pickup_km: float, trip_km: float, vehicle: str, prep_left_min: int = 0) -> int:
    """Minutes until the food reaches the customer: the driver gets to the restaurant (or waits for the food,
    whichever is longer), then rides to the customer."""
    to_pickup = travel_minutes(driver_to_pickup_km * ROAD_FACTOR, vehicle) if driver_to_pickup_km > 0.05 else 0
    return max(to_pickup, prep_left_min) + HANDOFF_MIN + travel_minutes(trip_km, vehicle) + HANDOFF_MIN
