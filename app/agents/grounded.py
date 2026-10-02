"""Production research paths: every fact must come from a retrieved source.

The model reads search results and pages NEXUS actually fetched and proposes
organisations, contacts and signals, each citing a source_id. Deterministic
checks then accept a proposal only if:

- the cited source exists in this task's retrieved set,
- the organisation or person name appears in that source's text,
- an e-mail address appears verbatim on a page of the organisation's own site,
- a quoted signal appears in the cited source.

Anything that fails is dropped and counted, never "fixed up". In simulation the
agents keep their simulated world; these functions only run with live research.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from app.agents.market_research import score_market
from app.commercial import settings as commercial
from app.core.interfaces import AgentResult
from app.core.types import EvidenceKind, OpportunityStage, ProductCategory, utcnow
from app.database.models import Company, Contact, MarketAssessment, Opportunity, SourceDocument
from app.policies.licenses import canonical_country
from app.tools.research import appears, domain_of, email_appears, is_aggregator, payload

CATEGORY_WORDS = {
    ProductCategory.LAPTOP.value: "laptops computers",
    ProductCategory.IPHONE.value: "smartphones iPhone",
    ProductCategory.MEDICAL.value: "medical equipment",
    ProductCategory.PHARMA.value: "pharmaceuticals medicines",
    ProductCategory.SERVER_IT.value: "servers IT infrastructure",
}

from app.core import languages  # noqa: E402

PROSPECT_QUERIES = {
    ProductCategory.LAPTOP.value: [
        "{country} tender supply of laptops",
        "{country} university ICT equipment tender",
        "{country} NGO procurement notice computers",
    ],
    ProductCategory.IPHONE.value: [
        "{country} mobile phone wholesaler",
        "{country} smartphone retail chain",
        "{country} refurbished iPhone dealer",
    ],
    ProductCategory.MEDICAL.value: [
        "{country} hospital tender medical equipment",
        "{country} private hospital expansion diagnostic equipment",
        "{country} medical equipment procurement notice",
    ],
    ProductCategory.PHARMA.value: [
        "{country} pharmaceutical wholesale distributor",
        "{country} tender supply of pharmaceuticals",
        "{country} pharmacy chain",
    ],
    ProductCategory.SERVER_IT.value: [
        "{country} data centre operator",
        "{country} tender supply of servers and storage",
        "{country} bank ICT infrastructure tender",
    ],
}

# Business-listing (Google Maps) searches for buyer types, per category.
PLACE_QUERIES = {
    ProductCategory.LAPTOP.value: ["university in {country}", "international school in {country}"],
    ProductCategory.IPHONE.value: ["mobile phone wholesaler in {country}", "phone shop in {country}"],
    ProductCategory.MEDICAL.value: ["hospital in {country}", "diagnostic centre in {country}"],
    ProductCategory.PHARMA.value: ["pharmaceutical wholesaler in {country}", "pharmacy in {country}"],
    ProductCategory.SERVER_IT.value: ["data centre in {country}", "bank head office in {country}"],
}

LINKEDIN_ROLE_WORDS = (
    "procurement", "purchasing", "supply chain", "buyer", "sourcing", "pharmacist", "pharmacy",
    "biomedical", "ict", " it ", "information technology", "operations", "logistics", "stores",
)

MARKET_QUERY = "{country} demand for {words} imports procurement {year}"

CONTACT_PATHS = ("/contact", "/contact-us", "/procurement", "/tenders", "/about", "/about-us", "/")
PROFILE_PATHS = ("/", "/about", "/about-us")

ROLE_MAILBOX_PRIORITY = (
    "procurement", "purchasing", "tenders", "tender", "supplychain", "supply", "supplies",
    "sourcing", "buying", "orders", "info", "contact", "enquiries", "inquiries", "admin",
)
ROLE_MAILBOX_NAME = "Procurement office"
ROLE_MAILBOX_ROLE = "published role mailbox"

FACTOR_KEYS = (
    "demand", "procurement", "purchasing_power", "supplier_availability",
    "logistics", "competition", "payment_risk", "regulation",
)


def _by_id(docs: list[SourceDocument]) -> dict[str, SourceDocument]:
    return {doc.id: doc for doc in docs}


def _doc_text(doc: SourceDocument) -> str:
    return f"{doc.title}\n{doc.text}"


def _clamp(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, number))


# --------------------------------------------------------------------- markets


def research_markets(agent: Any, ctx: Any, task_input: dict) -> AgentResult:
    category = task_input["product_category"]
    weights = task_input.get("weights")
    top_n = int(task_input.get("top_n", 3))
    candidates = commercial.get(ctx.session)["target_markets"].get(category) or []
    if not candidates:
        return agent.fail(f"no target markets configured for {category}")

    words = CATEGORY_WORDS.get(category, category)
    docs_by_country: dict[str, list[SourceDocument]] = {}
    for country in candidates[:8]:
        query = MARKET_QUERY.format(country=country, words=words, year=ctx.now.year)
        docs_by_country[country] = ctx.research.search(query, country=country, count=5)
    all_docs = [doc for docs in docs_by_country.values() for doc in docs]
    if not all_docs:
        return agent.fail("market research returned no sources")

    data, cost = agent.ask(
        ctx,
        (
            f"Assess each candidate country as a market for {category}. Score each factor from 0 to 1 "
            f"({', '.join(FACTOR_KEYS)}; for competition, payment_risk and regulation higher means MORE "
            "competition/risk/burden). Use the supplied sources where they speak to a factor and cite them; "
            "otherwise use general knowledge and say so in the rationale. Return "
            "{'markets':[{'country','region','factors':{...},'rationale','source_ids':[]}]}."
        ),
        {
            "product_category": category,
            "candidate_countries": candidates,
            "sources": {country: payload(docs, 800) for country, docs in docs_by_country.items()},
        },
    )
    docs = _by_id(all_docs)
    scored, evidence = [], []
    for market in data.get("markets") or []:
        country = canonical_country(market.get("country"))
        if country not in candidates:
            continue
        factors = {k: v for k in FACTOR_KEYS if (v := _clamp((market.get("factors") or {}).get(k))) is not None}
        cited = [docs[s] for s in (market.get("source_ids") or []) if s in docs]
        score = score_market(factors, weights)
        ctx.session.add(
            MarketAssessment(
                country=country,
                region=str(market.get("region") or ""),
                product_category=category,
                score=score,
                factors=factors,
                rationale=str(market.get("rationale") or "")[:4000],
                expires_at=ctx.now + timedelta(days=14),
            )
        )
        scored.append({"country": country, "score": score, "sources": len(cited)})
        evidence.append(
            agent.evidence(
                f"{country} attractiveness for {category}: {score} ({len(cited)} cited sources)",
                source=cited[0].url if cited else "model inference (no cited source)",
                kind=EvidenceKind.INFERENCE,
                confidence=0.55 if cited else 0.35,
                subject_type="market",
                subject_id=country,
                source_type="web" if cited else "model",
            )
        )
    ctx.session.flush()
    agent.persist_evidence(ctx, evidence)
    if not scored:
        return agent.fail("no candidate market could be assessed", cost_usd=cost)
    scored.sort(key=lambda m: m["score"], reverse=True)
    top = scored[:top_n]
    return agent.ok(
        output={"ranked_markets": scored, "selected": top},
        cost_usd=cost,
        evidence=evidence,
        next_tasks=[
            {
                "agent": "prospect_discovery",
                "input": {
                    "product_category": category,
                    "countries": [m["country"] for m in top],
                    "limit": int(task_input.get("prospect_limit", 6)),
                },
                "priority": 70,
            }
        ],
        notes=[f"top markets: {', '.join(m['country'] for m in top)}"],
    )


# --------------------------------------------------------------------- prospects


def discover_prospects(agent: Any, ctx: Any, task_input: dict) -> AgentResult:
    category = task_input["product_category"]
    countries = [canonical_country(c) for c in (task_input.get("countries") or [])]
    limit = int(task_input.get("limit", 6))
    per_country = max(1, int(ctx.settings.research_max_queries_per_task))
    templates = PROSPECT_QUERIES.get(category, ["{country} " + category])

    docs: list[SourceDocument] = []
    doc_country: dict[str, str] = {}
    directories = (commercial.get(ctx.session)["directories"] or {}).get("buyer") or []
    words = CATEGORY_WORDS.get(category, category)
    for country in countries:
        queries = [t.format(country=country) for t in templates[:per_country]]
        queries += [f"site:{d} {words} {country}" for d in directories[:2]]
        # Where buyers publish in Arabic, Turkish or Hebrew, search in that language too.
        local = languages.language_for(ctx.session, country)
        local_templates = languages.PROSPECT_QUERIES.get(local, {}).get(category, [])
        queries += [t.format(country=languages.country_name(local, country)) for t in local_templates[:per_country]]
        for query in queries:
            for doc in ctx.research.search(query, country=country, count=8):
                if doc.id not in doc_country:
                    docs.append(doc)
                    doc_country[doc.id] = country

    # Business listings: named organisations with their own websites, accepted
    # directly (the listing itself is the source) when the website is theirs.
    created = duplicates = rejected = listed = 0
    next_tasks, evidence = [], []
    if ctx.research.has_places:
        week = int(ctx.now.strftime("%W"))
        place_templates = PLACE_QUERIES.get(category, [])
        for country in countries:
            if not place_templates or created >= limit:
                break
            query = place_templates[week % len(place_templates)].format(country=country)
            local = languages.language_for(ctx.session, country)
            local_places = languages.PLACE_QUERIES.get(local, {}).get(category, [])
            if local_places and week % 2:  # alternate weeks between English and local-language listings
                query = local_places[(week // 2) % len(local_places)].format(country=languages.country_name(local, country))
            for place in ctx.research.places(query, country=country, count=10):
                if created >= limit:
                    break
                website = place.domain if place.domain and not is_aggregator(place.domain) else None
                if not website:
                    continue
                company, is_new = ctx.memory.upsert_company(
                    name=place.title, domain=website, country=country,
                    segment=None,
                    source=place.url[:200], buying_signals=[],
                )
                opportunity, opp_new = ctx.memory.create_opportunity(
                    company_id=company.id, product_category=category, objective_id=ctx.objective_id
                )
                created += int(is_new)
                duplicates += int(not is_new)
                listed += int(is_new)
                evidence.append(agent.evidence(
                    f"{company.name} listed as a business in {country} ({query})", source=place.url,
                    kind=EvidenceKind.UNVERIFIED_CLAIM, confidence=0.5, subject_type="company",
                    subject_id=company.id, source_type="web",
                ))
                if opp_new:
                    next_tasks.append({"agent": "company_intelligence", "input": {"opportunity_id": opportunity.id}, "priority": 64})

    if not docs:
        agent.persist_evidence(ctx, evidence)
        return agent.ok(output={"created": created, "duplicates": duplicates, "rejected": 0, "from_listings": listed},
                        evidence=evidence, next_tasks=next_tasks,
                        notes=["web searches returned nothing" + (f"; {listed} from business listings" if listed else "")])

    data, cost = agent.ask(
        ctx,
        (
            f"From these search results, list organisations that could plausibly buy {category}. "
            "Only include organisations NAMED in a result. For each give the exact name as written, "
            "the source_id it appears in, a short verbatim quote from that result showing why it is "
            "relevant, a segment, and its own website URL only if the result shows it. Return "
            "{'prospects':[{'name','source_id','evidence_quote','segment','website_url'}]}."
        ),
        {"product_category": category, "countries": countries, "results": payload(docs, 600)},
    )
    lookup = _by_id(docs)
    for item in (data.get("prospects") or [])[: limit * 2]:
        if created + duplicates >= limit:
            break
        doc = lookup.get(item.get("source_id") or "")
        name = (item.get("name") or "").strip()
        if doc is None or not appears(name, _doc_text(doc)):
            rejected += 1
            continue
        quote = (item.get("evidence_quote") or "").strip()
        signals = [quote[:300]] if quote and appears(quote, _doc_text(doc)) else []
        website = item.get("website_url") or ""
        domain = None
        if website and not is_aggregator(website):
            candidate = domain_of(website)
            if candidate and (candidate == doc.domain or appears(candidate, _doc_text(doc))):
                domain = candidate
        company, is_new = ctx.memory.upsert_company(
            name=name,
            domain=domain,
            country=doc_country[doc.id],
            segment=(item.get("segment") or None),
            description="",
            buying_signals=signals,
            source=doc.url[:200],
        )
        opportunity, opp_new = ctx.memory.create_opportunity(
            company_id=company.id, product_category=category, objective_id=ctx.objective_id
        )
        created += int(is_new)
        duplicates += int(not is_new)
        evidence.append(
            agent.evidence(
                f"{company.name} named in source as relevant to {category}" + (f": \"{signals[0]}\"" if signals else ""),
                source=doc.url,
                kind=EvidenceKind.UNVERIFIED_CLAIM,
                confidence=0.55 if signals else 0.45,
                subject_type="company",
                subject_id=company.id,
                source_type="web",
            )
        )
        if opp_new:
            next_tasks.append(
                {"agent": "company_intelligence", "input": {"opportunity_id": opportunity.id}, "priority": 65}
            )
    agent.persist_evidence(ctx, evidence)
    return agent.ok(
        output={"created": created, "duplicates": duplicates, "rejected": rejected, "sources": len(docs),
                "from_listings": listed},
        cost_usd=cost,
        evidence=evidence,
        next_tasks=next_tasks,
        notes=[f"{created} new, {duplicates} known, {rejected} proposals rejected as not in any source"],
    )


# --------------------------------------------------------------------- company


def _find_website(ctx: Any, company: Company) -> str | None:
    hits = ctx.research.search(f"\"{company.name}\" {company.country or ''} official website", country=company.country, count=6)
    for doc in hits:
        if not is_aggregator(doc.url) and appears(company.name, doc.title):
            return doc.domain
    return None


def company_intelligence(agent: Any, ctx: Any, opportunity: Opportunity, company: Company) -> AgentResult:
    if not company.domain:
        company.domain = _find_website(ctx, company)
    docs: list[SourceDocument] = []
    if company.domain:
        docs += ctx.research.fetch_site(company.domain, PROFILE_PATHS, limit=2)
    words = CATEGORY_WORDS.get(opportunity.product_category, opportunity.product_category)
    docs += ctx.research.search(f"\"{company.name}\" {company.country or ''} {words}", country=company.country, count=5)
    if not docs:
        ctx.memory.transition(opportunity, OpportunityStage.STALE, "no sources found for this organisation")
        return agent.ok(output={"sources": 0}, notes=["nothing retrievable about this organisation"])

    data, cost = agent.ask(
        ctx,
        (
            f"Using only these sources, summarise what {company.name} does and any evidence it needs "
            f"{opportunity.product_category}. Every signal must be a verbatim quote with its source_id. "
            "Return {'summary','size_indicator','size_quote','size_source_id',"
            "'signals':[{'quote','source_id'}],'needs':[]}."
        ),
        {"company_name": company.name, "country": company.country, "product_category": opportunity.product_category,
         "sources": payload(docs)},
    )
    lookup = _by_id(docs)
    accepted, evidence = [], []
    for signal in data.get("signals") or []:
        doc = lookup.get(signal.get("source_id") or "")
        quote = (signal.get("quote") or "").strip()
        if doc is not None and appears(quote, _doc_text(doc)):
            accepted.append(quote[:300])
            evidence.append(
                agent.evidence(
                    f"{company.name}: \"{quote[:300]}\"", source=doc.url, kind=EvidenceKind.UNVERIFIED_CLAIM,
                    confidence=0.6, subject_type="company", subject_id=company.id, source_type="web",
                )
            )
    size_doc = lookup.get(data.get("size_source_id") or "")
    if size_doc is not None and appears(data.get("size_quote"), _doc_text(size_doc)) and data.get("size_indicator"):
        company.size_indicator = str(data["size_indicator"])[:120]
    company.buying_signals = list(dict.fromkeys([*(company.buying_signals or []), *accepted]))
    company.description = str(data.get("summary") or "")[:2000]
    company.last_verified_at = utcnow()
    agent.persist_evidence(ctx, evidence)
    ctx.memory.transition(opportunity, OpportunityStage.RESEARCHED, f"profile from {len(docs)} sources")
    return agent.ok(
        output={"signals": company.buying_signals, "sources": len(docs), "website": company.domain},
        cost_usd=cost,
        evidence=evidence,
        next_tasks=[{"agent": "decision_maker_discovery", "input": {"opportunity_id": opportunity.id}, "priority": 60}],
    )


# --------------------------------------------------------------------- contacts


def _on_site(doc: SourceDocument, company_domain: str) -> bool:
    return doc.domain == company_domain or doc.domain.endswith("." + company_domain)


def _role_mailbox(emails: list[str]) -> str | None:
    for prefix in ROLE_MAILBOX_PRIORITY:
        for email in emails:
            local = email.split("@", 1)[0].lower().replace(".", "").replace("-", "").replace("_", "")
            if local.startswith(prefix):
                return email
    return None


def decision_makers(agent: Any, ctx: Any, opportunity: Opportunity, company: Company, task_input: dict) -> AgentResult:
    if not company.domain:
        ctx.memory.transition(opportunity, OpportunityStage.STALE, "no website: no published contact to use")
        return agent.ok(output={"contacts": 0}, notes=["no organisation website; contacts are only taken from it"])
    pages = ctx.research.fetch_site(company.domain, CONTACT_PATHS, limit=int(ctx.settings.research_max_pages_per_task))
    site_pages = [doc for doc in pages if _on_site(doc, company.domain)]
    excluded = set()
    if task_input.get("exclude_contact_id"):
        old = ctx.session.get(Contact, task_input["exclude_contact_id"])
        if old is not None and old.email:
            excluded.add(old.email.lower())
    from app.tools.fetch import extract_emails

    on_site_emails = sorted(
        {e for doc in site_pages for e in extract_emails(doc.text) if e not in excluded}
    )
    if not on_site_emails:
        ctx.memory.transition(opportunity, OpportunityStage.STALE, "no e-mail address published on the organisation's site")
        return agent.ok(output={"contacts": 0}, notes=["no published address found"])

    data, cost = agent.ask(
        ctx,
        (
            f"From these pages of {company.name}'s own website, choose the best person or mailbox for a "
            f"{opportunity.product_category} supply enquiry (procurement, purchasing, supply chain, pharmacy, "
            "biomedical, ICT). The e-mail MUST be one of 'published_emails'. Give the person's name exactly as "
            "written on the page, or null if the address is not tied to a named person. Return "
            "{'full_name','role','email','source_id'}."
        ),
        {"company_name": company.name, "product_category": opportunity.product_category,
         "published_emails": on_site_emails, "pages": payload(site_pages)},
    )
    lookup = _by_id(site_pages)
    email = (data.get("email") or "").strip().lower()
    doc = lookup.get(data.get("source_id") or "")
    full_name = (data.get("full_name") or "").strip() or None
    role = (data.get("role") or "").strip() or None
    source_doc = None
    if email in on_site_emails and doc is not None and email_appears(email, doc.text):
        source_doc = doc
        if full_name and not appears(full_name, doc.text):
            full_name = None  # a name we cannot see on the page is not used
    else:
        email = _role_mailbox(on_site_emails) or ""
        full_name, role = None, None
        source_doc = next((d for d in site_pages if email and email_appears(email, d.text)), None)
    if not email or source_doc is None:
        ctx.memory.transition(opportunity, OpportunityStage.STALE, "no suitable published address")
        return agent.ok(output={"contacts": 0}, cost_usd=cost, notes=["no address met the evidence rules"])

    named = full_name is not None
    linkedin = None
    if not named:
        linkedin = _linkedin_person(ctx, company)
    record = ctx.memory.record_evidence(
        agent.evidence(
            f"{email} published on {source_doc.url}" + (f" for {full_name} ({role})" if named else ""),
            source=source_doc.url,
            kind=EvidenceKind.VERIFIED_FACT,
            confidence=0.7 if named else 0.6,
            subject_type="company",
            subject_id=company.id,
            source_type="web",
        )
    )
    contact = ctx.memory.upsert_contact(
        company_id=company.id,
        full_name=full_name or ROLE_MAILBOX_NAME,
        role=role if named else ROLE_MAILBOX_ROLE,
        email=email,
        confidence=0.7 if named else 0.6,
        source=source_doc.url[:200],
        evidence_id=record.id,
        verified=True,
    )
    if linkedin and contact.full_name == ROLE_MAILBOX_NAME:
        # Address the right person by name, at the organisation's published mailbox.
        contact.full_name = linkedin["name"]
        contact.role = f"{linkedin['role']} (via published mailbox)"
        contact.linkedin_url = linkedin["url"][:300]
        ctx.memory.record_evidence(agent.evidence(
            f"{linkedin['name']} is {linkedin['role']} at {company.name} (public LinkedIn search result)",
            source=linkedin["url"], kind=EvidenceKind.UNVERIFIED_CLAIM, confidence=0.5,
            subject_type="company", subject_id=company.id, source_type="web",
        ))
    opportunity.contact_id = contact.id
    ctx.session.flush()
    return agent.ok(
        output={"contact_id": contact.id, "named": named or bool(linkedin), "source": source_doc.url,
                "linkedin": bool(linkedin)},
        cost_usd=cost,
        next_tasks=[{"agent": "qualification", "input": {"opportunity_id": opportunity.id}, "priority": 60}],
    )


def _linkedin_person(ctx: Any, company: Company) -> dict[str, str] | None:
    """The procurement-relevant person at this organisation, from public LinkedIn
    search results only (NEXUS never visits or scrapes LinkedIn itself).

    Accepted only when the result names the organisation and the job title is
    procurement-relevant. Used to address a published mailbox by name.
    """
    query = f'"{company.name}" procurement OR purchasing OR "supply chain" site:linkedin.com/in'
    try:
        results = ctx.research.search(query, country=company.country, count=5)
    except Exception:  # enrichment is optional; never fail contact discovery over it
        return None
    for doc in results:
        if "linkedin.com/in/" not in doc.url.lower():
            continue
        if not appears(company.name, _doc_text(doc)):
            continue
        parts = [p.strip() for p in re.split(r"\s[-\u2013|]\s", doc.title) if p.strip()]
        if len(parts) < 2:
            continue
        name, role = parts[0], parts[1]
        if not (2 <= len(name.split()) <= 4) or any(ch.isdigit() for ch in name):
            continue
        if not any(word.strip() in f" {role.lower()} " for word in LINKEDIN_ROLE_WORDS):
            continue
        return {"name": name[:120], "role": role[:120], "url": doc.url}
    return None
