"""Polite, bounded web page fetcher.

- http(s) only; hosts that resolve to private, loopback or link-local
  addresses are refused (no reaching into the server's own network)
- robots.txt is honoured for our user agent
- responses are size-capped and only HTML/plain text is read
- one request per domain every few seconds
"""

from __future__ import annotations

import ipaddress
import re
import socket
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from app.core.config import Settings

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
SKIP_TAGS = {"script", "style", "noscript", "svg", "template"}
BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "td", "footer", "header"}


class FetchRefused(Exception):
    """The page may not or cannot be fetched (policy, robots, type, network)."""


@dataclass
class FetchedPage:
    url: str
    final_url: str
    status: int
    title: str
    text: str
    emails: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self.links: list[str] = []
        self.mailtos: list[str] = []
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if href.lower().startswith("mailto:"):
                self.mailtos.append(href[7:].split("?")[0])
            elif href:
                self.links.append(href)

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.title += data
            return
        self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str, list[str], list[str]]:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    text = "".join(parser.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return parser.title.strip(), text, parser.links, parser.mailtos


def extract_emails(text: str, extra: list[str] | None = None) -> list[str]:
    found = {m.group(0).strip(".").lower() for m in EMAIL_RE.finditer(text)}
    found.update(e.strip().lower() for e in (extra or []) if EMAIL_RE.fullmatch(e.strip()))
    # Image filenames like logo@2x.png look like addresses; drop them.
    return sorted(e for e in found if not re.search(r"\.(png|jpe?g|gif|svg|webp)$", e))


def is_public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private or address.is_loopback or address.is_link_local
            or address.is_reserved or address.is_multicast or address.is_unspecified
        ):
            return False
    return True


class Fetcher(Protocol):
    def fetch(self, url: str) -> FetchedPage: ...


class PageFetcher:
    def __init__(self, settings: Settings, min_interval_seconds: float = 3.0) -> None:
        self.user_agent = settings.fetch_user_agent
        self.max_bytes = settings.fetch_max_bytes
        self.timeout = settings.fetch_timeout_seconds
        self.min_interval = min_interval_seconds
        self._robots: dict[str, RobotFileParser | None] = {}
        self._last_hit: dict[str, float] = {}

    def _client(self) -> httpx.Client:
        return httpx.Client(
            follow_redirects=True, timeout=self.timeout,
            headers={"User-Agent": self.user_agent, "Accept": "text/html,text/plain;q=0.9"},
        )

    def _allowed_by_robots(self, client: httpx.Client, parsed) -> bool:
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots:
            parser: RobotFileParser | None = RobotFileParser()
            try:
                response = client.get(origin + "/robots.txt")
                if response.status_code == 200:
                    parser.parse(response.text.splitlines())
                elif response.status_code in (401, 403):
                    parser.disallow_all = True
                else:
                    parser = None  # no robots.txt: allowed
            except httpx.HTTPError:
                parser = None
            self._robots[origin] = parser
        parser = self._robots[origin]
        return True if parser is None else parser.can_fetch(self.user_agent, parsed.geturl())

    def _wait_turn(self, host: str) -> None:
        last = self._last_hit.get(host)
        if last is not None:
            delay = self.min_interval - (time.monotonic() - last)
            if delay > 0:
                time.sleep(delay)
        self._last_hit[host] = time.monotonic()

    def fetch(self, url: str) -> FetchedPage:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise FetchRefused(f"unsupported URL: {url}")
        if not is_public_host(parsed.hostname):
            raise FetchRefused(f"host is not a public address: {parsed.hostname}")
        with self._client() as client:
            if not self._allowed_by_robots(client, parsed):
                raise FetchRefused(f"robots.txt disallows {url}")
            self._wait_turn(parsed.hostname)
            try:
                with client.stream("GET", url) as response:
                    final = urlparse(str(response.url))
                    if final.hostname and not is_public_host(final.hostname):
                        raise FetchRefused(f"redirected to a non-public host: {final.hostname}")
                    content_type = response.headers.get("content-type", "").lower()
                    if response.status_code >= 400:
                        raise FetchRefused(f"HTTP {response.status_code} for {url}")
                    if "html" not in content_type and "text/plain" not in content_type:
                        raise FetchRefused(f"not a text page ({content_type or 'unknown type'})")
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        chunks.append(chunk)
                        size += len(chunk)
                        if size >= self.max_bytes:
                            break
                    raw = b"".join(chunks)[: self.max_bytes]
                    encoding = response.encoding or "utf-8"
                    status = response.status_code
                    final_url = str(response.url)
            except httpx.HTTPError as exc:
                raise FetchRefused(f"network error fetching {url}: {exc}") from exc
        body = raw.decode(encoding, errors="replace")
        if "html" in content_type:
            title, text, links, mailtos = html_to_text(body)
        else:
            title, text, links, mailtos = "", body, [], []
        absolute = [urljoin(final_url, link) for link in links]
        return FetchedPage(
            url=url, final_url=final_url, status=status, title=title[:500], text=text,
            emails=extract_emails(text, mailtos), links=absolute[:300],
        )


class StaticFetcher:
    """Deterministic fetcher for tests: url -> (title, text)."""

    def __init__(self, pages: dict[str, tuple[str, str]] | None = None) -> None:
        self.pages = pages or {}
        self.requested: list[str] = []

    def fetch(self, url: str) -> FetchedPage:
        self.requested.append(url)
        key = url.rstrip("/")
        for candidate, (title, text) in self.pages.items():
            if candidate.rstrip("/") == key:
                return FetchedPage(url=url, final_url=url, status=200, title=title, text=text,
                                   emails=extract_emails(text))
        raise FetchRefused(f"HTTP 404 for {url}")
