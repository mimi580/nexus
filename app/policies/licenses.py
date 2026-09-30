"""Licence register: which regulated trade the operator is actually allowed to do.

A licence covers one country, one or more licence types (importer, distributor,
wholesaler...) and one or more product categories, between two dates. The
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

LICENSE_TYPES = {"importer", "distributor", "wholesaler", "exporter", "retailer"}
VERIFICATION_LEVELS = {"user_provided", "document_checked", "registry_confirmed"}
EXPIRY_WARNING_DAYS = 45


class LicenseError(ValueError):
    """Invalid licence data."""


@dataclass(frozen=True)
class Coverage:
    covered: bool
    reason: str
    license: License | None = None

    @property
    def expires_on(self) -> date | None:
        return self.license.expires_on if self.license else None


def _norm(value: str | None) -> str:
    return (value or "").strip().casefold()


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
) -> License:
    """Validate and store a licence, with an evidence record for provenance."""
    if not holder_name.strip() or not country.strip() or not license_number.strip():
        raise LicenseError("holder_name, country and license_number are required")
    if not issuing_authority.strip():
        raise LicenseError("issuing_authority is required")
    types = sorted({_norm(t) for t in license_types if t})
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
    start = _parse_date(valid_from, "valid_from")
    if start and start > expiry:
        raise LicenseError("valid_from is after expires_on")

    duplicate = session.scalar(
        select(License).where(
            License.country == country.strip(), License.license_number == license_number.strip()
        )
    )
    if duplicate is not None:
        raise LicenseError(f"licence {license_number} for {country} is already registered ({duplicate.id})")

    row = License(
        holder_name=holder_name.strip(),
        country=country.strip(),
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
            f"for {', '.join(categories)}, issued by {row.issuing_authority}, expiring {expiry.isoformat()}"
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


def coverage(session: Session, country: str | None, category: str | None, on: date) -> Coverage:
    """Is there an active licence covering this country and category on this date?"""
    if not country:
        return Coverage(False, "buyer country unknown; licence coverage cannot be established")
    if not category:
        return Coverage(False, "product category unknown")
    in_country = [lic for lic in list_licenses(session) if _norm(lic.country) == _norm(country)]
    if not in_country:
        return Coverage(False, f"no active licence registered for {country}")
    for_category = [lic for lic in in_country if category in (lic.product_categories or [])]
    if not for_category:
        return Coverage(False, f"licences in {country} do not cover {category}")
    current = [
        lic for lic in for_category
        if (lic.valid_from is None or lic.valid_from <= on) and lic.expires_on is not None and lic.expires_on >= on
    ]
    if not current:
        latest = max(for_category, key=lambda lic: lic.expires_on or date.min)
        if latest.valid_from and latest.valid_from > on:
            return Coverage(False, f"{country} licence {latest.license_number} not valid until {latest.valid_from}", latest)
        return Coverage(False, f"{country} licence {latest.license_number} expired on {latest.expires_on}", latest)
    best = max(current, key=lambda lic: lic.expires_on)
    return Coverage(True, f"covered by {country} licence {best.license_number} until {best.expires_on}", best)


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
