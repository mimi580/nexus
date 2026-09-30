"""Research service: budgeted search + fetch, persisted as source documents.

Every search result and page NEXUS reads is stored as a SourceDocument, so
each claim an agent makes can point at the exact text it came from. The
grounding helpers below are how agents check that a name, e-mail address or
quote actually appears in a retrieved source before accepting it.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.budget.controller import BudgetController
from app.core.config import Settings
from app.core.types import utcnow
from app.database.models import SourceDocument
from app.tools.fetch import FetchedPage, Fetcher, FetchRefused, PageFetcher
from app.tools.search import SearchProvider, build_search_provider

SEARCH_CACHE_DAYS = 7
PAGE_CACHE_DAYS = 14
MAX_STORED_TEXT = 60_000
EXCERPT_CHARS = 2_500

# Sites that describe organisations but are not their own websites.
AGGREGATOR_DOMAINS = {
    "linkedin.com", "facebook.com", "twitter.com", "x.com", "instagram.com", "youtube.com",
    "wikipedia.org", "yellowpages", "businesslist", "kompass.com", "zoominfo.com", "crunchbase.com",
    "bloomberg.com", "dnb.com", "opencorporates.com", "google.com", "bing.com", "tripadvisor",
    "glassdoor", "indeed.com", "cybo.com", "africabizinfo", "yelp.com",
}


def domain_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def is_aggregator(url_or_domain: str) -> bool:
    domain = domain_of(url_or_domain) if "/" in url_or_domain else url_or_domain.lower()
    return any(marker in domain for marker in AGGREGATOR_DOMAINS)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9@.]+", " ", text.lower())
    return " ".join(text.split())


def appears(term: str | None, text: str | None) -> bool:
    """True when the term occurs in the text, ignoring case, accents and punctuation."""
    if not term or not text:
        return False
    needle = normalize(term)
    return bool(needle) and len(needle) >= 3 and needle in normalize(text)


def email_appears(email: str | None, text: str | None) -> bool:
    if not email or not text:
        return False
    return email.strip().lower() in text.lower()


class ResearchService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        budget: BudgetController,
        search: SearchProvider | None,
        fetcher: Fetcher | None,
        clock: Any = utcnow,
    ) -> None:
        self.session = session
        self.settings = settings
        self.budget = budget
        self.provider = search
        self.fetcher = fetcher
        self.clock = clock

    @property
    def live(self) -> bool:
        return self.provider is not None

    # ------------------------------------------------------------ search
    def search(self, query: str, *, country: str | None = None, count: int = 8) -> list[SourceDocument]:
        if self.provider is None:
            raise FetchRefused("no search provider configured (SEARCH_PROVIDER / SEARCH_API_KEY)")
        query = query.strip()[:480]
        since = self.clock() - timedelta(days=SEARCH_CACHE_DAYS)
        cached = list(
            self.session.scalars(
                select(SourceDocument).where(
                    SourceDocument.kind == "search_result",
                    SourceDocument.query == query,
                    SourceDocument.retrieved_at >= since,
                )
            )
        )
        if cached:
            return cached
        cost = float(self.settings.search_cost_per_query_usd)
        reservation = self.budget.reserve("research_data", cost, f"search:{self.provider.name}")
        try:
            hits = self.provider.search(query, country=country, count=count)
        except Exception:
            self.budget.release(reservation)
            raise
        self.budget.commit(reservation, cost)
        docs = []
        for hit in hits:
            text = f"{hit.title}\n{hit.snippet}".strip()
            doc = SourceDocument(
                url=hit.url[:1000],
                domain=domain_of(hit.url)[:255],
                title=hit.title[:500],
                kind="search_result",
                query=query,
                text=text,
                content_hash=hashlib.sha256(text.encode()).hexdigest(),
                retrieved_at=self.clock(),
            )
            self.session.add(doc)
            docs.append(doc)
        self.session.flush()
        return docs

    # ------------------------------------------------------------ places
    @property
    def has_places(self) -> bool:
        if self.provider is None or not hasattr(self.provider, "places"):
            return False
        return getattr(self.provider, "places_results", True) is not None

    def places(self, query: str, *, country: str | None = None, count: int = 10) -> list[SourceDocument]:
        """Business listings, stored like search results. One budgeted call per query."""
        if not self.has_places:
            return []
        query = ("places: " + query.strip())[:480]
        since = self.clock() - timedelta(days=SEARCH_CACHE_DAYS)
        cached = list(self.session.scalars(
            select(SourceDocument).where(SourceDocument.kind == "place", SourceDocument.query == query,
                                         SourceDocument.retrieved_at >= since)
        ))
        if cached:
            return cached
        cost = float(self.settings.search_cost_per_query_usd)
        reservation = self.budget.reserve("research_data", cost, f"places:{self.provider.name}")
        try:
            hits = self.provider.places(query[len("places: "):], country=country, count=count)
        except Exception:
            self.budget.release(reservation)
            raise
        self.budget.commit(reservation, cost)
        docs = []
        for hit in hits:
            text = "\n".join(x for x in (hit.name, hit.address, hit.category, hit.website, hit.phone) if x)
            doc = SourceDocument(
                url=(hit.website or f"place:{hit.name}")[:1000],
                domain=domain_of(hit.website)[:255] if hit.website else "",
                title=hit.name[:500], kind="place", query=query, text=text,
                content_hash=hashlib.sha256(text.encode()).hexdigest(), retrieved_at=self.clock(),
            )
            self.session.add(doc)
            docs.append(doc)
        self.session.flush()
        return docs

    # ------------------------------------------------------------ fetch
    def fetch(self, url: str) -> SourceDocument | None:
        """A stored page, fetched if needed. None when the page is unavailable."""
        if self.fetcher is None:
            return None
        since = self.clock() - timedelta(days=PAGE_CACHE_DAYS)
        cached = self.session.scalar(
            select(SourceDocument).where(
                SourceDocument.kind == "page", SourceDocument.url == url[:1000],
                SourceDocument.retrieved_at >= since,
            )
        )
        if cached is not None:
            return cached
        try:
            page: FetchedPage = self.fetcher.fetch(url)
        except FetchRefused:
            return None
        text = page.text[:MAX_STORED_TEXT]
        if page.emails:
            # Keep addresses found in mailto: links searchable alongside the text.
            text += "\n" + " ".join(page.emails)
        doc = SourceDocument(
            url=url[:1000],
            domain=domain_of(page.final_url or url)[:255],
            title=page.title[:500],
            kind="page",
            text=text,
            content_hash=hashlib.sha256(text.encode()).hexdigest(),
            http_status=page.status,
            retrieved_at=self.clock(),
        )
        self.session.add(doc)
        self.session.flush()
        return doc

    def fetch_site(self, domain: str, paths: tuple[str, ...], limit: int) -> list[SourceDocument]:
        docs: list[SourceDocument] = []
        for path in paths:
            if len(docs) >= limit:
                break
            doc = self.fetch(f"https://{domain}{path}")
            if doc is not None and doc.content_hash not in {d.content_hash for d in docs}:
                docs.append(doc)
        return docs


def payload(docs: list[SourceDocument], excerpt_chars: int = EXCERPT_CHARS) -> list[dict[str, Any]]:
    """What a model is shown: numbered sources with bounded excerpts."""
    return [
        {"source_id": doc.id, "url": doc.url, "title": doc.title, "text": doc.text[:excerpt_chars]}
        for doc in docs
    ]


def build_research(session: Session, settings: Settings, budget: BudgetController, clock: Any = utcnow) -> ResearchService | None:
    """Live research in production mode only; simulation keeps its simulated world."""
    if settings.nexus_mode != "production":
        return None
    provider = build_search_provider(settings)
    if provider is None:
        return None
    return ResearchService(session, settings, budget, provider, PageFetcher(settings), clock)
