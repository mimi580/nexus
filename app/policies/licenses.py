"""Licence register: which regulated trade the operator is actually allowed to do.

A licence is issued in one country and may be registered with a wider trading
scope (a regional bloc such as EAC or COMESA, or named countries). It has one or
more licence types (importer, distributor, wholesaler...) and product
categories, between two dates.

A licence never authorises import into another country. Deals with buyers
outside the issuing country are "cross-border": outreach is permitted, but any
commitment escalates until the buyer's own import authorisation and the
product's registration in the destination country are confirmed. The
policy engine asks `coverage()` before any regulated outreach or commitment;
the outreach agent asks it before letting copy mention a licence.

Everything here is deterministic. A model never decides whether a licence
applies.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.interfaces import EvidenceRecord
from app.core.types import EvidenceKind, ProductCategory, VerificationStatus
from app.database.models import Evidence, License

LICENSE_TYPE_ORDER = ("importer", "exporter", "distributor", "wholesaler", "retailer")
LICENSE_TYPES = set(LICENSE_TYPE_ORDER)
VERIFICATION_LEVELS = {"user_provided", "document_checked", "registry_confirmed"}
EXPIRY_WARNING_DAYS = 45

# Membership as published by each bloc (checked 2026-09-30); re-check when
# membership changes. comesa.int lists 21 member states.
REGIONS: dict[str, tuple[str, ...]] = {
    "EAC": (
        "Burundi", "Democratic Republic of the Congo", "Kenya", "Rwanda", "Somalia",
        "South Sudan", "Tanzania", "Uganda",
    ),
    "COMESA": (
        "Burundi", "Comoros", "Democratic Republic of the Congo", "Djibouti", "Egypt", "Eswatini",
        "Eritrea", "Ethiopia", "Kenya", "Libya", "Madagascar", "Malawi", "Mauritius", "Rwanda",
        "Seychelles", "Somalia", "Sudan", "Tunisia", "Uganda", "Zambia", "Zimbabwe",
    ),
}

COUNTRY_ALIASES = {
    "drc": "Democratic Republic of the Congo",
    "dr congo": "Democratic Republic of the Congo",
    "congo-kinshasa": "Democratic Republic of the Congo",
    "democratic republic of congo": "Democratic Republic of the Congo",
    "swaziland": "Eswatini",
    "united republic of tanzania": "Tanzania",
    "the sudan": "Sudan",
    "republic of the sudan": "Sudan",
}


class LicenseError(ValueError):
    """Invalid licence data."""


@dataclass(frozen=True)
class Coverage:
    covered: bool
    reason: str
    license: License | None = None
    cross_border: bool = False

    @property
    def expires_on(self) -> date | None:
        return self.license.expires_on if self.license else None


def _norm(value: str | None) -> str:
    return (value or "").strip().casefold()


def canonical_country(value: str | None) -> str:
    """Normalise a country name so 'DRC', 'dr congo' and the full name match."""
    cleaned = " ".join((value or "").split())
    alias = COUNTRY_ALIASES.get(cleaned.casefold())
    if alias:
        return alias
    for members in REGIONS.values():
        for name in members:
            if name.casefold() == cleaned.casefold():
                return name
    return cleaned


def _parse_date(value: Any, field: str) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise LicenseError(f"{field} must be an ISO date (YYYY-MM-DD), got {value!r}") from exc


def add_license(
    session: Session,
    *,
    holder_name: str,
    country: str,
    issuing_authority: str,
    license_number: str,
    license_types: list[str],
    product_categories: list[str],
    expires_on: Any,
    valid_from: Any = None,
    scope_notes: str = "",
    document_ref: str | None = None,
    verification: str = "user_provided",
    regions: list[str] | None = None,
    coverage_countries: list[str] | None = None,
) -> License:
    """Validate and store a licence, with an evidence record for provenance."""
    if not holder_name.strip() or not country.strip() or not license_number.strip():
        raise LicenseError("holder_name, country and license_number are required")
    if not issuing_authority.strip():
        raise LicenseError("issuing_authority is required")
    requested = {_norm(t) for t in license_types if t}
    types = [t for t in LICENSE_TYPE_ORDER if t in requested] + sorted(requested - LICENSE_TYPES)
    if not types:
        raise LicenseError(f"at least one licence type is required: {sorted(LICENSE_TYPES)}")
    unknown_types = [t for t in types if t not in LICENSE_TYPES]
    if unknown_types:
        raise LicenseError(f"unknown licence types {unknown_types}; valid: {sorted(LICENSE_TYPES)}")
    valid_categories = {c.value for c in ProductCategory}
    categories = sorted({c.strip() for c in product_categories if c})
    if not categories:
        raise LicenseError("at least one product category is required")
    unknown = [c for c in categories if c not in valid_categories]
    if unknown:
        raise LicenseError(f"unknown product categories {unknown}; valid: {sorted(valid_categories)}")
    if verification not in VERIFICATION_LEVELS:
        raise LicenseError(f"verification must be one of {sorted(VERIFICATION_LEVELS)}")
    expiry = _parse_date(expires_on, "expires_on")
    if expiry is None:
        raise LicenseError("expires_on is required; licences without an expiry cannot be relied on")
    region_names = sorted({r.strip().upper() for r in (regions or []) if r and r.strip()})
    unknown_regions = [r for r in region_names if r not in REGIONS]
    if unknown_regions:
        raise LicenseError(f"unknown regions {unknown_regions}; valid: {sorted(REGIONS)}")
    issuing = canonical_country(country)
    scope = {issuing}
    for region in region_names:
        scope.update(REGIONS[region])
    scope.update(canonical_country(c) for c in (coverage_countries or []) if c and c.strip())
    start = _parse_date(valid_from, "valid_from")
    if start and start > expiry:
        raise LicenseError("valid_from is after expires_on")

    duplicate = session.scalar(
        select(License).where(
            License.country == issuing, License.license_number == license_number.strip()
        )
    )
    if duplicate is not None:
        raise LicenseError(f"licence {license_number} for {country} is already registered ({duplicate.id})")

    row = License(
        holder_name=holder_name.strip(),
        country=issuing,
        regions=region_names,
        coverage_countries=sorted(scope),
        issuing_authority=issuing_authority.strip(),
        license_number=license_number.strip(),
        license_types=types,
        product_categories=categories,
        valid_from=start,
        expires_on=expiry,
        scope_notes=scope_notes,
        document_ref=document_ref,
        verification=verification,
        active=True,
    )
    session.add(row)
    session.flush()
    record = EvidenceRecord(
        claim=(
            f"{row.holder_name} holds {'/'.join(types)} licence {row.license_number} in {row.country} "
            f"for {', '.join(categories)}, issued by {row.issuing_authority}, expiring {expiry.isoformat()}; "
            f"declared trading scope: {', '.join(region_names) or 'issuing country only'}"
            + (f" ({len(scope)} countries)" if len(scope) > 1 else "")
        ),
        kind=EvidenceKind.USER_PROVIDED,
        source=document_ref or "operator declaration",
        source_type="user",
        confidence=0.9 if verification == "user_provided" else 1.0,
        verification=(
            VerificationStatus.UNVERIFIED if verification == "user_provided" else VerificationStatus.VERIFIED
        ),
        subject_type="license",
        subject_id=row.id,
    )
    session.add(
        Evidence(
            claim=record.claim,
            kind=record.kind.value,
            source=record.source[:500],
            source_type=record.source_type,
            confidence=record.confidence,
            verification=record.verification.value,
            subject_type=record.subject_type,
            subject_id=record.subject_id,
        )
    )
    session.flush()
    return row


def deactivate_license(session: Session, license_id: str) -> License:
    row = session.get(License, license_id)
    if row is None:
        raise LicenseError(f"no licence {license_id}")
    row.active = False
    session.flush()
    return row


def list_licenses(session: Session, include_inactive: bool = False) -> list[License]:
    query = select(License).order_by(License.country, License.expires_on)
    if not include_inactive:
        query = query.where(License.active.is_(True))
    return list(session.scalars(query))


def _scope(lic: License) -> set[str]:
    return {canonical_country(c) for c in (lic.coverage_countries or [])} | {canonical_country(lic.country)}


def coverage(session: Session, country: str | None, category: str | None, on: date) -> Coverage:
    """Is there an active licence whose trading scope covers this country and category?

    A match outside the licence's issuing country is flagged cross_border.
    """
    if not country:
        return Coverage(False, "buyer country unknown; licence coverage cannot be established")
    if not category:
        return Coverage(False, "product category unknown")
    target = canonical_country(country)
    in_scope = [lic for lic in list_licenses(session) if target in _scope(lic)]
    if not in_scope:
        return Coverage(False, f"no active licence covers {target}")
    for_category = [lic for lic in in_scope if category in (lic.product_categories or [])]
    if not for_category:
        return Coverage(False, f"licences covering {target} do not include {category}")
    current = [
        lic for lic in for_category
        if (lic.valid_from is None or lic.valid_from <= on) and lic.expires_on is not None and lic.expires_on >= on
    ]
    if not current:
        latest = max(for_category, key=lambda lic: lic.expires_on or date.min)
        if latest.valid_from and latest.valid_from > on:
            return Coverage(False, f"licence {latest.license_number} not valid until {latest.valid_from}", latest)
        return Coverage(False, f"licence {latest.license_number} expired on {latest.expires_on}", latest)
    # Prefer a licence issued in the buyer's own country, then the longest-lived.
    best = max(current, key=lambda lic: (canonical_country(lic.country) == target, lic.expires_on))
    cross_border = canonical_country(best.country) != target
    where = f"{best.country} licence {best.license_number}"
    reason = (
        f"covered by {where} until {best.expires_on}"
        + (f"; cross-border into {target}" if cross_border else "")
    )
    return Coverage(True, reason, best, cross_border)


def expiring(session: Session, today: date, within_days: int = EXPIRY_WARNING_DAYS) -> list[License]:
    horizon = today + timedelta(days=within_days)
    return [
        lic for lic in list_licenses(session)
        if lic.expires_on is not None and lic.expires_on <= horizon
    ]


def as_dict(lic: License, today: date | None = None) -> dict[str, Any]:
    days_left = (lic.expires_on - today).days if (today and lic.expires_on) else None
    return {
        "id": lic.id,
        "holder_name": lic.holder_name,
        "country": lic.country,
        "regions": lic.regions or [],
        "coverage_countries": lic.coverage_countries or [],
        "issuing_authority": lic.issuing_authority,
        "license_number": lic.license_number,
        "license_types": lic.license_types,
        "product_categories": lic.product_categories,
        "valid_from": lic.valid_from.isoformat() if lic.valid_from else None,
        "expires_on": lic.expires_on.isoformat() if lic.expires_on else None,
        "days_to_expiry": days_left,
        "verification": lic.verification,
        "active": lic.active,
        "scope_notes": lic.scope_notes,
        "document_ref": lic.document_ref,
    }
