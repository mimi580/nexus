"""Fake world used by simulation mode.

Everything here is invented test data and is labelled as such: simulated
records are written with source="simulation" and evidence kind
MODEL_HYPOTHESIS / UNVERIFIED_CLAIM so they can never be mistaken for
verified facts.
"""

from __future__ import annotations

from app.core.types import ProductCategory

MARKETS = [
    # country, region, base factor profile (0-1 scales)
    ("Kenya", "East Africa", dict(demand=0.78, procurement=0.72, purchasing_power=0.55, competition=0.55, regulation=0.6, logistics=0.7, payment_risk=0.45, supplier_availability=0.7)),
    ("Nigeria", "West Africa", dict(demand=0.85, procurement=0.6, purchasing_power=0.5, competition=0.65, regulation=0.5, logistics=0.5, payment_risk=0.65, supplier_availability=0.6)),
    ("Ghana", "West Africa", dict(demand=0.62, procurement=0.65, purchasing_power=0.5, competition=0.45, regulation=0.6, logistics=0.6, payment_risk=0.5, supplier_availability=0.55)),
    ("Rwanda", "East Africa", dict(demand=0.55, procurement=0.8, purchasing_power=0.45, competition=0.35, regulation=0.75, logistics=0.65, payment_risk=0.35, supplier_availability=0.5)),
    ("Tanzania", "East Africa", dict(demand=0.6, procurement=0.6, purchasing_power=0.42, competition=0.45, regulation=0.55, logistics=0.6, payment_risk=0.5, supplier_availability=0.55)),
    ("Egypt", "North Africa", dict(demand=0.7, procurement=0.65, purchasing_power=0.5, competition=0.7, regulation=0.55, logistics=0.7, payment_risk=0.55, supplier_availability=0.75)),
    ("South Africa", "Southern Africa", dict(demand=0.72, procurement=0.75, purchasing_power=0.68, competition=0.8, regulation=0.75, logistics=0.85, payment_risk=0.3, supplier_availability=0.85)),
    ("Romania", "Southeastern Europe", dict(demand=0.6, procurement=0.7, purchasing_power=0.62, competition=0.7, regulation=0.8, logistics=0.85, payment_risk=0.25, supplier_availability=0.7)),
    ("Moldova", "Eastern Europe", dict(demand=0.5, procurement=0.55, purchasing_power=0.45, competition=0.4, regulation=0.7, logistics=0.7, payment_risk=0.35, supplier_availability=0.5)),
    ("Serbia", "Southeastern Europe", dict(demand=0.55, procurement=0.62, purchasing_power=0.58, competition=0.55, regulation=0.75, logistics=0.8, payment_risk=0.3, supplier_availability=0.6)),
]

EUROPE_ALLOWED_CATEGORIES = {ProductCategory.IPHONE.value}

COMPANIES = [
    dict(name="Mombasa Road Medical Centre", domain="mrmedical.co.ke", country="Kenya", city="Nairobi", segment="private hospital", size_indicator="120 beds", category=ProductCategory.MEDICAL.value, signals=["tender notice for patient monitors", "new ICU wing announced"]),
    dict(name="Lakeview County Referral Hospital", domain="lakeviewhospital.go.ke", country="Kenya", city="Kisumu", segment="public hospital", size_indicator="300 beds", category=ProductCategory.MEDICAL.value, signals=["published equipment tender"]),
    dict(name="Tuskys Pharma Distributors", domain="tuskyspharma.co.ke", country="Kenya", city="Nairobi", segment="pharmaceutical distributor", size_indicator="45 staff", category=ProductCategory.PHARMA.value, signals=["expanding cold chain capacity"]),
    dict(name="Accra Diagnostics Group", domain="accradiagnostics.com.gh", country="Ghana", city="Accra", segment="diagnostic chain", size_indicator="8 branches", category=ProductCategory.MEDICAL.value, signals=["opening two new labs"]),
    dict(name="Lagos Teaching Hospital Trust", domain="lthtrust.ng", country="Nigeria", city="Lagos", segment="teaching hospital", size_indicator="700 beds", category=ProductCategory.MEDICAL.value, signals=["capital equipment budget approved"]),
    dict(name="Kigali Innovation Academy", domain="kigaliacademy.rw", country="Rwanda", city="Kigali", segment="private school", size_indicator="900 students", category=ProductCategory.LAPTOP.value, signals=["1:1 laptop programme announced"]),
    dict(name="Dar Business College", domain="darbusinesscollege.ac.tz", country="Tanzania", city="Dar es Salaam", segment="tertiary college", size_indicator="2400 students", category=ProductCategory.LAPTOP.value, signals=["computer lab refresh tender"]),
    dict(name="Nairobi Fintech Labs", domain="nairobifintechlabs.io", country="Kenya", city="Nairobi", segment="technology company", size_indicator="180 staff", category=ProductCategory.LAPTOP.value, signals=["hiring 40 engineers"]),
    dict(name="Cairo Cloud Services", domain="cairocloud.eg", country="Egypt", city="Cairo", segment="hosting provider", size_indicator="datacentre operator", category=ProductCategory.SERVER_IT.value, signals=["capacity expansion posted"]),
    dict(name="Jozi Data Centres", domain="jozidc.co.za", country="South Africa", city="Johannesburg", segment="datacentre", size_indicator="3 facilities", category=ProductCategory.SERVER_IT.value, signals=["server refresh cycle due"]),
    dict(name="Bucharest Mobile Retail", domain="bucharestmobile.ro", country="Romania", city="Bucharest", segment="phone retailer", size_indicator="22 stores", category=ProductCategory.IPHONE.value, signals=["seeking refurbished stock"]),
    dict(name="Chisinau Phone Market", domain="chisinauphone.md", country="Moldova", city="Chisinau", segment="phone wholesaler", size_indicator="wholesale", category=ProductCategory.IPHONE.value, signals=["import volumes rising"]),
    dict(name="Nakuru Phone Hub", domain="nakuruphonehub.co.ke", country="Kenya", city="Nakuru", segment="phone retailer", size_indicator="6 stores", category=ProductCategory.IPHONE.value, signals=["advertising trade-in programme"]),
]

CONTACT_ROLES = {
    ProductCategory.MEDICAL.value: ["Head of Procurement", "Biomedical Engineering Manager"],
    ProductCategory.PHARMA.value: ["Procurement Manager", "Supply Chain Lead"],
    ProductCategory.LAPTOP.value: ["IT Manager", "Operations Director"],
    ProductCategory.SERVER_IT.value: ["Infrastructure Manager", "CTO"],
    ProductCategory.IPHONE.value: ["Purchasing Manager", "Owner"],
}

FIRST_NAMES = ["Achieng", "Brian", "Chidi", "Diana", "Emeka", "Faith", "Grace", "Hassan", "Ioana", "James", "Kwame", "Lucia", "Mercy", "Nadia", "Omar", "Peter"]
LAST_NAMES = ["Omondi", "Mwangi", "Okafor", "Njeri", "Abara", "Kimani", "Mensah", "Ali", "Popescu", "Otieno", "Asante", "Marin", "Wanjiru", "Hassan", "Farah", "Kariuki"]

SUPPLIERS = [
    dict(name="Shenzhen Refurb Mobile Ltd", country="China", categories=[ProductCategory.IPHONE.value], reliability=0.72, lead_time_days=18, payment_terms="50% deposit, balance on shipment", documents=["commercial invoice", "packing list"]),
    dict(name="Dubai IT Liquidators FZE", country="UAE", categories=[ProductCategory.LAPTOP.value, ProductCategory.SERVER_IT.value], reliability=0.8, lead_time_days=12, payment_terms="T/T in advance", documents=["commercial invoice", "certificate of origin"]),
    dict(name="Rotterdam Medical Surplus BV", country="Netherlands", categories=[ProductCategory.MEDICAL.value], reliability=0.85, lead_time_days=25, payment_terms="30% deposit, 70% before shipment", documents=["CE documentation on file", "service history"]),
    dict(name="Mumbai Pharma Exports Pvt", country="India", categories=[ProductCategory.PHARMA.value], reliability=0.68, lead_time_days=30, payment_terms="LC at sight", documents=["export licence", "batch certificates"]),
    dict(name="Nairobi Trade Partners Ltd", country="Kenya", categories=[ProductCategory.LAPTOP.value, ProductCategory.IPHONE.value], reliability=0.62, lead_time_days=7, payment_terms="net 15", documents=["invoice"]),
]

# A fictional licence so simulated runs exercise both licensed (Kenya) and
# unlicensed (Ghana, Nigeria) regulated markets. Never used outside simulation.
LICENSES = [
    dict(
        holder_name="Simulated Operator Ltd",
        country="Kenya",
        issuing_authority="Simulated Regulator",
        license_number="SIM-KE-0001",
        license_types=["importer", "distributor"],
        product_categories=[ProductCategory.MEDICAL.value, ProductCategory.PHARMA.value],
        valid_from="2026-01-01",
        expires_on="2027-12-31",
        scope_notes="simulation fixture",
    ),
]

UNIT_ECONOMICS = {
    ProductCategory.LAPTOP.value: dict(unit_cost=(190.0, 240.0), unit_price=(320.0, 360.0), typical_qty=40),
    ProductCategory.IPHONE.value: dict(unit_cost=(240.0, 300.0), unit_price=(370.0, 430.0), typical_qty=60),
    ProductCategory.MEDICAL.value: dict(unit_cost=(1800.0, 2600.0), unit_price=(3200.0, 4100.0), typical_qty=6),
    ProductCategory.PHARMA.value: dict(unit_cost=(12.0, 18.0), unit_price=(21.0, 28.0), typical_qty=2000),
    ProductCategory.SERVER_IT.value: dict(unit_cost=(1400.0, 1900.0), unit_price=(2300.0, 2900.0), typical_qty=10),
}

# Inbound reply templates keyed by the category the responder will be classified as.
REPLY_TEMPLATES = {
    "interested": "Thanks for reaching out. This is relevant to us - we are reviewing options this quarter. Could you share availability and condition grading?",
    "price_request": "Please send your best pricing for the quantities you mentioned, delivered to our premises.",
    "rfq": "We have a formal requirement open. Please respond to our RFQ with unit pricing, lead time, warranty and payment terms.",
    "information_request": "Could you send more detail on specifications, warranty and after-sales support before we take this further?",
    "not_interested": "Thank you, but we are not looking at this category at the moment.",
    "wrong_contact": "I am not the right person for this. Procurement handles purchasing decisions here.",
    "unsubscribe": "Please remove me from your list and do not contact me again.",
    "complaint": "This is unsolicited and unwelcome. I am reporting this message.",
    "regulatory_issue": "Any supply of this nature requires registration with our national regulator. We cannot proceed without documentation.",
    "negotiation": "Your indicative pricing is above our budget. If you can improve terms we can talk.",
}

# How the simulated world responds, by product category (probability weights).
RESPONSE_MIX = {
    ProductCategory.LAPTOP.value: [("interested", 0.18), ("price_request", 0.14), ("information_request", 0.1), ("not_interested", 0.24), ("wrong_contact", 0.08), ("unsubscribe", 0.06), ("complaint", 0.02), ("no_reply", 0.5)],
    ProductCategory.IPHONE.value: [("price_request", 0.2), ("interested", 0.14), ("rfq", 0.06), ("not_interested", 0.24), ("wrong_contact", 0.08), ("unsubscribe", 0.06), ("complaint", 0.02), ("no_reply", 0.5)],
    ProductCategory.MEDICAL.value: [("rfq", 0.14), ("interested", 0.14), ("information_request", 0.16), ("regulatory_issue", 0.08), ("not_interested", 0.22), ("wrong_contact", 0.06), ("unsubscribe", 0.04), ("no_reply", 0.5)],
    ProductCategory.PHARMA.value: [("regulatory_issue", 0.24), ("information_request", 0.14), ("interested", 0.08), ("not_interested", 0.26), ("unsubscribe", 0.06), ("complaint", 0.04), ("no_reply", 0.5)],
    ProductCategory.SERVER_IT.value: [("interested", 0.16), ("price_request", 0.16), ("rfq", 0.08), ("not_interested", 0.24), ("wrong_contact", 0.06), ("unsubscribe", 0.04), ("no_reply", 0.5)],
}
