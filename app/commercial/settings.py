"""Operator-owned commercial parameters, stored in the database and editable live.

Nothing here is a market fact. Margins and typical order sizes are the
operator's policy; duty rates are only used when the operator has entered them
(from a tariff schedule or a clearing agent). Unknown duty stays unknown and is
reported as such in every deal's economics.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.types import ProductCategory
from app.database.models import SystemState
from app.policies.licenses import canonical_country

KEY = "commercial"

DEFAULTS: dict[str, Any] = {
    "min_margin_pct": {c.value: 12.0 for c in ProductCategory},
    # Used only to size an opportunity when the buyer has not stated a quantity;
    # always labelled as an assumption in the deal economics.
    "typical_order_qty": {
        ProductCategory.LAPTOP.value: 20,
        ProductCategory.IPHONE.value: 30,
        ProductCategory.MEDICAL.value: 2,
        ProductCategory.PHARMA.value: 1000,
        ProductCategory.SERVER_IT.value: 4,
    },
    "duties_taxes_pct": {},  # country -> fraction, e.g. {"Kenya": 0.16}
    # Documents a supplier offer must carry before a regulated line goes to a
    # buyer. Matching is case-insensitive against the offer's document list.
    "required_documents": {
        ProductCategory.PHARMA.value: ["export licence", "batch certificates"],
        ProductCategory.MEDICAL.value: ["CE documentation on file"],
    },
    "transaction_cost_pct": 0.03,
    # Countries research may consider per category. Market scores are recomputed
    # from evidence every cycle; this list only bounds where NEXUS looks.
    "target_markets": {
        ProductCategory.MEDICAL.value: ["Kenya", "Uganda", "Tanzania", "Rwanda", "Ethiopia", "Zambia"],
        ProductCategory.PHARMA.value: ["Kenya", "Uganda", "Tanzania", "Rwanda", "Ethiopia", "Zambia"],
        ProductCategory.LAPTOP.value: ["Kenya", "Uganda", "Tanzania", "Rwanda", "Ethiopia", "Nigeria", "Ghana"],
        ProductCategory.SERVER_IT.value: ["Kenya", "Uganda", "Tanzania", "Rwanda", "Ethiopia", "Nigeria", "Egypt"],
        ProductCategory.IPHONE.value: ["Kenya", "Uganda", "Tanzania", "Rwanda", "Romania", "Bulgaria", "Serbia", "Moldova"],
    },
    # Public directories searched (through the search API, "site:" queries) in
    # addition to the open web. The organisation's own site is always what
    # contact details are taken from.
    "directories": {
        "buyer": ["ungm.org", "tenders.go.ke"],
        "supplier": {
            ProductCategory.LAPTOP.value: ["made-in-china.com", "indiamart.com", "europages.co.uk", "globalsources.com"],
            ProductCategory.IPHONE.value: ["made-in-china.com", "globalsources.com", "europages.co.uk"],
            ProductCategory.MEDICAL.value: ["dotmed.com", "europages.co.uk", "indiamart.com", "made-in-china.com"],
            ProductCategory.PHARMA.value: ["indiamart.com", "tradeindia.com", "europages.co.uk"],
            ProductCategory.SERVER_IT.value: ["europages.co.uk", "made-in-china.com", "indiamart.com"],
        },
    },
    # Where supplier research looks, per category.
    "supplier_regions": {
        ProductCategory.LAPTOP.value: ["United Arab Emirates", "United States", "United Kingdom", "Germany", "China", "Kenya"],
        ProductCategory.IPHONE.value: ["United Arab Emirates", "Hong Kong", "United States", "China"],
        ProductCategory.MEDICAL.value: ["United States", "Germany", "Netherlands", "India", "China", "United Arab Emirates"],
        ProductCategory.PHARMA.value: ["India", "China", "Kenya", "Egypt"],
        ProductCategory.SERVER_IT.value: ["United Arab Emirates", "United States", "United Kingdom", "Netherlands", "Germany"],
    },
    # What an RFQ asks suppliers to quote for (quantities come from live demand).
    "rfq_products": {
        ProductCategory.LAPTOP.value: "business-class refurbished laptops (Dell Latitude, HP EliteBook/ProBook, Lenovo ThinkPad), Intel Core i5/i7 8th generation or newer, 8-16GB RAM, SSD",
        ProductCategory.IPHONE.value: "used / refurbished Apple iPhones (iPhone 11 to current generations), grade A and B, unlocked",
        ProductCategory.MEDICAL.value: "new or refurbished hospital equipment: patient monitors, ultrasound, ECG, laboratory analysers",
        ProductCategory.PHARMA.value: "generic essential medicines and medical consumables for institutional supply",
        ProductCategory.SERVER_IT.value: "refurbished enterprise servers (Dell PowerEdge, HPE ProLiant), storage and networking equipment",
    },
    "rfq_destination": "Nairobi, Kenya (CIF Mombasa or DAP Nairobi)",
    "supplier_min_score": 0.5,
}


class CommercialSettingsError(ValueError):
    pass


def get(session: Session) -> dict[str, Any]:
    row = session.get(SystemState, KEY)
    stored = dict(row.value) if row else {}
    merged: dict[str, Any] = {}
    for key, default in DEFAULTS.items():
        value = stored.get(key, default)
        merged[key] = {**default, **value} if isinstance(default, dict) else value
    return merged


def update(session: Session, changes: dict[str, Any]) -> dict[str, Any]:
    categories = {c.value for c in ProductCategory}
    current = get(session)
    for key, value in changes.items():
        if key not in DEFAULTS:
            raise CommercialSettingsError(f"unknown setting {key!r}; valid: {sorted(DEFAULTS)}")
        if key in ("min_margin_pct", "typical_order_qty"):
            if not isinstance(value, dict):
                raise CommercialSettingsError(f"{key} must be an object keyed by product category")
            for cat, number in value.items():
                if cat not in categories:
                    raise CommercialSettingsError(f"unknown category {cat!r}")
                number = float(number)
                if key == "min_margin_pct" and not 0 <= number < 100:
                    raise CommercialSettingsError("min_margin_pct must be between 0 and 100")
                if key == "typical_order_qty" and number < 1:
                    raise CommercialSettingsError("typical_order_qty must be at least 1")
                current[key][cat] = int(number) if key == "typical_order_qty" else number
        elif key == "duties_taxes_pct":
            if not isinstance(value, dict):
                raise CommercialSettingsError("duties_taxes_pct must be an object keyed by country")
            for country, rate in value.items():
                if rate is None:
                    current[key].pop(country, None)
                    continue
                rate = float(rate)
                if not 0 <= rate < 3:
                    raise CommercialSettingsError("duty rates are fractions, e.g. 0.16 for 16%")
                current[key][country] = rate
        elif key == "required_documents":
            if not isinstance(value, dict):
                raise CommercialSettingsError("required_documents must be an object keyed by product category")
            for cat, docs in value.items():
                if cat not in categories:
                    raise CommercialSettingsError(f"unknown category {cat!r}")
                if not isinstance(docs, list) or not all(isinstance(d, str) and d.strip() for d in docs):
                    raise CommercialSettingsError("required_documents values are lists of document names")
                current[key][cat] = [d.strip() for d in docs]
        elif key == "target_markets":
            if not isinstance(value, dict):
                raise CommercialSettingsError("target_markets must be an object keyed by product category")
            for cat, countries in value.items():
                if cat not in categories:
                    raise CommercialSettingsError(f"unknown category {cat!r}")
                if not isinstance(countries, list) or not all(isinstance(c, str) and c.strip() for c in countries):
                    raise CommercialSettingsError("target_markets values are lists of country names")
                current[key][cat] = [canonical_country(c) for c in countries]
        elif key in ("directories", "supplier_regions", "rfq_products"):
            if not isinstance(value, dict):
                raise CommercialSettingsError(f"{key} must be an object")
            current[key] = {**current[key], **value}
        elif key == "rfq_destination":
            if not isinstance(value, str) or not value.strip():
                raise CommercialSettingsError("rfq_destination is a place description, e.g. 'Nairobi, Kenya'")
            current[key] = value.strip()
        elif key == "supplier_min_score":
            number = float(value)
            if not 0 <= number <= 1:
                raise CommercialSettingsError("supplier_min_score is between 0 and 1")
            current[key] = number
        elif key == "transaction_cost_pct":
            rate = float(value)
            if not 0 <= rate < 0.5:
                raise CommercialSettingsError("transaction_cost_pct is a fraction, e.g. 0.03")
            current[key] = rate
    row = session.get(SystemState, KEY)
    if row is None:
        row = SystemState(key=KEY, value={})
        session.add(row)
    row.value = current
    session.flush()
    return current


def typical_order_qty(session: Session, category: str, simulation: bool) -> int:
    if simulation:
        from app.simulation.fixtures import UNIT_ECONOMICS

        return max(int(UNIT_ECONOMICS.get(category, {}).get("typical_qty", 20)), 1)
    return max(int(get(session)["typical_order_qty"].get(category, 1)), 1)


def duty_rate(session: Session, country: str | None, simulation: bool) -> float | None:
    """Operator-entered duty/tax fraction for a destination, or None (unknown)."""
    if not country:
        return None
    if simulation:
        from app.simulation.fixtures import SIM_DUTIES_PCT

        return SIM_DUTIES_PCT.get(country)
    return get(session)["duties_taxes_pct"].get(country)
