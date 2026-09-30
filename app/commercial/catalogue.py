"""Supplier offers and the selling-price book.

In production these tables are the only place acquisition costs and selling
prices come from. They are filled by the operator from real quotes, supplier
price lists and observed market prices — one at a time or by CSV import.
An expired offer or price is ignored; a missing one leaves the deal unpriced
and routes it to review rather than letting anything estimate a number.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.ids import stable_key
from app.core.types import ProductCategory
from app.database.models import PriceReference, Supplier, SupplierOffer
from app.policies.licenses import canonical_country


class CatalogueError(ValueError):
    pass


CATEGORIES = {c.value for c in ProductCategory}


def _date(value: Any, field: str) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise CatalogueError(f"{field} must be YYYY-MM-DD, got {value!r}") from exc


def _float(value: Any, field: str, required: bool = True) -> float | None:
    if value in (None, ""):
        if required:
            raise CatalogueError(f"{field} is required")
        return None
    try:
        number = float(str(value).replace(",", "").strip())
    except ValueError as exc:
        raise CatalogueError(f"{field} must be a number, got {value!r}") from exc
    if number < 0:
        raise CatalogueError(f"{field} cannot be negative")
    return number


def _int(value: Any, field: str) -> int | None:
    number = _float(value, field, required=False)
    return None if number is None else int(number)


def _list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value).replace(";", "|").split("|") if part.strip()]


def _category(value: Any) -> str:
    category = str(value or "").strip()
    if category not in CATEGORIES:
        raise CatalogueError(f"unknown product category {category!r}; valid: {sorted(CATEGORIES)}")
    return category


# ------------------------------------------------------------------ suppliers


def upsert_supplier(
    session: Session,
    *,
    name: str,
    country: str | None = None,
    categories: list[str] | None = None,
    reliability_score: float | None = None,
    payment_terms: str | None = None,
    documents: list[str] | None = None,
) -> Supplier:
    if not name or not name.strip():
        raise CatalogueError("supplier name is required")
    country_name = canonical_country(country) if country else None
    key = stable_key(name.strip(), country_name or "")
    supplier = session.scalar(select(Supplier).where(Supplier.dedupe_key == key))
    if supplier is None:
        supplier = Supplier(name=name.strip(), dedupe_key=key, country=country_name, categories=[], documents=[])
        session.add(supplier)
    for category in categories or []:
        _category(category)
    supplier.categories = sorted({*(supplier.categories or []), *(categories or [])})
    if reliability_score is not None:
        if not 0 <= reliability_score <= 1:
            raise CatalogueError("reliability_score is between 0 and 1")
        supplier.reliability_score = reliability_score
    if payment_terms:
        supplier.payment_terms = payment_terms
    if documents:
        supplier.documents = sorted({*(supplier.documents or []), *documents})
    session.flush()
    return supplier


# ------------------------------------------------------------------ offers


def add_offer(session: Session, **fields: Any) -> SupplierOffer:
    category = _category(fields.get("product_category"))
    product_name = str(fields.get("product_name") or "").strip()
    if not product_name:
        raise CatalogueError("product_name is required")
    source = str(fields.get("source") or "").strip()
    if not source:
        raise CatalogueError("source is required (quote reference, email, price list) — offers must be traceable")
    low = _float(fields.get("unit_cost_low_usd", fields.get("unit_cost_usd")), "unit_cost_low_usd")
    high = _float(fields.get("unit_cost_high_usd"), "unit_cost_high_usd", required=False) or low
    if high < low:
        raise CatalogueError("unit_cost_high_usd is below unit_cost_low_usd")
    documents = _list(fields.get("documents"))
    supplier = upsert_supplier(
        session,
        name=str(fields.get("supplier_name") or ""),
        country=fields.get("supplier_country"),
        categories=[category],
        reliability_score=_float(fields.get("reliability_score"), "reliability_score", required=False),
        payment_terms=fields.get("payment_terms"),
        documents=documents,
    )
    offer = SupplierOffer(
        supplier_id=supplier.id,
        product_category=category,
        product_name=product_name,
        condition=(fields.get("condition") or None),
        quantity_available=_int(fields.get("quantity_available"), "quantity_available"),
        moq=_int(fields.get("moq"), "moq"),
        unit_cost_low_usd=low,
        unit_cost_high_usd=high,
        incoterm=(fields.get("incoterm") or None),
        shipping_cost_usd=_float(fields.get("shipping_cost_usd"), "shipping_cost_usd", required=False),
        lead_time_days=_int(fields.get("lead_time_days"), "lead_time_days"),
        payment_terms=(fields.get("payment_terms") or None),
        documents=documents,
        warranty=(fields.get("warranty") or None),
        valid_until=_date(fields.get("valid_until"), "valid_until"),
        source=source,
        notes=str(fields.get("notes") or ""),
        active=True,
    )
    session.add(offer)
    session.flush()
    return offer


def deactivate_offer(session: Session, offer_id: str) -> SupplierOffer:
    offer = session.get(SupplierOffer, offer_id)
    if offer is None:
        raise CatalogueError(f"no offer {offer_id}")
    offer.active = False
    session.flush()
    return offer


def list_offers(session: Session, category: str | None = None, include_inactive: bool = False) -> list[SupplierOffer]:
    query = select(SupplierOffer).order_by(SupplierOffer.product_category, SupplierOffer.unit_cost_low_usd)
    if category:
        query = query.where(SupplierOffer.product_category == category)
    if not include_inactive:
        query = query.where(SupplierOffer.active.is_(True))
    return list(session.scalars(query))


def current_offers(session: Session, category: str, on: date) -> list[dict[str, Any]]:
    """Live offers for a category in the shape the sourcing agent consumes."""
    rows = []
    for offer in list_offers(session, category):
        if offer.valid_until is not None and offer.valid_until < on:
            continue
        supplier = session.get(Supplier, offer.supplier_id)
        rows.append(
            {
                "offer_id": offer.id,
                "supplier_name": supplier.name if supplier else "unknown",
                "country": supplier.country if supplier else None,
                "product_name": offer.product_name,
                "unit_cost_usd": offer.unit_cost_low_usd,
                "unit_cost_high_usd": offer.unit_cost_high_usd,
                "quantity_available": offer.quantity_available,
                "moq": offer.moq,
                "condition": offer.condition,
                "lead_time_days": offer.lead_time_days,
                "shipping_cost_usd": offer.shipping_cost_usd,
                "incoterm": offer.incoterm,
                "payment_terms": offer.payment_terms,
                "documents": sorted({*(offer.documents or []), *((supplier.documents or []) if supplier else [])}),
                "reliability": supplier.reliability_score if supplier else 0.5,
                "source": offer.source,
                "valid_until": offer.valid_until.isoformat() if offer.valid_until else None,
            }
        )
    return rows


# ------------------------------------------------------------------ price book


def add_price_reference(session: Session, **fields: Any) -> PriceReference:
    category = _category(fields.get("product_category"))
    low = _float(fields.get("unit_price_low_usd"), "unit_price_low_usd")
    high = _float(fields.get("unit_price_high_usd"), "unit_price_high_usd", required=False) or low
    if high < low:
        raise CatalogueError("unit_price_high_usd is below unit_price_low_usd")
    basis = str(fields.get("basis") or "").strip()
    source = str(fields.get("source") or "").strip()
    if not basis or not source:
        raise CatalogueError("basis and source are required — a selling price must say where it came from")
    country = fields.get("country")
    row = PriceReference(
        product_category=category,
        product_name=(fields.get("product_name") or None),
        condition=(fields.get("condition") or None),
        country=canonical_country(country) if country else None,
        unit_price_low_usd=low,
        unit_price_high_usd=high,
        basis=basis,
        source=source,
        valid_until=_date(fields.get("valid_until"), "valid_until"),
        active=True,
    )
    session.add(row)
    session.flush()
    return row


def list_price_references(session: Session, category: str | None = None) -> list[PriceReference]:
    query = select(PriceReference).where(PriceReference.active.is_(True)).order_by(
        PriceReference.product_category, PriceReference.country
    )
    if category:
        query = query.where(PriceReference.product_category == category)
    return list(session.scalars(query))


def deactivate_price_reference(session: Session, price_id: str) -> PriceReference:
    row = session.get(PriceReference, price_id)
    if row is None:
        raise CatalogueError(f"no price reference {price_id}")
    row.active = False
    session.flush()
    return row


def price_reference(session: Session, category: str, country: str | None, on: date) -> PriceReference | None:
    """Country-specific price first, then a category-wide one; newest wins."""
    target = canonical_country(country) if country else None
    live = [
        p for p in list_price_references(session, category)
        if p.valid_until is None or p.valid_until >= on
    ]
    specific = [p for p in live if p.country and target and p.country == target]
    general = [p for p in live if not p.country]
    pool = specific or general
    if not pool:
        return None
    return max(pool, key=lambda p: p.created_at)


# ------------------------------------------------------------------ CSV import

OFFER_COLUMNS = [
    "supplier_name", "supplier_country", "product_category", "product_name", "condition",
    "quantity_available", "moq", "unit_cost_low_usd", "unit_cost_high_usd", "incoterm",
    "shipping_cost_usd", "lead_time_days", "payment_terms", "documents", "warranty",
    "valid_until", "source", "reliability_score", "notes",
]
PRICE_COLUMNS = [
    "product_category", "product_name", "condition", "country", "unit_price_low_usd",
    "unit_price_high_usd", "basis", "source", "valid_until",
]


def import_csv(session: Session, text: str, kind: str) -> dict[str, Any]:
    """Import offers or prices. All rows are validated first; any error imports nothing."""
    if kind not in {"offers", "prices"}:
        raise CatalogueError("kind must be 'offers' or 'prices'")
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    rows = [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in reader]
    if not rows:
        raise CatalogueError("the file has no data rows")
    errors: list[str] = []
    created: list[str] = []
    savepoint = session.begin_nested()
    for number, row in enumerate(rows, start=2):
        try:
            item = add_offer(session, **row) if kind == "offers" else add_price_reference(session, **row)
            created.append(item.id)
        except CatalogueError as exc:
            errors.append(f"row {number}: {exc}")
    if errors:
        savepoint.rollback()
        return {"imported": 0, "errors": errors}
    savepoint.commit()
    return {"imported": len(created), "errors": [], "ids": created}


def template(kind: str) -> str:
    columns = OFFER_COLUMNS if kind == "offers" else PRICE_COLUMNS
    return ",".join(columns) + "\n"


def offer_as_dict(offer: SupplierOffer, session: Session) -> dict[str, Any]:
    supplier = session.get(Supplier, offer.supplier_id)
    return {
        "id": offer.id,
        "supplier": supplier.name if supplier else None,
        "supplier_country": supplier.country if supplier else None,
        "product_category": offer.product_category,
        "product_name": offer.product_name,
        "condition": offer.condition,
        "quantity_available": offer.quantity_available,
        "moq": offer.moq,
        "unit_cost_usd": [offer.unit_cost_low_usd, offer.unit_cost_high_usd],
        "incoterm": offer.incoterm,
        "shipping_cost_usd": offer.shipping_cost_usd,
        "lead_time_days": offer.lead_time_days,
        "payment_terms": offer.payment_terms,
        "documents": offer.documents,
        "warranty": offer.warranty,
        "valid_until": offer.valid_until.isoformat() if offer.valid_until else None,
        "source": offer.source,
        "active": offer.active,
    }


def price_as_dict(row: PriceReference) -> dict[str, Any]:
    return {
        "id": row.id,
        "product_category": row.product_category,
        "product_name": row.product_name,
        "condition": row.condition,
        "country": row.country,
        "unit_price_usd": [row.unit_price_low_usd, row.unit_price_high_usd],
        "basis": row.basis,
        "source": row.source,
        "valid_until": row.valid_until.isoformat() if row.valid_until else None,
    }
