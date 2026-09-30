"""Ad platforms behind one interface: Google Ads (REST), Meta Marketing API, and a simulator.

Everything is created PAUSED and only enabled once every object exists, so a
half-built campaign never spends. Budgets are converted from USD into the ad
account's currency with the operator's rates; spend comes back the same way.

Only the platform adapters know vendor field names. Callers see plain dicts.
"""

from __future__ import annotations

import base64
import hashlib
import json
import random
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Protocol

import httpx

from app.core.config import Settings
from app.core.ids import stable_key
from app.tools.search import COUNTRY_CODES

# ISO 3166-1 numeric codes: Google geo target constants for countries are 2000 + this.
ISO_NUMERIC = {
    "Kenya": 404, "Uganda": 800, "Tanzania": 834, "Rwanda": 646, "Burundi": 108, "Ethiopia": 231,
    "Somalia": 706, "South Sudan": 728, "Democratic Republic of the Congo": 180, "Zambia": 894,
    "Zimbabwe": 716, "Malawi": 454, "Mozambique": 508, "Nigeria": 566, "Ghana": 288, "Egypt": 818,
    "South Africa": 710, "Botswana": 72, "Namibia": 516, "Madagascar": 450, "Mauritius": 480,
    "Djibouti": 262, "Sudan": 729, "Eswatini": 748, "Morocco": 504, "Tunisia": 788, "Senegal": 686,
    "Cameroon": 120, "Romania": 642, "Bulgaria": 100, "Serbia": 688, "Moldova": 498,
    "North Macedonia": 807, "Albania": 8, "Bosnia and Herzegovina": 70, "Montenegro": 499,
    "Georgia": 268, "Ukraine": 804,
}
EU_COUNTRIES = {"Romania", "Bulgaria", "Greece", "Croatia", "Poland", "Hungary", "Slovakia", "Czechia",
                "Slovenia", "Austria", "Germany", "France", "Italy", "Spain", "Portugal", "Netherlands",
                "Belgium", "Ireland", "Sweden", "Finland", "Denmark", "Estonia", "Latvia", "Lithuania",
                "Luxembourg", "Malta", "Cyprus"}


class AdPlatformError(Exception):
    """The platform refused or failed; message is safe to show the operator."""


@dataclass
class Launch:
    """Everything needed to build a campaign on a platform."""

    campaign_id: str
    name: str
    countries: list[str]
    language: str
    daily_budget_native: float
    landing_url: str
    url_suffix: str
    keywords: list[str] = field(default_factory=list)
    negative_keywords: list[str] = field(default_factory=list)
    variants: list[dict[str, Any]] = field(default_factory=list)  # {key, content, image_bytes?}
    business_name: str = ""
    use_conversions: bool = False


class AdPlatform(Protocol):
    name: str
    currency: str

    def create(self, launch: Launch) -> dict[str, Any]: ...
    def set_status(self, external: dict[str, Any], active: bool) -> None: ...
    def set_budget(self, external: dict[str, Any], daily_native: float) -> None: ...
    def set_variant_status(self, external: dict[str, Any], variant_external_id: str, active: bool) -> None: ...
    def add_variant(self, external: dict[str, Any], launch: Launch, variant: dict[str, Any]) -> str: ...
    def metrics(self, external: dict[str, Any], since: date, until: date) -> list[dict[str, Any]]: ...
    def search_terms(self, external: dict[str, Any], since: date, until: date) -> list[dict[str, Any]]: ...
    def add_negative_keywords(self, external: dict[str, Any], terms: list[str]) -> None: ...
    def upload_conversion(self, event: str, attribution: dict[str, Any], email: str, phone: str | None,
                          value_usd: float | None, when: datetime, event_id: str, page_url: str | None) -> bool: ...


def _sha256(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()


# ============================================================ Google Ads


class GoogleAdsPlatform:
    name = "google"

    def __init__(self, settings: Settings, timeout: float = 60.0) -> None:
        if not settings.google_ads_configured:
            raise AdPlatformError("Google Ads credentials are incomplete")
        self.s = settings
        self.currency = settings.google_ads_currency.upper()
        self.customer = settings.google_ads_customer_id.replace("-", "")
        self.base = f"https://googleads.googleapis.com/{settings.google_ads_api_version}"
        self.timeout = timeout
        self._token: tuple[str, float] | None = None

    # ------------------------------------------------------------ transport
    def _access_token(self) -> str:
        if self._token and self._token[1] > time.time() + 60:
            return self._token[0]
        try:
            response = httpx.post("https://oauth2.googleapis.com/token", timeout=self.timeout, data={
                "grant_type": "refresh_token", "client_id": self.s.google_ads_client_id,
                "client_secret": self.s.google_ads_client_secret, "refresh_token": self.s.google_ads_refresh_token,
            })
        except httpx.HTTPError as exc:
            raise AdPlatformError(f"Google sign-in failed: {exc}") from exc
        if response.status_code >= 400:
            raise AdPlatformError(f"Google sign-in refused ({response.status_code}): check the OAuth client and refresh token")
        data = response.json()
        self._token = (data["access_token"], time.time() + float(data.get("expires_in", 3000)))
        return self._token[0]

    def _headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._access_token()}", "developer-token": self.s.google_ads_developer_token,
                   "Content-Type": "application/json"}
        if self.s.google_ads_login_customer_id:
            headers["login-customer-id"] = self.s.google_ads_login_customer_id.replace("-", "")
        return headers

    def _call(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = httpx.post(f"{self.base}/customers/{self.customer}/{path}", json=body,
                                  headers=self._headers(), timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise AdPlatformError(f"Google Ads request failed: {exc}") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("error", {}).get("message", "")
                details = json.dumps(response.json().get("error", {}).get("details", []))[:800]
            except ValueError:
                detail, details = response.text[:300], ""
            raise AdPlatformError(f"Google Ads refused {path} ({response.status_code}): {detail} {details}")
        return response.json() if response.content else {}

    def _mutate(self, resource: str, operations: list[dict[str, Any]]) -> list[str]:
        data = self._call(f"{resource}:mutate", {"operations": operations})
        return [r.get("resourceName", "") for r in data.get("results", [])]

    def _search(self, query: str) -> list[dict[str, Any]]:
        rows, token = [], None
        while True:
            body: dict[str, Any] = {"query": query}
            if token:
                body["pageToken"] = token
            data = self._call("googleAds:search", body)
            rows += data.get("results", [])
            token = data.get("nextPageToken")
            if not token:
                return rows

    # ------------------------------------------------------------ build
    @staticmethod
    def _rsa(content: dict[str, Any], landing_url: str) -> dict[str, Any]:
        ad: dict[str, Any] = {
            "finalUrls": [landing_url],
            "responsiveSearchAd": {
                "headlines": [{"text": h} for h in content["headlines"]],
                "descriptions": [{"text": d} for d in content["descriptions"]],
            },
        }
        for i, path in enumerate(content.get("paths") or [], start=1):
            if i <= 2 and path:
                ad["responsiveSearchAd"][f"path{i}"] = path[:15]
        return ad

    def create(self, launch: Launch) -> dict[str, Any]:
        c = f"customers/{self.customer}"
        budget = self._mutate("campaignBudgets", [{"create": {
            "name": f"{launch.name} budget {launch.campaign_id}", "amountMicros": str(int(launch.daily_budget_native * 1_000_000)),
            "deliveryMethod": "STANDARD", "explicitlyShared": False,
        }}])[0]
        bidding = {"maximizeConversions": {}} if launch.use_conversions else {"targetSpend": {}}
        campaign = self._mutate("campaigns", [{"create": {
            "name": f"{launch.name} [{launch.campaign_id}]", "status": "PAUSED", "advertisingChannelType": "SEARCH",
            "campaignBudget": budget, "finalUrlSuffix": launch.url_suffix,
            "networkSettings": {"targetGoogleSearch": True, "targetSearchNetwork": False,
                                "targetContentNetwork": False, "targetPartnerSearchNetwork": False},
            "geoTargetTypeSetting": {"positiveGeoTargetType": "PRESENCE"},
            "containsEuPoliticalAdvertising": "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING",
            **bidding,
        }}])[0]
        criteria = [{"create": {"campaign": campaign, "location": {"geoTargetConstant": f"geoTargetConstants/{2000 + ISO_NUMERIC[country]}"}}}
                    for country in launch.countries if country in ISO_NUMERIC]
        criteria.append({"create": {"campaign": campaign, "language": {"languageConstant": "languageConstants/1000"}}})
        criteria += [{"create": {"campaign": campaign, "negative": True, "keyword": {"text": t, "matchType": "PHRASE"}}}
                     for t in launch.negative_keywords]
        self._mutate("campaignCriteria", criteria)
        ad_group = self._mutate("adGroups", [{"create": {
            "name": f"{launch.name} - core", "campaign": campaign, "status": "ENABLED", "type": "SEARCH_STANDARD",
        }}])[0]
        keywords = [{"create": {"adGroup": ad_group, "status": "ENABLED", "keyword": {"text": k, "matchType": "PHRASE"}}}
                    for k in launch.keywords]
        if keywords:
            self._mutate("adGroupCriteria", keywords)
        variant_ids = {}
        for variant in launch.variants:
            rn = self._mutate("adGroupAds", [{"create": {"adGroup": ad_group, "status": "ENABLED",
                                                          "ad": self._rsa(variant["content"], launch.landing_url)}}])[0]
            variant_ids[variant["key"]] = rn.split("~")[-1]
        self._mutate("campaigns", [{"update": {"resourceName": campaign, "status": "ENABLED"}, "updateMask": "status"}])
        return {"campaign": campaign, "campaign_id": campaign.split("/")[-1], "budget": budget, "ad_group": ad_group,
                "variants": variant_ids, "customer": c}

    def set_status(self, external: dict[str, Any], active: bool) -> None:
        self._mutate("campaigns", [{"update": {"resourceName": external["campaign"],
                                               "status": "ENABLED" if active else "PAUSED"}, "updateMask": "status"}])

    def set_budget(self, external: dict[str, Any], daily_native: float) -> None:
        self._mutate("campaignBudgets", [{"update": {"resourceName": external["budget"],
                                                     "amountMicros": str(int(daily_native * 1_000_000))},
                                          "updateMask": "amountMicros"}])

    def set_variant_status(self, external: dict[str, Any], variant_external_id: str, active: bool) -> None:
        ad_group_id = external["ad_group"].split("/")[-1]
        self._mutate("adGroupAds", [{"update": {
            "resourceName": f"customers/{self.customer}/adGroupAds/{ad_group_id}~{variant_external_id}",
            "status": "ENABLED" if active else "PAUSED"}, "updateMask": "status"}])

    def add_variant(self, external: dict[str, Any], launch: Launch, variant: dict[str, Any]) -> str:
        rn = self._mutate("adGroupAds", [{"create": {"adGroup": external["ad_group"], "status": "ENABLED",
                                                      "ad": self._rsa(variant["content"], launch.landing_url)}}])[0]
        return rn.split("~")[-1]

    def metrics(self, external: dict[str, Any], since: date, until: date) -> list[dict[str, Any]]:
        rows = self._search(
            "SELECT ad_group_ad.ad.id, segments.date, metrics.impressions, metrics.clicks, metrics.cost_micros "
            f"FROM ad_group_ad WHERE campaign.id = {external['campaign_id']} "
            f"AND segments.date BETWEEN '{since.isoformat()}' AND '{until.isoformat()}'"
        )
        out = []
        for row in rows:
            m = row.get("metrics", {})
            out.append({
                "day": date.fromisoformat(row["segments"]["date"]),
                "variant_external_id": str(row.get("adGroupAd", {}).get("ad", {}).get("id", "")),
                "impressions": int(m.get("impressions", 0) or 0), "clicks": int(m.get("clicks", 0) or 0),
                "spend_native": int(m.get("costMicros", 0) or 0) / 1_000_000,
            })
        return out

    def search_terms(self, external: dict[str, Any], since: date, until: date) -> list[dict[str, Any]]:
        rows = self._search(
            "SELECT search_term_view.search_term, metrics.clicks, metrics.impressions, metrics.cost_micros "
            f"FROM search_term_view WHERE campaign.id = {external['campaign_id']} "
            f"AND segments.date BETWEEN '{since.isoformat()}' AND '{until.isoformat()}'"
        )
        return [{"term": r.get("searchTermView", {}).get("searchTerm", ""),
                 "clicks": int(r.get("metrics", {}).get("clicks", 0) or 0),
                 "spend_native": int(r.get("metrics", {}).get("costMicros", 0) or 0) / 1_000_000} for r in rows]

    def add_negative_keywords(self, external: dict[str, Any], terms: list[str]) -> None:
        if terms:
            self._mutate("campaignCriteria", [{"create": {"campaign": external["campaign"], "negative": True,
                                                          "keyword": {"text": t, "matchType": "EXACT"}}} for t in terms])

    def upload_conversion(self, event, attribution, email, phone, value_usd, when, event_id, page_url) -> bool:
        gclid = attribution.get("gclid")
        action = self.s.google_ads_conversion_action_won if event == "won" else self.s.google_ads_conversion_action_lead
        if not gclid or not action:
            return False
        conversion: dict[str, Any] = {
            "gclid": gclid, "conversionAction": f"customers/{self.customer}/conversionActions/{action}",
            "conversionDateTime": when.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00:00"),
            "orderId": event_id,
        }
        if value_usd:
            conversion.update(conversionValue=float(value_usd), currencyCode="USD")
        self._upload(conversion)
        return True

    def _upload(self, conversion: dict[str, Any]) -> None:
        try:
            response = httpx.post(f"{self.base}/customers/{self.customer}:uploadClickConversions",
                                  json={"conversions": [conversion], "partialFailure": True},
                                  headers=self._headers(), timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise AdPlatformError(f"Google conversion upload failed: {exc}") from exc
        if response.status_code >= 400:
            raise AdPlatformError(f"Google refused the conversion ({response.status_code}): {response.text[:300]}")


# ============================================================ Meta


class MetaAdsPlatform:
    name = "meta"
    CTA_FALLBACK = "LEARN_MORE"

    def __init__(self, settings: Settings, timeout: float = 60.0) -> None:
        if not settings.meta_ads_configured:
            raise AdPlatformError("Meta credentials are incomplete")
        self.s = settings
        self.currency = settings.meta_ad_account_currency.upper()
        self.account = f"act_{settings.meta_ad_account_id.replace('act_', '')}"
        self.base = f"https://graph.facebook.com/{settings.meta_api_version}"
        self.timeout = timeout

    def _request(self, method: str, path: str, data: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in (data or {}).items()}
        query = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in (params or {}).items()}
        query["access_token"] = self.s.meta_access_token
        try:
            response = httpx.request(method, f"{self.base}/{path}", data=payload or None, params=query, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise AdPlatformError(f"Meta request failed: {exc}") from exc
        body = response.json() if response.content else {}
        if response.status_code >= 400 or "error" in body:
            error = body.get("error", {})
            raise AdPlatformError(f"Meta refused {path} ({response.status_code}): {error.get('message', '')} "
                                  f"{error.get('error_user_msg', '')}".strip())
        return body

    def _post(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", path, data=data)

    def _creative(self, launch: Launch, variant: dict[str, Any]) -> str:
        content = variant["content"]
        image_hash = None
        if variant.get("image_bytes"):
            uploaded = self._post(f"{self.account}/adimages", {"bytes": base64.b64encode(variant["image_bytes"]).decode()})
            image_hash = next(iter(uploaded.get("images", {}).values()), {}).get("hash")
        link_data: dict[str, Any] = {
            "link": launch.landing_url, "message": content["primary_text"], "name": content["headline"],
            "call_to_action": {"type": content.get("call_to_action") or self.CTA_FALLBACK, "value": {"link": launch.landing_url}},
        }
        if content.get("description"):
            link_data["description"] = content["description"]
        if image_hash:
            link_data["image_hash"] = image_hash
        creative = self._post(f"{self.account}/adcreatives", {
            "name": f"{launch.name} {variant['key']}", "url_tags": launch.url_suffix,
            "object_story_spec": {"page_id": self.s.meta_page_id, "link_data": link_data},
        })
        return creative["id"]

    def create(self, launch: Launch) -> dict[str, Any]:
        campaign = self._post(f"{self.account}/campaigns", {
            "name": f"{launch.name} [{launch.campaign_id}]", "objective": "OUTCOME_TRAFFIC", "status": "PAUSED",
            "special_ad_categories": [], "is_adset_budget_sharing_enabled": "false",
        })["id"]
        codes = [COUNTRY_CODES[c] for c in launch.countries if c in COUNTRY_CODES]
        adset_data: dict[str, Any] = {
            "name": f"{launch.name} - {', '.join(codes)}", "campaign_id": campaign,
            "daily_budget": int(round(launch.daily_budget_native * 100)), "billing_event": "IMPRESSIONS",
            "optimization_goal": "LINK_CLICKS", "bid_strategy": "LOWEST_COST_WITHOUT_CAP", "status": "PAUSED",
            "targeting": {"geo_locations": {"countries": codes}, "age_min": 25, "age_max": 65,
                          "targeting_automation": {"advantage_audience": 1}},
        }
        if any(c in EU_COUNTRIES for c in launch.countries):
            # EU Digital Services Act: who benefits from and who pays for the ad.
            adset_data["dsa_beneficiary"] = launch.business_name
            adset_data["dsa_payor"] = launch.business_name
        adset = self._post(f"{self.account}/adsets", adset_data)["id"]
        variant_ids = {}
        for variant in launch.variants:
            creative_id = self._creative(launch, variant)
            ad = self._post(f"{self.account}/ads", {"name": f"{launch.name} {variant['key']}", "adset_id": adset,
                                                     "creative": {"creative_id": creative_id}, "status": "PAUSED"})["id"]
            variant_ids[variant["key"]] = ad
        for ad_id in variant_ids.values():
            self._post(ad_id, {"status": "ACTIVE"})
        self._post(adset, {"status": "ACTIVE"})
        self._post(campaign, {"status": "ACTIVE"})
        return {"campaign": campaign, "campaign_id": campaign, "adset": adset, "variants": variant_ids}

    def set_status(self, external: dict[str, Any], active: bool) -> None:
        self._post(external["campaign"], {"status": "ACTIVE" if active else "PAUSED"})

    def set_budget(self, external: dict[str, Any], daily_native: float) -> None:
        self._post(external["adset"], {"daily_budget": int(round(daily_native * 100))})

    def set_variant_status(self, external: dict[str, Any], variant_external_id: str, active: bool) -> None:
        self._post(variant_external_id, {"status": "ACTIVE" if active else "PAUSED"})

    def add_variant(self, external: dict[str, Any], launch: Launch, variant: dict[str, Any]) -> str:
        creative_id = self._creative(launch, variant)
        return self._post(f"{self.account}/ads", {"name": f"{launch.name} {variant['key']}", "adset_id": external["adset"],
                                                   "creative": {"creative_id": creative_id}, "status": "ACTIVE"})["id"]

    def metrics(self, external: dict[str, Any], since: date, until: date) -> list[dict[str, Any]]:
        out, after = [], None
        while True:
            params: dict[str, Any] = {
                "level": "ad", "fields": "ad_id,spend,impressions,clicks", "time_increment": 1,
                "time_range": {"since": since.isoformat(), "until": until.isoformat()},
                "filtering": [{"field": "campaign.id", "operator": "EQUAL", "value": external["campaign_id"]}],
                "limit": 200,
            }
            if after:
                params["after"] = after
            data = self._request("GET", f"{self.account}/insights", params=params)
            for row in data.get("data", []):
                out.append({"day": date.fromisoformat(row["date_start"]), "variant_external_id": str(row.get("ad_id", "")),
                            "impressions": int(row.get("impressions", 0) or 0), "clicks": int(row.get("clicks", 0) or 0),
                            "spend_native": float(row.get("spend", 0) or 0)})
            after = data.get("paging", {}).get("cursors", {}).get("after") if data.get("paging", {}).get("next") else None
            if not after:
                return out

    def search_terms(self, external, since, until) -> list[dict[str, Any]]:
        return []  # Meta has no search terms

    def add_negative_keywords(self, external, terms) -> None:
        return None

    def upload_conversion(self, event, attribution, email, phone, value_usd, when, event_id, page_url) -> bool:
        if not self.s.meta_pixel_id:
            return False
        user_data: dict[str, Any] = {"em": [_sha256(email)]}
        if phone:
            digits = "".join(ch for ch in phone if ch.isdigit())
            if digits:
                user_data["ph"] = [_sha256(digits)]
        if attribution.get("fbc"):
            user_data["fbc"] = attribution["fbc"]
        payload: dict[str, Any] = {"event_name": "Purchase" if event == "won" else "Lead",
                                   "event_time": int(when.replace(tzinfo=when.tzinfo or timezone.utc).timestamp()),
                                   "action_source": "website", "event_id": event_id, "user_data": user_data}
        if page_url:
            payload["event_source_url"] = page_url
        if value_usd:
            payload["custom_data"] = {"value": float(value_usd), "currency": "USD"}
        self._post(f"{self.s.meta_pixel_id}/events", {"data": [payload]})
        return True


# ============================================================ simulator

# How the simulated world responds to ad angles (click-through and lead rates).
SIM_ANGLE_RATES = {
    "price_value": (0.030, 0.050), "lead_time": (0.022, 0.060), "quality_warranty": (0.018, 0.080),
    "local_support": (0.020, 0.045), "compliance_docs": (0.012, 0.090), "range": (0.025, 0.035),
}


class SimulatedAdsPlatform:
    """No accounts, no spend: deterministic synthetic delivery for tests and simulation."""

    name = "simulated"
    currency = "USD"

    def __init__(self, platform: str = "google", seed: int = 20260930) -> None:
        self.platform = platform
        self.seed = seed
        self.calls: list[tuple[str, Any]] = []
        self.state: dict[str, dict[str, Any]] = {}

    def create(self, launch: Launch) -> dict[str, Any]:
        self.calls.append(("create", launch.campaign_id))
        ids = {v["key"]: stable_key("simad", launch.campaign_id, v["key"])[:12] for v in launch.variants}
        self.state[launch.campaign_id] = {"active": True, "budget": launch.daily_budget_native,
                                          "variants": {i: True for i in ids.values()},
                                          "angles": {ids[v["key"]]: v.get("angle") for v in launch.variants}}
        return {"campaign": f"sim/{launch.campaign_id}", "campaign_id": launch.campaign_id, "variants": ids, "simulated": True}

    def _st(self, external):
        return self.state.setdefault(external["campaign_id"], {"active": True, "budget": 5.0, "variants": {}, "angles": {}})

    def set_status(self, external, active):
        self.calls.append(("status", external["campaign_id"], active))
        self._st(external)["active"] = active

    def set_budget(self, external, daily_native):
        self.calls.append(("budget", external["campaign_id"], daily_native))
        self._st(external)["budget"] = daily_native

    def set_variant_status(self, external, variant_external_id, active):
        self.calls.append(("variant", variant_external_id, active))
        self._st(external)["variants"][variant_external_id] = active

    def add_variant(self, external, launch, variant):
        vid = stable_key("simad", external["campaign_id"], variant["key"])[:12]
        st = self._st(external)
        st["variants"][vid] = True
        st["angles"][vid] = variant.get("angle")
        self.calls.append(("add_variant", vid))
        return vid

    def metrics(self, external, since, until):
        st = self._st(external)
        if not st["active"]:
            return []
        live = [v for v, on in st["variants"].items() if on]
        out, day = [], since
        while day <= until:
            for vid in live:
                rng = random.Random(f"{self.seed}{vid}{day.isoformat()}")
                ctr, _ = SIM_ANGLE_RATES.get(st["angles"].get(vid) or "range", (0.02, 0.04))
                spend = round(st["budget"] / max(len(live), 1) * rng.uniform(0.85, 1.0), 2)
                impressions = int(spend * rng.uniform(250, 400))
                clicks = int(impressions * ctr * rng.uniform(0.7, 1.3))
                out.append({"day": day, "variant_external_id": vid, "impressions": impressions,
                            "clicks": clicks, "spend_native": spend})
            day = date.fromordinal(day.toordinal() + 1)
        return out

    def simulated_leads(self, external, variant_external_id, clicks, day) -> int:
        rate = SIM_ANGLE_RATES.get(self._st(external)["angles"].get(variant_external_id) or "range", (0.02, 0.04))[1]
        rng = random.Random(f"{self.seed}lead{variant_external_id}{day.isoformat()}")
        return sum(1 for _ in range(clicks) if rng.random() < rate)

    def search_terms(self, external, since, until):
        return [{"term": "free laptop download", "clicks": 25, "spend_native": 6.0},
                {"term": "refurbished laptops wholesale", "clicks": 40, "spend_native": 12.0}]

    def add_negative_keywords(self, external, terms):
        self.calls.append(("negatives", tuple(terms)))

    def upload_conversion(self, event, attribution, email, phone, value_usd, when, event_id, page_url) -> bool:
        self.calls.append(("conversion", event, event_id))
        return True


_SIMULATED: dict[str, SimulatedAdsPlatform] = {}


def build_platform(settings: Settings, platform: str) -> AdPlatform | None:
    """The live platform when its credentials exist; the simulator outside production."""
    if settings.nexus_mode != "production":
        return _SIMULATED.setdefault(platform, SimulatedAdsPlatform(platform))
    if platform == "google" and settings.google_ads_configured:
        return GoogleAdsPlatform(settings)
    if platform == "meta" and settings.meta_ads_configured:
        return MetaAdsPlatform(settings)
    return None


def platform_for(ctx: Any, platform: str) -> AdPlatform | None:
    overrides = getattr(ctx, "ads_platforms", None) or {}
    if platform in overrides:
        return overrides[platform]
    return build_platform(ctx.settings, platform)
