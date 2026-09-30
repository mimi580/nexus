"""Email channel: MIME, footer, unsubscribe tokens, SMTP behaviour, inbound parsing and ingest."""

from __future__ import annotations

import smtplib

import pytest
from sqlalchemy import select

from app.agents.outreach import OutreachAgent
from app.core.config import Settings
from app.core.types import ProductCategory
from app.database.models import Contact, FollowUp, Message, ReviewItem
from app.execution import email as email_mod
from app.execution.inbound import InboundMail, ingest, parse, strip_quoted
from app.scheduler.jobs import process_inbound

LAPTOP = ProductCategory.LAPTOP.value


def _settings(**overrides) -> Settings:
    base = dict(
        _env_file=None, email_sender_address="sales@nexus-trade.example", email_sender_name="Francis",
        email_reply_to="sales@nexus-trade.example", business_name="Leonard Trading Ltd",
        business_postal_address="P.O. Box 1234-00100, Nairobi, Kenya", public_base_url="https://nexus.example",
        unsubscribe_secret="x" * 32, smtp_host="smtp.example", smtp_username="sales@nexus-trade.example",
        smtp_password="pw", email_provider="smtp",
    )
    base.update(overrides)
    return Settings(**base)


# ------------------------------------------------------------------ outbound


def test_mime_headers_support_one_click_unsubscribe_and_threading():
    settings = _settings()
    message = email_mod.build_mime(
        settings, to="buyer@clinic.example", subject="Laptop supply", body="Hello",
        message_id="<abc@nexus-trade.example>", unsubscribe_url="https://nexus.example/u/tok",
        in_reply_to="<first@nexus-trade.example>",
    )
    assert message["From"] == "Francis <sales@nexus-trade.example>"
    assert message["Message-ID"] == "<abc@nexus-trade.example>"
    assert "<https://nexus.example/u/tok>" in message["List-Unsubscribe"]
    assert "mailto:sales@nexus-trade.example?subject=unsubscribe" in message["List-Unsubscribe"]
    assert message["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert message["In-Reply-To"] == "<first@nexus-trade.example>"


def test_footer_identifies_the_sender_and_offers_opt_out():
    footer = email_mod.compliance_footer(_settings(), "https://nexus.example/u/t")
    assert "Leonard Trading Ltd" in footer and "P.O. Box 1234-00100" in footer
    assert "unsubscribe" in footer and "https://nexus.example/u/t" in footer


def test_unsubscribe_tokens_cannot_be_forged():
    settings = _settings()
    token = email_mod.unsubscribe_token(settings, "ctc_123")
    assert email_mod.verify_unsubscribe_token(settings, token) == "ctc_123"
    assert email_mod.verify_unsubscribe_token(settings, "ctc_999." + token.split(".")[1]) is None
    assert email_mod.verify_unsubscribe_token(settings, "garbage") is None
    assert email_mod.verify_unsubscribe_token(_settings(unsubscribe_secret="y" * 32), token) is None


class FakeSMTP:
    instances: list["FakeSMTP"] = []
    refuse = False

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port = host, port
        self.calls: list[str] = []
        self.sent = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(f"login:{user}")

    def send_message(self, message):
        if FakeSMTP.refuse:
            raise smtplib.SMTPRecipientsRefused({message["To"]: (550, b"no such user")})
        self.sent.append(message)
        return {}


@pytest.fixture
def fake_smtp(monkeypatch):
    FakeSMTP.instances = []
    FakeSMTP.refuse = False
    monkeypatch.setattr(email_mod.smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


def test_smtp_provider_uses_starttls_and_returns_the_message_id(fake_smtp):
    provider = email_mod.SmtpEmailProvider(_settings())
    result = provider.send(to="buyer@clinic.example", subject="Hi", body="Hello", context={"message_id": "<m1@x>"})
    assert result.ok and result.provider_message_id == "<m1@x>" and result.simulated is False
    assert fake_smtp.instances[0].calls == ["starttls", "login:sales@nexus-trade.example"]


def test_refused_recipient_is_a_permanent_failure(fake_smtp):
    fake_smtp.refuse = True
    result = email_mod.SmtpEmailProvider(_settings()).send(to="nobody@clinic.example", subject="Hi", body="x", context={})
    assert not result.ok and result.permanent_failure


def test_production_without_smtp_never_pretends_to_send():
    provider = email_mod.build_email_provider(_settings(nexus_mode="production", smtp_host=None))
    result = provider.send(to="a@b.example", subject="s", body="b", context={})
    assert result.ok is False and "SMTP_HOST" in result.error


def _opportunity(ctx, email="amina@buyer.example", name="Buyer Co"):
    company, _ = ctx.memory.upsert_company(name=name, domain=f"{name.split()[0].lower()}.example", country="Kenya")
    contact = ctx.memory.upsert_contact(
        company_id=company.id, full_name="Amina Yusuf", role="IT Manager", email=email,
        confidence=0.7, source="https://buyer.example/contact",
    )
    opportunity, _ = ctx.memory.create_opportunity(company.id, LAPTOP)
    opportunity.contact_id = contact.id
    opportunity.economics = {"quantity": 40, "lead_time_days": 12}
    opportunity.qualification = {"strategy": {"message_angle": "lead time", "followup_cadence_days": 4}}
    ctx.session.flush()
    return opportunity, contact


def test_sent_mail_carries_the_footer_and_a_hard_bounce_suppresses_the_contact(ctx, fake_smtp):
    ctx.settings = _settings(nexus_mode="simulation")
    ctx.email = email_mod.SmtpEmailProvider(ctx.settings)
    opportunity, contact = _opportunity(ctx)
    OutreachAgent().run(ctx, {"opportunity_id": opportunity.id, "regulatory_checked": True})
    sent = fake_smtp.instances[-1].sent[0]
    body = sent.get_content()
    assert "Leonard Trading Ltd" in body and "https://nexus.example/u/" in body
    stored = ctx.session.scalar(select(Message).where(Message.direction == "outbound"))
    assert stored.provider_message_id == sent["Message-ID"]

    fake_smtp.refuse = True
    other, bad = _opportunity(ctx, email="gone@other.example", name="Other Co")
    OutreachAgent().run(ctx, {"opportunity_id": other.id, "regulatory_checked": True})
    assert bad.bounced is True


# ------------------------------------------------------------------ inbound parsing

REPLY = (
    b"From: Amina Yusuf <amina@buyer.example>\r\nTo: sales@nexus-trade.example\r\n"
    b"Subject: Re: Laptop supply\r\nMessage-ID: <reply1@buyer.example>\r\n"
    b"In-Reply-To: <out1@nexus-trade.example>\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
    b"Please send pricing for 40 units.\r\n\r\nOn Mon, 21 Sep 2026 Francis wrote:\r\n> Hello Amina\r\n"
)

DSN = (
    b"From: Mail Delivery Subsystem <mailer-daemon@googlemail.com>\r\nTo: sales@nexus-trade.example\r\n"
    b"Subject: Delivery Status Notification (Failure)\r\nMessage-ID: <dsn1@google>\r\n"
    b"MIME-Version: 1.0\r\nContent-Type: multipart/report; report-type=delivery-status; boundary=\"B\"\r\n\r\n"
    b"--B\r\nContent-Type: text/plain\r\n\r\nYour message wasn't delivered.\r\n"
    b"--B\r\nContent-Type: message/delivery-status\r\n\r\nReporting-MTA: dns; google.com\r\n\r\n"
    b"Final-Recipient: rfc822; gone@buyer.example\r\nAction: failed\r\nStatus: 5.1.1\r\n\r\n"
    b"--B--\r\n"
)

OOO = (
    b"From: amina@buyer.example\r\nSubject: Automatic reply: Laptop supply\r\nMessage-ID: <ooo@buyer>\r\n"
    b"Auto-Submitted: auto-replied\r\nContent-Type: text/plain\r\n\r\nI am out of the office until Monday.\r\n"
)


def test_reply_parsing_keeps_only_the_new_text():
    mail = parse(REPLY)
    assert mail.from_address == "amina@buyer.example"
    assert mail.in_reply_to == ["<out1@nexus-trade.example>"]
    assert mail.text == "Please send pricing for 40 units."
    assert not mail.is_bounce and not mail.is_auto_reply


def test_dsn_bounce_is_recognised_with_the_failed_recipient():
    mail = parse(DSN)
    assert mail.is_bounce and mail.bounced_recipients == ["gone@buyer.example"]


def test_auto_reply_is_recognised():
    assert parse(OOO).is_auto_reply


def test_strip_quoted_handles_outlook_style_history():
    text = "Yes please.\n\n-----Original Message-----\nFrom: Francis\nSent: Monday"
    assert strip_quoted(text) == "Yes please."


# ------------------------------------------------------------------ ingest


class ListInbox:
    def __init__(self, mails):
        self.mails = mails

    def fetch_new(self):
        mails, self.mails = self.mails, []
        return mails


def test_reply_is_threaded_to_its_opportunity_and_classified(ctx):
    opportunity, contact = _opportunity(ctx)
    ctx.session.add(Message(
        opportunity_id=opportunity.id, contact_id=contact.id, direction="outbound", subject="Laptop supply",
        body="Hello", status="sent", provider_message_id="<out1@nexus-trade.example>", dedupe_key="d1",
    ))
    ctx.session.flush()
    ctx.inbox = ListInbox([parse(REPLY)])
    result = process_inbound(ctx)
    assert result["received"] == 1 and result["tasks_created"] == 1
    inbound = ctx.session.scalar(select(Message).where(Message.direction == "inbound"))
    assert inbound.opportunity_id == opportunity.id and inbound.status == "received"
    assert process_inbound(ctx)["tasks_created"] == 0  # idempotent


def test_opt_out_words_suppress_immediately_in_code(ctx):
    opportunity, contact = _opportunity(ctx)
    ctx.session.add(FollowUp(opportunity_id=opportunity.id, contact_id=contact.id, step=1, due_at=ctx.now))
    ctx.session.flush()
    counts = ingest(ctx, [InboundMail(message_id="<u1@b>", from_address="amina@buyer.example",
                                      subject="Re: Laptop supply", text="Please unsubscribe me.")])
    assert counts["opt_outs"] == 1
    assert contact.opted_out is True
    assert ctx.session.scalar(select(FollowUp)).status == "stopped"


def test_bounce_marks_the_contact(ctx):
    _, contact = _opportunity(ctx, email="gone@buyer.example")
    counts = ingest(ctx, [parse(DSN)])
    assert counts["bounces"] == 1 and contact.bounced is True


def test_unmatched_mail_goes_to_review_and_duplicates_are_ignored(ctx):
    stranger = InboundMail(message_id="<s1@x>", from_address="someone@else.example", subject="Hello", text="Who are you?")
    assert ingest(ctx, [stranger])["unmatched"] == 1
    assert ingest(ctx, [stranger])["duplicates"] == 1
    item = ctx.session.scalar(select(ReviewItem))
    assert item is not None and item.action_payload["from"] == "someone@else.example"


def test_auto_replies_do_not_become_conversations(ctx):
    _opportunity(ctx)
    counts = ingest(ctx, [parse(OOO)])
    assert counts["auto_replies"] == 1
    assert ctx.session.scalar(select(Message).where(Message.direction == "inbound")).status == "auto_reply"
