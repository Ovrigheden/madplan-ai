"""Klient til at hente aktuelle dagligvaretilbud fra eTilbudsavis.dk (Tjek v2 API)."""

from __future__ import annotations

from typing import Any

import requests

from src.models import Offer

API_BASE = "https://api.etilbudsavis.dk/v2"
HEADERS = {
    "Origin": "https://etilbudsavis.dk",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

# Standardlokation: 7400 Herning
DEFAULT_POSTAL_CODE = "7400"
DEFAULT_LAT = 56.1393
DEFAULT_LNG = 8.9738

# Brugerens foretrukne standardkæder (navn -> dealer_id hos eTilbudsavis)
DEFAULT_STORES: dict[str, str] = {
    "REMA 1000": "11deC",
    "Netto": "9ba51",
    "365discount": "DWZE1w",
    "ABC Lavpris": "70d42L",
    "MENY": "267e1m",
    "Bilka": "93f13",
}

# Ekstra danske kæder som kan tilvælges i UI'et
EXTRA_STORES: dict[str, str] = {
    "føtex": "bdf5A",
    "Lidl": "71c90",
    "Kvickly": "c1edq",
    "SuperBrugsen": "0b1e8",
    "SPAR": "88ddE",
}

ALL_STORES: dict[str, str] = {**DEFAULT_STORES, **EXTRA_STORES}


def geocode_postal_code(postal_code: str) -> tuple[float, float, str]:
    """Slår dansk postnummer op via Dataforsyningens åbne API."""
    clean = postal_code.strip()
    if not clean or clean == DEFAULT_POSTAL_CODE:
        return DEFAULT_LAT, DEFAULT_LNG, "Herning"

    try:
        resp = requests.get(
            f"https://api.dataforsyningen.dk/postnumre/{clean}",
            timeout=6,
        )
        if resp.status_code == 200:
            data = resp.json()
            center = data.get("visueltcenter")
            name = data.get("navn", clean)
            if isinstance(center, list) and len(center) == 2:
                # Dataforsyningen returnerer [lng, lat]
                return float(center[1]), float(center[0]), str(name)
    except requests.RequestException:
        pass

    return DEFAULT_LAT, DEFAULT_LNG, f"Herning (fallback for {clean})"


def _format_quantity(qty: dict[str, Any] | None) -> str:
    """Formaterer mængde og enhed fra eTilbudsavis' quantity-objekt."""
    if not qty:
        return ""

    size = qty.get("size") or {}
    unit = (qty.get("unit") or {}).get("symbol", "")
    pieces = qty.get("pieces") or {}

    size_from = size.get("from")
    size_to = size.get("to")
    pieces_from = pieces.get("from")

    parts: list[str] = []
    if pieces_from and pieces_from > 1:
        parts.append(f"{pieces_from}x")

    if size_from is not None:
        if size_to is not None and size_to != size_from:
            parts.append(f"{size_from:g}-{size_to:g}")
        else:
            parts.append(f"{size_from:g}")
        if unit:
            parts.append(unit)
    elif pieces_from:
        parts.append("stk" if pieces_from == 1 else "stk.")

    return " ".join(parts).replace("x ", "x")


def _parse_offer(raw: dict[str, Any], fallback_store: str) -> Offer | None:
    """Konverterer et råt API-tilbud til vores Offer-model."""
    heading = (raw.get("heading") or "").strip()
    pricing = raw.get("pricing") or {}
    price = pricing.get("price")
    if not heading or price is None:
        return None

    branding = raw.get("branding") or {}
    store_name = (branding.get("name") or fallback_store).strip()

    return Offer(
        id=str(raw.get("id") or ""),
        heading=heading,
        description=(raw.get("description") or "").strip(),
        price=float(price),
        unit_size=_format_quantity(raw.get("quantity")),
        store_name=store_name,
        valid_from=str(raw.get("run_from") or "")[:10],
        valid_until=str(raw.get("run_till") or "")[:10],
    )


def fetch_offers_for_stores(
    store_names: list[str],
    lat: float = DEFAULT_LAT,
    lng: float = DEFAULT_LNG,
    radius_meters: int = 15000,
    limit_per_store: int = 80,
) -> tuple[list[Offer], list[str]]:
    """
    Henter aktuelle tilbud fra eTilbudsavis.dk for de valgte kæder.

    Returnerer (liste af tilbud, liste af evt. advarsler/fejl pr. butik).
    """
    all_offers: list[Offer] = []
    warnings: list[str] = []

    for store_name in store_names:
        dealer_id = ALL_STORES.get(store_name)
        if not dealer_id:
            warnings.append(f"Ukendt butikskæde: {store_name}")
            continue

        params = {
            "r_lat": lat,
            "r_lng": lng,
            "r_radius": radius_meters,
            "dealer_ids": dealer_id,
            "limit": min(limit_per_store, 100),
            "offset": 0,
        }

        try:
            resp = requests.get(
                f"{API_BASE}/offers",
                headers=HEADERS,
                params=params,
                timeout=15,
            )
            if resp.status_code != 200:
                warnings.append(
                    f"{store_name}: API svarede med status {resp.status_code}"
                )
                continue

            raw_items = resp.json()
            if not isinstance(raw_items, list):
                warnings.append(f"{store_name}: Uventet svarformat fra API")
                continue

            parsed_count = 0
            for item in raw_items:
                offer = _parse_offer(item, fallback_store=store_name)
                if offer is not None:
                    all_offers.append(offer)
                    parsed_count += 1

            if parsed_count == 0:
                warnings.append(
                    f"{store_name}: Ingen aktive tilbud fundet inden for {radius_meters // 1000} km."
                )

        except requests.RequestException as exc:
            warnings.append(f"{store_name}: Netværksfejl ({exc})")

    return all_offers, warnings
