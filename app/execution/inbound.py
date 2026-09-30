"""Inbound mail: replies, bounces and opt-outs from the sending mailbox (IMAP).

Matching is deterministic: a reply is tied to the outbound message it answers
through In-Reply-To/References, falling back to the sender's address. Opt-out
words and bounces are acted on here, in code, before any model sees the mail —
suppression never depends on a classifier.
"""

from __future__ import annotations

import imaplib
import re
from dataclasses import dataclass, field
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Any, Protocol

from sqlalchemy import select

from app.core.config import Settings
from app.core.ids import stable_key
from app.database.models import Contact, FollowUp, Message, Opportunity

OPT_OUT_RE = re.compile(r"\b(unsubscribe|remove me|opt[ -]?out|stop (?:emailing|contacting|sending))\b", re.I)
BOUNCE_SENDERS = ("mailer-daemon", "postmaster", "mail delivery")
BOUNCE_SUBJECTS = ("undeliver", "delivery status notification", "returned mail", "delivery failure", "failure notice", "mail delivery failed")
AUTO_REPLY_SUBJECTS = ("out of office", "automatic reply", "auto-reply", "autoreply", "away from", "on leave")
QUOTE_MARKERS = (re.compile(r"^On .+ wrote:\s*$", re.M), re.compile(r"^-{2,}\s*Original Message", re.M | re.I), re.compile(r"^From: ", re.M))


@dataclass
class InboundMail:
    message_id: str
    from_address: str
    subject: str
    text: str
    in_reply_to: list[str] = field(default_factory=list)
    is_bounce: bool = False
    bounced_recipients: list[str] = field(default_factory=list)
    is_auto_reply: bool = False


class Inbox(Protocol):
    def fetch_new(self) -> list[InboundMail]: ...


def _body_text(message: EmailMessage) -> str:
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    content = part.get_content()
    if part.get_content_type() == "text/html":
        from app.tools.fetch import html_to_text

        content = html_to_text(content)[1]
    return content


def strip_quoted(text: str) -> str:
    """Keep what the person wrote, not the thread they replied to."""
    text = text.replace("\r\n", "\n")
    cut = len(text)
    for marker in QUOTE_MARKERS:
        match = marker.search(text)
        if match:
            cut = min(cut, match.start())
    lines = [line for line in text[:cut].splitlines() if not line.startswith(">")]
    return "\n".join(lines).strip()


def parse(raw: bytes) -> InboundMail:
    message = message_from_bytes(raw, policy=policy.default)
    subject = str(message.get("Subject", ""))
    sender = parseaddr(str(message.get("From", "")))[1].lower()
    refs = " ".join(str(message.get(h, "")) for h in ("In-Reply-To", "References"))
    in_reply_to = re.findall(r"<[^>]+>", refs)

    bounced: list[str] = []
    is_bounce = message.get_content_type() == "multipart/report" or (
        any(s in sender for s in BOUNCE_SENDERS) and any(s in subject.lower() for s in BOUNCE_SUBJECTS)
    )
    if is_bounce:
        for part in message.walk():
            if part.get_content_type() == "message/delivery-status":
                payload = part.get_payload()
                blocks = payload if isinstance(payload, list) else [payload]
                for block in blocks:
                    block_text = str(block)
                    if re.search(r"Action:\s*failed", block_text, re.I):
                        bounced += [m.lower() for m in re.findall(r"Final-Recipient:\s*rfc822;\s*([^\s]+)", block_text, re.I)]
            if part.get_content_type() == "message/rfc822" or part.get_content_type() == "text/rfc822-headers":
                original = str(part.get_payload(0) if part.is_multipart() else part.get_payload())
                in_reply_to += re.findall(r"Message-ID:\s*(<[^>]+>)", original, re.I)
        if not bounced:
            # Non-standard bounce: collect every address in it. Only addresses
            # that belong to known contacts are ever acted on.
            from app.tools.fetch import EMAIL_RE

            bounced = sorted({e.lower() for e in EMAIL_RE.findall(str(message)) if "mailer-daemon" not in e.lower()})
    auto = str(message.get("Auto-Submitted", "")).lower().startswith("auto-") or any(
        s in subject.lower() for s in AUTO_REPLY_SUBJECTS
    )
    text = "" if is_bounce else strip_quoted(_body_text(message))
    return InboundMail(
        message_id=str(message.get("Message-ID", "")).strip() or stable_key(sender, subject, text[:200]),
        from_address=sender,
        subject=subject[:300],
        text=text[:8000],
        in_reply_to=in_reply_to,
        is_bounce=is_bounce,
        bounced_recipients=sorted(set(bounced)),
        is_auto_reply=auto and not is_bounce,
    )


class ImapInbox:
    def __init__(self, settings: Settings, batch: int = 50) -> None:
        self.settings = settings
        self.batch = batch

    def fetch_new(self) -> list[InboundMail]:
        s = self.settings
        mails: list[InboundMail] = []
        with imaplib.IMAP4_SSL(s.imap_host, s.imap_port) as client:
            client.login(s.imap_username, s.imap_password)
            client.select(s.imap_folder)
            status, data = client.search(None, "UNSEEN")
            if status != "OK":
                return []
            for number in data[0].split()[: self.batch]:
                status, parts = client.fetch(number, "(RFC822)")  # marks the message \\Seen
                if status == "OK" and parts and isinstance(parts[0], tuple):
                    mails.append(parse(parts[0][1]))
        return mails


def build_inbox(settings: Settings) -> Inbox | None:
    if settings.nexus_mode != "production":
        return None
    if not (settings.imap_host and settings.imap_username and settings.imap_password):
        return None
    return ImapInbox(settings)


def _stop_followups(ctx: Any, contact_id: str, reason: str) -> None:
    for row in ctx.session.scalars(
        select(FollowUp).where(FollowUp.contact_id == contact_id, FollowUp.status == "scheduled")
    ):
        row.status = "stopped"
        row.stop_reason = reason[:160]


def ingest(ctx: Any, mails: list[InboundMail]) -> dict[str, int]:
    """Store new inbound mail and apply deterministic suppression."""
    counts = {"received": 0, "bounces": 0, "opt_outs": 0, "auto_replies": 0, "unmatched": 0, "duplicates": 0}
    for mail in mails:
        if ctx.session.scalar(
            select(Message.id).where(Message.direction == "inbound", Message.provider_message_id == mail.message_id)
        ):
            counts["duplicates"] += 1
            continue

        if mail.is_bounce:
            for address in mail.bounced_recipients:
                for contact in ctx.session.scalars(select(Contact).where(Contact.email == address)):
                    contact.bounced = True
                    _stop_followups(ctx, contact.id, "address bounced")
                    ctx.audit.record("hard_bounce", summary=f"{address} bounced", decision="block", contact_id=contact.id)
            ctx.session.add(Message(
                direction="inbound", subject=mail.subject, body="", status="bounce", provider="imap",
                provider_message_id=mail.message_id[:120], simulated=False, from_address=mail.from_address[:200],
                dedupe_key=stable_key("inbound", mail.message_id),
            ))
            counts["bounces"] += 1
            continue

        outbound = None
        for ref in mail.in_reply_to:
            outbound = ctx.session.scalar(
                select(Message).where(Message.direction == "outbound", Message.provider_message_id == ref)
            )
            if outbound is not None:
                break
        contact = ctx.session.get(Contact, outbound.contact_id) if outbound and outbound.contact_id else None
        if contact is None:
            contact = ctx.session.scalar(
                select(Contact).where(Contact.email == mail.from_address).order_by(Contact.created_at.desc())
            )
        opportunity_id = outbound.opportunity_id if outbound else None
        if opportunity_id is None and contact is not None:
            opportunity_id = ctx.session.scalar(
                select(Opportunity.id).where(Opportunity.contact_id == contact.id).order_by(Opportunity.created_at.desc())
            )

        status = "received"
        if mail.is_auto_reply:
            status = "auto_reply"
            counts["auto_replies"] += 1
        elif contact is None:
            status = "unmatched"
            counts["unmatched"] += 1
            ctx.audit.record(
                "unmatched_inbound",
                summary=f"mail from {mail.from_address}: {mail.subject}"[:300],
                decision="escalate",
                review_key=stable_key("unmatched_inbound", mail.message_id),
                reasons=["reply could not be tied to a sent message or known contact"],
                action_preview={"from": mail.from_address, "subject": mail.subject, "body": mail.text[:2000]},
            )
        else:
            counts["received"] += 1

        # Deterministic opt-out, whatever the classifier later says.
        if contact is not None and OPT_OUT_RE.search(f"{mail.subject}\n{mail.text[:500]}"):
            ctx.memory.opt_out_contact(contact.id, reason="unsubscribe")
            _stop_followups(ctx, contact.id, "opted out")
            counts["opt_outs"] += 1

        ctx.session.add(Message(
            opportunity_id=opportunity_id,
            contact_id=contact.id if contact else None,
            direction="inbound",
            subject=mail.subject,
            body=mail.text,
            status=status,
            provider="imap",
            provider_message_id=mail.message_id[:120],
            simulated=False,
            thread_ref=(mail.in_reply_to[0] if mail.in_reply_to else None),
            from_address=mail.from_address[:200],
            dedupe_key=stable_key("inbound", mail.message_id),
        ))
        ctx.session.flush()
    return counts


def poll(ctx: Any) -> dict[str, int]:
    inbox = getattr(ctx, "inbox", None)
    if inbox is None:
        return {"polled": 0}
    try:
        mails = inbox.fetch_new()
    except (imaplib.IMAP4.error, OSError) as exc:
        ctx.audit.record("inbox_error", summary=f"IMAP poll failed: {exc}", decision="allow")
        return {"polled": 0, "error": 1}
    result = ingest(ctx, mails)
    result["polled"] = len(mails)
    return result

