"""Southern and Central Africa: markets, licence blocs, ad targeting, French and Portuguese."""

from __future__ import annotations

from datetime import date

import pytest

from app.ads import copy as adcopy
from app.ads.compliance import check_keywords, claim_issues
from app.ads.platforms import (
    ISO_NUMERIC,
    AdPlatformError,
    GoogleAdsPlatform,
    Launch,
    MetaAdsPlatform,
    require_targeting,
)
from app.commercial import settings as commercial
from app.core import languages
from app.core.types import ProductCategory
from app.policies.fact_check import validate_message
from app.policies.licenses import REGIONS, add_license, canonical_country, coverage
from app.tools.search import COUNTRY_CODES

MEDICAL = ProductCategory.MEDICAL.value
SOUTHERN = ["Zambia", "South Africa", "Zimbabwe", "Malawi", "Mozambique", "Botswana", "Namibia", "Angola"]
CENTRAL = ["Democratic Republic of the Congo", "Cameroon", "Republic of the Congo", "Gabon"]


# ------------------------------------------------------------------ default markets
@pytest.mark.parametrize("category", [c.value for c in ProductCategory])
def test_every_line_targets_southern_and_central_africa_by_default(category):
    markets = commercial.DEFAULTS["target_markets"][category]
    assert set(SOUTHERN + CENTRAL) <= set(markets)
    assert len(markets) == len(set(markets))  # no duplicates


def test_every_default_market_can_be_searched_and_advertised():
    for category, markets in commercial.DEFAULTS["target_markets"].items():
        for country in markets:
            assert country in COUNTRY_CODES, (category, country)
            if category != ProductCategory.PHARMA.value:  # pharmaceuticals are never advertised
                assert country in ISO_NUMERIC, (category, country)


def test_google_geo_codes_are_the_iso_numeric_codes():
    expected = {"South Africa": 710, "Angola": 24, "Democratic Republic of the Congo": 180, "Cameroon": 120,
                "Republic of the Congo": 178, "Gabon": 266, "Zambia": 894, "Mozambique": 508}
    for country, code in expected.items():
        assert ISO_NUMERIC[country] == code
    assert COUNTRY_CODES["Republic of the Congo"] == "CG" and COUNTRY_CODES["Gabon"] == "GA"


def test_a_mistyped_market_is_rejected_when_saved(session):
    with pytest.raises(commercial.CommercialSettingsError, match="unknown countries"):
        commercial.update(session, {"target_markets": {"refurbished_laptop": ["Keyna"]}})
    commercial.update(session, {"target_markets": {"refurbished_laptop": ["Lesotho", "DRC", "Congo-Brazzaville"]}})
    assert commercial.get(session)["target_markets"]["refurbished_laptop"] == [
        "Lesotho", "Democratic Republic of the Congo", "Republic of the Congo"]


# ------------------------------------------------------------------ licence blocs
def test_regional_blocs_match_their_published_membership():
    assert len(REGIONS["SADC"]) == 16 and len(REGIONS["ECCAS"]) == 11 and len(REGIONS["CEMAC"]) == 6
    assert set(REGIONS["CEMAC"]) <= set(REGIONS["ECCAS"])
    assert {"South Africa", "Zambia", "Angola", "Mozambique", "Democratic Republic of the Congo"} <= set(REGIONS["SADC"])
    assert {"Cameroon", "Gabon", "Chad", "Rwanda", "Burundi"} <= set(REGIONS["ECCAS"])


@pytest.mark.parametrize("name,canonical", [
    ("DRC", "Democratic Republic of the Congo"), ("Congo-Brazzaville", "Republic of the Congo"),
    ("Republic of Congo", "Republic of the Congo"), ("RSA", "South Africa"), ("CAR", "Central African Republic"),
    ("São Tomé and Príncipe", "Sao Tome and Principe"), ("gabon", "Gabon"),
])
def test_country_names_are_normalised(name, canonical):
    assert canonical_country(name) == canonical


def test_a_licence_scoped_to_sadc_covers_southern_africa_only(session):
    add_license(
        session, holder_name="Leonard Medical Supplies Ltd", country="Zambia",
        issuing_authority="Zambia Medicines Regulatory Authority", license_number="ZMRA-T-1",
        license_types=["importer", "distributor"], product_categories=[MEDICAL],
        valid_from="2026-01-01", expires_on="2027-06-30", regions=["SADC"],
    )
    today = date(2026, 11, 23)
    assert coverage(session, "Zambia", MEDICAL, today).covered and not coverage(session, "Zambia", MEDICAL, today).cross_border
    south_africa = coverage(session, "South Africa", MEDICAL, today)
    assert south_africa.covered and south_africa.cross_border  # allowed to approach; a licence is never an import permit
    assert not coverage(session, "Cameroon", MEDICAL, today).covered
    assert not coverage(session, "Kenya", MEDICAL, today).covered


def test_a_licence_scoped_to_cemac_covers_francophone_central_africa(session):
    add_license(
        session, holder_name="Leonard Medical Supplies Ltd", country="Cameroon",
        issuing_authority="Ministère de la Santé publique", license_number="CM-T-1",
        license_types=["importer"], product_categories=[MEDICAL], expires_on="2027-06-30", regions=["CEMAC"],
    )
    today = date(2026, 11, 23)
    for country in ("Gabon", "Chad", "Republic of the Congo", "Central African Republic", "Equatorial Guinea"):
        assert coverage(session, country, MEDICAL, today).covered, country
    assert not coverage(session, "Angola", MEDICAL, today).covered  # Angola is ECCAS, not CEMAC


# ------------------------------------------------------------------ ad targeting safety
def test_campaigns_are_refused_when_a_country_has_no_location_code():
    require_targeting(["Kenya", "Gabon"], ISO_NUMERIC, "x")  # known: fine
    with pytest.raises(AdPlatformError, match="Atlantis"):
        require_targeting(["Kenya", "Atlantis"], ISO_NUMERIC, "location")
    with pytest.raises(AdPlatformError):
        require_targeting([], ISO_NUMERIC, "location")  # no country at all would mean worldwide


def _launch(countries):
    return Launch(campaign_id="c1", name="t", countries=countries, language="en", daily_budget_native=5.0,
                  landing_url="https://x.test/p/a", url_suffix="")


def test_google_and_meta_refuse_before_creating_anything(monkeypatch):
    import app.ads.platforms as platforms

    def no_network(*a, **k):  # any call would mean a half-built campaign
        raise AssertionError("must not call the platform")

    monkeypatch.setattr(platforms.httpx, "post", no_network)
    monkeypatch.setattr(platforms.httpx, "request", no_network)
    for cls in (GoogleAdsPlatform, MetaAdsPlatform):
        platform = object.__new__(cls)
        with pytest.raises(AdPlatformError, match="Atlantis"):
            platform.create(_launch(["Atlantis"]))


# ------------------------------------------------------------------ French and Portuguese
def test_central_and_lusophone_markets_get_their_own_language(session):
    for country in ("Democratic Republic of the Congo", "Republic of the Congo", "Gabon", "Cameroon", "Chad"):
        assert languages.language_for(session, country) == "fr", country
    assert languages.language_for(session, "DRC") == "fr"
    for country in ("Angola", "Mozambique"):
        assert languages.language_for(session, country) == "pt", country
    for country in ("Zambia", "South Africa", "Zimbabwe", "Kenya", "Rwanda"):
        assert languages.language_for(session, country) == "en", country
    commercial.update(session, {"languages": {"Cameroon": "en"}})  # Cameroon is bilingual
    assert languages.language_for(session, "Cameroon") == "en"


def test_french_and_portuguese_are_complete():
    english = set(languages.STRINGS["en"])
    for code in ("fr", "pt"):
        assert english <= set(languages.STRINGS[code]), (code, english - set(languages.STRINGS[code]))
        assert set(languages.CATEGORY_NAMES[code]) == {c.value for c in ProductCategory}
        assert languages.LANGUAGES[code]["google_language"] and not languages.is_rtl(code)
        for key in ("medical", "regulatory", "relationship", "license", "ad", "new"):
            assert languages.CLAIMS[code][key], (code, key)
        # every template placeholder survives so pages and e-mails render
        for key in ("ack", "price", "step2_d", "thanks_body", "wa_text", "serving", "optout_link"):
            assert set(_placeholders(languages.STRINGS["en"][key])) == set(_placeholders(languages.STRINGS[code][key])), (code, key)
        for cat, queries in languages.PROSPECT_QUERIES[code].items():
            assert all("{country}" in q for q in queries), (code, cat)


def _placeholders(text):
    import re

    return re.findall(r"\{(\w+)\}", text)


def test_french_and_portuguese_text_renders():
    assert "devis" in languages.t("fr", "request_quote").lower() or "devis" in languages.t("fr", "card_cta").lower()
    assert languages.t("pt", "ack", name="Ana", topic="x", qty="", promise=languages.t("pt", "promise"), sender="S")
    assert languages.country_name("fr", "Cameroon") == "Cameroun"
    assert languages.country_name("pt", "Mozambique") == "Moçambique"
    assert languages.category_name("fr", "server_it").startswith("serveurs")


def test_unverifiable_claims_are_caught_in_french_and_portuguese():
    fr = {"numbers": [], "language": "fr"}
    pt = {"numbers": [], "language": "pt"}
    assert any("relationship_claim" in u for u in validate_message("", "Nous sommes distributeur agréé dans la région", fr).unsupported)
    assert any("relationship_claim" in u for u in validate_message("", "Somos distribuidor autorizado em Angola", pt).unsupported)
    assert any("license_claim" in u for u in validate_message("", "Importateur agréé de matériel médical", fr).unsupported)
    assert validate_message("", "Importateur agréé de matériel médical", {**fr, "license_verified": True}).ok
    assert any("regulatory_claim" in u for u in validate_message("", "Équipement approuvé par la FDA", fr).unsupported)
    assert any("medical_claim" in u for u in validate_message("", "Tratamento sem efeitos secundários", pt).unsupported)
    # accents are ignored when matching, so dropping them does not slip a claim through
    assert any("regulatory_claim" in u for u in validate_message("", "Equipement approuve par la FDA", fr).unsupported)
    # ordinary supply wording and warranties are fine
    assert validate_message("", "Nous fournissons du matériel hospitalier avec garantie de 12 mois", {**fr, "numbers": [12]}).ok
    assert validate_message("", "Fornecemos equipamento hospitalar com garantia de 12 meses. Procura por monitores?",
                            {**pt, "numbers": [12]}).ok
    # ... but a figure that is not in the catalogue is still refused in these languages
    assert "unsupported_figure:12" in validate_message("", "garantie de 12 mois", fr).unsupported


def test_ad_claims_in_french_and_portuguese():
    assert claim_issues("Le meilleur prix du marché", MEDICAL, "fr")
    assert claim_issues("Melhor preço garantido", MEDICAL, "pt")
    assert claim_issues("Ordinateurs flambant neuf", "refurbished_laptop", "fr")
    assert claim_issues("Ordinateurs flambant neuf", "refurbished_laptop", "fr", {"stock": "new"}) == []
    assert claim_issues("Garantie 12 mois sur le matériel médical", MEDICAL, "fr") == []
    assert claim_issues("Garantia de 12 meses", MEDICAL, "pt") == []


def test_opt_out_is_recognised_in_french_and_portuguese():
    assert languages.opt_out_in("Merci de me désabonner de vos envois")
    assert languages.opt_out_in("merci de me desabonner")  # accent dropped
    assert languages.opt_out_in("Por favor, cancelar subscrição")
    assert languages.opt_out_in("Remova-me da lista")
    assert not languages.opt_out_in("Pouvez-vous nous envoyer un devis pour 20 ordinateurs ?")
    assert not languages.opt_out_in("Pode enviar uma cotação para 20 portáteis?")


def test_local_keywords_pass_the_ad_checks_and_use_local_country_names():
    for code, country, name in (("fr", "Cameroon", "cameroun"), ("pt", "Angola", "angola")):
        for category in ("medical_equipment", "refurbished_laptop", "used_iphone", "server_it"):
            bank = [k.format(country=name) for k in languages.AD_KEYWORDS[code][category]]
            accepted, rejected = check_keywords(bank)
            assert accepted and not rejected, (code, category, rejected)
        facts = {"category": "refurbished_laptop", "country": country, "stock": "new"}
        keywords, negatives, _ = adcopy.keyword_plan(facts, None, code)
        assert any(name in k for k in keywords)
        assert not any("refurbished" in k or "reconditionn" in k for k in keywords)


def test_the_new_regions_appear_in_every_search_table():
    for country in SOUTHERN + CENTRAL:
        assert country in COUNTRY_CODES


def test_reset_brings_back_new_defaults_without_touching_other_settings(session):
    commercial.update(session, {"min_margin_pct": {"refurbished_laptop": 20}})
    # simulate a deployment that saved settings before the new markets existed
    from app.database.models import SystemState

    row = session.get(SystemState, commercial.KEY)
    stored = dict(row.value)
    stored["target_markets"] = {"refurbished_laptop": ["Kenya"]}
    stored["languages"] = {"Saudi Arabia": "ar"}
    row.value = stored
    session.flush()
    assert commercial.get(session)["target_markets"]["refurbished_laptop"] == ["Kenya"]

    result = commercial.reset(session, ["target_markets", "languages"])
    assert set(SOUTHERN + CENTRAL) <= set(result["target_markets"]["refurbished_laptop"])
    assert result["languages"]["Angola"] == "pt" and result["languages"]["Gabon"] == "fr"
    assert result["min_margin_pct"]["refurbished_laptop"] == 20  # untouched
    with pytest.raises(commercial.CommercialSettingsError):
        commercial.reset(session, ["no_such_setting"])
    with pytest.raises(commercial.CommercialSettingsError):
        commercial.reset(session, [])
