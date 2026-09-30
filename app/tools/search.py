"""Web search providers behind one small interface.

Brave, Tavily and Serper are supported; which one is used is configuration.
Providers only return what the search engine returned — title, URL, snippet —
and never interpret it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx

from app.core.config import Settings
from app.core.errors import ProviderError, ProviderUnavailable

# ISO 3166-1 alpha-2 codes for markets NEXUS works in (for search localisation).
COUNTRY_CODES = {
    "Kenya": "KE", "Uganda": "UG", "Tanzania": "TZ", "Rwanda": "RW", "Burundi": "BI",
    "South Sudan": "SS", "Somalia": "SO", "Democratic Republic of the Congo": "CD", "Ethiopia": "ET",
    "Djibouti": "DJ", "Eritrea": "ER", "Sudan": "SD", "Egypt": "EG", "Libya": "LY", "Tunisia": "TN",
    "Zambia": "ZM", "Zimbabwe": "ZW", "Malawi": "MW", "Madagascar": "MG", "Mauritius": "MU",
    "Seychelles": "SC", "Comoros": "KM", "Eswatini": "SZ", "Nigeria": "NG", "Ghana": "GH",
    "South Africa": "ZA", "Morocco": "MA", "Senegal": "SN", "Cote d'Ivoire": "CI", "Cameroon": "CM",
    "Mozambique": "MZ", "Botswana": "BW", "Namibia": "NA", "Angola": "AO", "Romania": "RO",
    "Bulgaria": "BG", "Serbia": "RS", "Moldova": "MD", "North Macedonia": "MK", "Albania": "AL",
    "Bosnia and Herzegovina": "BA", "Montenegro": "ME", "Ukraine": "UA", "Georgia": "GE",
}


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str
    rank: int


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, *, country: str | None = None, count: int = 8) -> list[SearchHit]: ...


def _check(response: httpx.Response, provider: str) -> dict:
    if response.status_code == 429 or response.status_code >= 500:
        raise ProviderUnavailable(f"{provider} search unavailable", status=response.status_code)
    if response.status_code >= 400:
        raise ProviderError(f"{provider} search rejected the request (check SEARCH_API_KEY)", status=response.status_code)
    return response.json()


class BraveSearch:
    name = "brave"
    URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str, timeout: float = 20.0) -> None:
        self._key = api_key
        self.timeout = timeout

    def search(self, query: str, *, country: str | None = None, count: int = 8) -> list[SearchHit]:
        params: dict = {"q": query, "count": min(count, 20)}
        code = COUNTRY_CODES.get(country or "")
        if code:
            params["country"] = code.lower()
        try:
            response = httpx.get(
                self.URL, params=params, timeout=self.timeout,
                headers={"X-Subscription-Token": self._key, "Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("brave search request failed", error=str(exc)) from exc
        data = _check(response, self.name)
        results = (data.get("web") or {}).get("results") or []
        return [
            SearchHit(title=r.get("title", ""), url=r.get("url", ""), snippet=r.get("description", ""), rank=i)
            for i, r in enumerate(results, start=1)
            if r.get("url")
        ]


class TavilySearch:
    name = "tavily"
    URL = "https://api.tavily.com/search"

    def __init__(self, api_key: str, timeout: float = 30.0) -> None:
        self._key = api_key
        self.timeout = timeout

    def search(self, query: str, *, country: str | None = None, count: int = 8) -> list[SearchHit]:
        payload = {"query": query, "max_results": min(count, 20), "search_depth": "basic"}
        try:
            response = httpx.post(
                self.URL, json=payload, timeout=self.timeout,
                headers={"Authorization": f"Bearer {self._key}"},
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("tavily search request failed", error=str(exc)) from exc
        data = _check(response, self.name)
        return [
            SearchHit(title=r.get("title", ""), url=r.get("url", ""), snippet=r.get("content", ""), rank=i)
            for i, r in enumerate(data.get("results") or [], start=1)
            if r.get("url")
        ]


class SerperSearch:
    name = "serper"
    URL = "https://google.serper.dev/search"

    def __init__(self, api_key: str, timeout: float = 20.0) -> None:
        self._key = api_key
        self.timeout = timeout

    def search(self, query: str, *, country: str | None = None, count: int = 8) -> list[SearchHit]:
        payload: dict = {"q": query, "num": min(count, 20)}
        code = COUNTRY_CODES.get(country or "")
        if code:
            payload["gl"] = code.lower()
        try:
            response = httpx.post(
                self.URL, json=payload, timeout=self.timeout,
                headers={"X-API-KEY": self._key, "Content-Type": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("serper search request failed", error=str(exc)) from exc
        data = _check(response, self.name)
        return [
            SearchHit(title=r.get("title", ""), url=r.get("link", ""), snippet=r.get("snippet", ""), rank=i)
            for i, r in enumerate(data.get("organic") or [], start=1)
            if r.get("link")
        ]


class StaticSearch:
    """Deterministic provider for tests: maps query substrings to hits."""

    name = "static"

    def __init__(self, results: dict[str, list[SearchHit]] | None = None) -> None:
        self.results = results or {}
        self.queries: list[str] = []

    def search(self, query: str, *, country: str | None = None, count: int = 8) -> list[SearchHit]:
        self.queries.append(query)
        hits: list[SearchHit] = []
        for needle, found in self.results.items():
            if needle.lower() in query.lower():
                hits.extend(found)
        return hits[:count]


def build_search_provider(settings: Settings) -> SearchProvider | None:
    if settings.search_provider == "none" or not settings.search_api_key:
        return None
    if settings.search_provider == "brave":
        return BraveSearch(settings.search_api_key)
    if settings.search_provider == "tavily":
        return TavilySearch(settings.search_api_key)
    if settings.search_provider == "serper":
        return SerperSearch(settings.search_api_key)
    return None
