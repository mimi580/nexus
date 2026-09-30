"""Provider-neutral outbound communication layer.

Simulation is the default. A production adapter implements the same protocol;
policy, budget, dedupe and rate limits are enforced before send() is ever
called, in app.agents.outreach.
"""

from __future__ import annotations

import hashlib
import hmac
import random
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Protocol

from app.core.config import Settings
from app.core.errors import ProviderUnavailable
from app.core.ids import new_id, stable_key
from app.simulation import fixtures


@dataclass
class SendResult:
    ok: bool
    provider: str
    provider_message_id: str | None = None
    simulated: bool = True
    error: str | None = None
    simulated_reply: str | None = None
    simulated_reply_category: str | None = None
    reply_delay_hours: float | None = None
    cost_usd: float = 0.0
    permanent_failure: bool = False


class EmailProvider(Protocol):
    name: str

    def send(self, *, to: str, subject: str, body: str, context: dict) -> SendResult: ...


class SimulatedEmailProvider:
    """Sends nothing. Produces a deterministic, seeded reply for the loop to process."""

    name = "simulated"

    def __init__(self, seed: int = 20260917, failure_rate: float = 0.0) -> None:
        self.seed = seed
        self.failure_rate = failure_rate
        self.sent: list[dict] = []

    def send(self, *, to: str, subject: str, body: str, context: dict) -> SendResult:
        rng = random.Random(int(stable_key(to, subject, str(self.seed))[:8], 16))
        if self.failure_rate and rng.random() < self.failure_rate:
            return SendResult(ok=False, provider=self.name, error="simulated transport failure")

        self.sent.append({"to": to, "subject": subject, "body": body})
        category = (context or {}).get("product_category")
        if (context or {}).get("counterparty") == "supplier":
            labels = [label for label, _ in fixtures.SUPPLIER_RESPONSE_MIX]
            weights = [weight for _, weight in fixtures.SUPPLIER_RESPONSE_MIX]
            outcome = rng.choices(labels, weights=weights, k=1)[0]
            reply = {
                "quote": fixtures.sim_supplier_quote(category, rng) if category else None,
                "question": fixtures.SUPPLIER_QUESTION,
                "not_supplying": fixtures.SUPPLIER_DECLINE,
            }.get(outcome)
            return SendResult(
                ok=True, provider=self.name, provider_message_id=new_id("sim"), simulated=True,
                simulated_reply=reply, simulated_reply_category=outcome if reply else None,
                reply_delay_hours=round(rng.uniform(4, 72), 1) if reply else None,
            )
        mix = fixtures.RESPONSE_MIX.get(category) or fixtures.RESPONSE_MIX[
            fixtures.ProductCategory.LAPTOP.value
        ]
        labels = [label for label, _ in mix]
        weights = [weight for _, weight in mix]
        outcome = rng.choices(labels, weights=weights, k=1)[0]
        reply = None if outcome == "no_reply" else fixtures.REPLY_TEMPLATES.get(outcome)
        return SendResult(
            ok=True,
            provider=self.name,
            provider_message_id=new_id("sim"),
            simulated=True,
            simulated_reply=reply,
            simulated_reply_category=None if reply is None else outcome,
            reply_delay_hours=round(rng.uniform(2, 48), 1) if reply else None,
            cost_usd=0.0,
        )


class SmtpEmailProvider:
    """Sends through any SMTP server (Google Workspace, Zoho, Microsoft 365, Mailgun...).

    Credentials come from settings only. Policy, budget, dedupe and rate limits
    have already been enforced by the time send() is called.
    """

    name = "smtp"

    def __init__(self, settings: Settings) -> None:
        missing = [
            name for name, value in (
                ("SMTP_HOST", settings.smtp_host),
                ("SMTP_USERNAME", settings.smtp_username),
                ("SMTP_PASSWORD", settings.smtp_password),
                ("EMAIL_SENDER_ADDRESS", settings.email_sender_address),
            ) if not value
        ]
        if missing:
            raise ProviderUnavailable("email is not configured: " + ", ".join(missing))
        self.settings = settings

    def send(self, *, to: str, subject: str, body: str, context: dict) -> SendResult:
        message = build_mime(
            self.settings, to=to, subject=subject, body=body,
            message_id=context.get("message_id"), unsubscribe_url=context.get("unsubscribe_url"),
            in_reply_to=context.get("in_reply_to"),
        )
        s = self.settings
        try:
            if s.smtp_security == "ssl":
                server: smtplib.SMTP = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=30,
                                                       context=ssl.create_default_context())
            else:
                server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30)
            with server:
                if s.smtp_security == "starttls":
                    server.starttls(context=ssl.create_default_context())
                server.login(s.smtp_username, s.smtp_password)
                refused = server.send_message(message)
        except smtplib.SMTPRecipientsRefused as exc:
            return SendResult(ok=False, provider=self.name, simulated=False,
                              error=f"recipient refused: {exc.recipients}", permanent_failure=True)
        except smtplib.SMTPAuthenticationError as exc:
            return SendResult(ok=False, provider=self.name, simulated=False, error=f"SMTP login failed ({exc.smtp_code})")
        except (smtplib.SMTPException, OSError) as exc:
            return SendResult(ok=False, provider=self.name, simulated=False, error=f"SMTP error: {exc}")
        if refused:
            return SendResult(ok=False, provider=self.name, simulated=False,
                              error=f"recipient refused: {refused}", permanent_failure=True)
        return SendResult(ok=True, provider=self.name, provider_message_id=message["Message-ID"], simulated=False)


class UnconfiguredEmailProvider:
    name = "unconfigured"

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def send(self, *, to: str, subject: str, body: str, context: dict) -> SendResult:
        return SendResult(ok=False, provider=self.name, simulated=False, error=self.reason)


def sender_domain(settings: Settings) -> str:
    address = settings.email_sender_address or "nexus.invalid"
    return address.rsplit("@", 1)[-1]


def new_message_id(settings: Settings) -> str:
    return make_msgid(idstring=new_id("m"), domain=sender_domain(settings))


def unsubscribe_token(settings: Settings, contact_id: str) -> str | None:
    if not settings.unsubscribe_secret:
        return None
    digest = hmac.new(settings.unsubscribe_secret.encode(), contact_id.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{contact_id}.{digest}"


def verify_unsubscribe_token(settings: Settings, token: str) -> str | None:
    """The contact id a token was issued for, or None if it is not genuine."""
    if not settings.unsubscribe_secret or "." not in token:
        return None
    contact_id, _ = token.rsplit(".", 1)
    expected = unsubscribe_token(settings, contact_id)
    return contact_id if expected and hmac.compare_digest(expected, token) else None


def unsubscribe_url(settings: Settings, contact_id: str) -> str | None:
    token = unsubscribe_token(settings, contact_id)
    if not token or not settings.public_base_url:
        return None
    return settings.public_base_url.rstrip("/") + "/u/" + token


def compliance_footer(settings: Settings, unsubscribe_link: str | None) -> str:
    """Sender identification and opt-out, appended by code to every message."""
    lines = []
    if settings.business_name:
        lines.append(settings.business_name)
    if settings.business_postal_address:
        lines.append(settings.business_postal_address)
    opt_out = "To stop receiving these emails, reply with 'unsubscribe'"
    opt_out += f" or use {unsubscribe_link}" if unsubscribe_link else ""
    lines.append(opt_out + ".")
    return "\n\n--\n" + "\n".join(lines)


def build_mime(
    settings: Settings, *, to: str, subject: str, body: str,
    message_id: str | None = None, unsubscribe_url: str | None = None, in_reply_to: str | None = None,
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = formataddr((settings.email_sender_name or "", settings.email_sender_address or ""))
    message["To"] = to
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = message_id or new_message_id(settings)
    if settings.email_reply_to:
        message["Reply-To"] = settings.email_reply_to
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        message["References"] = in_reply_to
    unsubscribe_targets = [f"<mailto:{settings.email_reply_to or settings.email_sender_address}?subject=unsubscribe>"]
    if unsubscribe_url:
        unsubscribe_targets.insert(0, f"<{unsubscribe_url}>")
        message["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    message["List-Unsubscribe"] = ", ".join(unsubscribe_targets)
    message.set_content(body)
    return message


def build_email_provider(settings: Settings) -> EmailProvider:
    if settings.nexus_mode == "simulation" or settings.email_provider == "simulated":
        return SimulatedEmailProvider(seed=settings.random_seed)
    try:
        return SmtpEmailProvider(settings)
    except ProviderUnavailable as exc:
        # Never fall back to "pretend sending" in production; every send fails
        # visibly and readiness reports what is missing.
        return UnconfiguredEmailProvider(str(exc))
