import hashlib
import uuid


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def stable_key(*parts: object) -> str:
    """Deterministic idempotency / dedup key."""
    raw = "|".join(str(p).strip().lower() for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def normalize_domain(value: str) -> str:
    v = value.strip().lower()
    for prefix in ("https://", "http://", "www."):
        if v.startswith(prefix):
            v = v[len(prefix) :]
    return v.split("/")[0]


def normalize_email(value: str) -> str:
    return value.strip().lower()
