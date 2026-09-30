"""Server-rendered public pages. No external scripts, fonts or trackers: they
load fast on mobile data, and attribution is captured server-side."""

from __future__ import annotations

from html import escape
from typing import Any
from urllib.parse import quote

ATTRIBUTION_PARAMS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "gclid",
                      "fbclid", "nx", "nv")

STYLE = """
:root{--ink:#14202b;--muted:#5b6b7a;--line:#e3e8ee;--bg:#f7f9fb;--card:#fff;--accent:#0b6e4f;--accent-ink:#fff;--wa:#1f8f4e}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1040px;margin:0 auto;padding:0 18px}
header.top{background:var(--card);border-bottom:1px solid var(--line)}
header.top .wrap{display:flex;justify-content:space-between;align-items:center;padding-top:12px;padding-bottom:12px;gap:12px}
.brand{font-weight:700;letter-spacing:.01em}
.hero{padding:44px 0 28px}
.hero h1{font-size:clamp(28px,5vw,42px);line-height:1.15;margin:0 0 12px;letter-spacing:-.01em}
.hero p.sub{font-size:18px;color:var(--muted);margin:0 0 22px;max-width:680px}
.btns{display:flex;gap:10px;flex-wrap:wrap}
.btn{display:inline-block;padding:13px 20px;border-radius:8px;font-weight:600;text-decoration:none;border:0;font-size:16px;cursor:pointer}
.btn.primary{background:var(--accent);color:var(--accent-ink)}.btn.wa{background:var(--wa);color:#fff}
.trust{display:flex;flex-wrap:wrap;gap:8px 20px;color:var(--muted);font-size:14px;margin-top:18px}
.trust span:before{content:"\\2713  ";color:var(--accent);font-weight:700}
section{padding:26px 0}
h2{font-size:22px;margin:0 0 14px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}
.card h3{margin:0 0 6px;font-size:16px}.card p{margin:0;color:var(--muted);font-size:14px}
.price{font-size:15px;margin-top:8px}.price b{font-size:20px}
ul.benefits{padding-left:20px;margin:0}ul.benefits li{margin:6px 0}
.steps{counter-reset:s;display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px}
.steps div{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}
.steps div:before{counter-increment:s;content:counter(s);display:inline-block;width:28px;height:28px;border-radius:50%;background:var(--accent);color:#fff;text-align:center;line-height:28px;font-weight:700;margin-bottom:8px}
form.enq{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px;display:grid;gap:12px;grid-template-columns:1fr 1fr}
form.enq .full{grid-column:1/-1}
label{font-size:14px;font-weight:600;display:block;margin-bottom:4px}
input,select,textarea{width:100%;font:inherit;padding:11px 12px;border:1px solid #c9d3dd;border-radius:8px;background:#fff;color:var(--ink)}
textarea{min-height:110px}
input:focus,select:focus,textarea:focus,.btn:focus-visible{outline:3px solid #9ad3bf;outline-offset:1px}
.consent{display:flex;gap:10px;align-items:flex-start;font-size:14px;color:var(--muted)}.consent input{width:auto;margin-top:4px}
.hp{position:absolute;left:-5000px}
details{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 16px;margin-bottom:10px}
summary{font-weight:600;cursor:pointer}
footer{padding:28px 0 40px;color:var(--muted);font-size:14px;border-top:1px solid var(--line);margin-top:20px}
footer a{color:var(--muted)}
@media(max-width:640px){form.enq{grid-template-columns:1fr}.hero{padding-top:28px}}
"""


def _page(title: str, body: str, description: str = "") -> str:
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{escape(title)}</title><meta name='description' content='{escape(description)}'>"
        f"<style>{STYLE}</style></head><body>{body}</body></html>"
    )


def _header(facts: dict[str, Any], wa_link: str | None) -> str:
    wa = f"<a class='btn wa' href='{escape(wa_link)}'>WhatsApp us</a>" if wa_link else ""
    return (f"<header class='top'><div class='wrap'><span class='brand'>{escape(facts.get('business_name') or '')}</span>"
            f"{wa}</div></header>")


def _footer(facts: dict[str, Any]) -> str:
    address = escape(facts.get("business_address") or "")
    return (f"<footer><div class='wrap'><div>{escape(facts.get('business_name') or '')}"
            f"{' &middot; ' + address if address else ''}</div>"
            "<div style='margin-top:6px'><a href='/privacy'>Privacy notice</a></div></div></footer>")


def render_landing(page: Any, params: dict[str, str], wa_link: str | None) -> str:
    c, f = page.content or {}, page.facts or {}
    hidden = "".join(
        f"<input type='hidden' name='{k}' value='{escape(params.get(k, ''))}'>" for k in ATTRIBUTION_PARAMS
    )
    trust = [f"Serving {escape(f.get('country', ''))}", f"Reply {escape(f.get('response_promise', ''))}"]
    if f.get("licence_statement"):
        trust.append(escape(f["licence_statement"]))
    products = ""
    for p in f.get("products") or []:
        bits = [x for x in (p.get("condition"), f"warranty: {p['warranty']}" if p.get("warranty") else None,
                            f"MOQ {p['moq']}" if p.get("moq") else None) if x]
        products += f"<div class='card'><h3>{escape(p['name'])}</h3><p>{escape(' · '.join(bits))}</p></div>"
    price = ""
    if f.get("from_price_usd"):
        price = (f"<p class='price'>Indicative prices from <b>USD {f['from_price_usd']:g}</b> per unit "
                 "&mdash; your quotation depends on quantity, specification and delivery.</p>")
    benefits = "".join(f"<li>{escape(b)}</li>" for b in c.get("benefits") or [])
    faq = "".join(f"<details><summary>{escape(q.get('q', ''))}</summary><p>{escape(q.get('a', ''))}</p></details>"
                  for q in c.get("faq") or [])
    product_options = "".join(f"<option>{escape(p['name'])}</option>" for p in f.get("products") or [])
    wa_button = f"<a class='btn wa' href='{escape(wa_link)}'>Chat on WhatsApp</a>" if wa_link else ""
    body = f"""
{_header(f, wa_link)}
<main>
<div class='wrap hero'>
  <h1>{escape(c.get('headline', ''))}</h1>
  <p class='sub'>{escape(c.get('subheadline', ''))}</p>
  <div class='btns'><a class='btn primary' href='#quote'>{escape(c.get('cta') or 'Request a quotation')}</a>{wa_button}</div>
  <div class='trust'>{''.join(f'<span>{t}</span>' for t in trust)}</div>
</div>
{f"<section><div class='wrap'><h2>What we supply</h2>{price}<div class='grid'>{products}</div></div></section>" if products else ''}
{f"<section><div class='wrap'><h2>Why buyers work with us</h2><ul class='benefits'>{benefits}</ul></div></section>" if benefits else ''}
<section><div class='wrap'><h2>How it works</h2><div class='steps'>
  <div><b>Tell us what you need</b><p>Models, quantity and delivery location.</p></div>
  <div><b>Get a written quotation</b><p>Price, condition, warranty, lead time and terms, {escape(f.get('response_promise', ''))}.</p></div>
  <div><b>Confirm and receive</b><p>You only commit when you accept the quotation.</p></div>
</div></div></section>
<section id='quote'><div class='wrap'><h2>Request a quotation</h2>
<form class='enq' method='post' action='/p/{escape(page.slug)}/enquiry'>
  {hidden}
  <div class='hp' aria-hidden='true'><label>Website<input name='website' tabindex='-1' autocomplete='off'></label></div>
  <div><label for='n'>Your name *</label><input id='n' name='full_name' required maxlength='200' autocomplete='name'></div>
  <div><label for='o'>Organisation *</label><input id='o' name='organisation' required maxlength='300' autocomplete='organization'></div>
  <div><label for='e'>Work e-mail *</label><input id='e' name='email' type='email' required maxlength='200' autocomplete='email'></div>
  <div><label for='ph'>Phone / WhatsApp</label><input id='ph' name='phone' maxlength='60' autocomplete='tel'></div>
  <div><label for='pr'>Product</label><select id='pr' name='product'><option value=''>Any / not sure</option>{product_options}</select></div>
  <div><label for='q'>Quantity</label><input id='q' name='quantity' type='number' min='1' max='1000000' inputmode='numeric'></div>
  <div class='full'><label for='m'>What do you need?</label><textarea id='m' name='message' maxlength='4000' placeholder='Specification, delivery location, timing'></textarea></div>
  <label class='consent full'><input type='checkbox' name='consent' value='yes' required> I agree to be contacted about this enquiry. See the <a href='/privacy'>privacy notice</a>.</label>
  <div class='full'><button class='btn primary' type='submit'>{escape(c.get('cta') or 'Request a quotation')}</button></div>
</form></div></section>
{f"<section><div class='wrap'><h2>Questions</h2>{faq}</div></section>" if faq else ''}
{f"<section><div class='wrap'><h2>About us</h2><p>{escape(c.get('about', ''))}</p></div></section>" if c.get('about') else ''}
</main>
{_footer(f)}"""
    return _page(c.get("headline") or f.get("category_title", "Quotation"), body, c.get("subheadline") or "")


def render_thanks(page: Any, wa_link: str | None) -> str:
    f = page.facts or {}
    wa = f"<p><a class='btn wa' href='{escape(wa_link)}'>Continue on WhatsApp</a></p>" if wa_link else ""
    body = (f"{_header(f, wa_link)}<main><div class='wrap hero'><h1>Thank you &mdash; we have your enquiry</h1>"
            f"<p class='sub'>We will reply by e-mail {escape(f.get('response_promise', ''))} with a written quotation or any questions.</p>"
            f"{wa}</div></main>{_footer(f)}")
    return _page("Thank you", body)


def render_privacy(settings: Any) -> str:
    name = escape(settings.business_name or "We")
    address = escape(settings.business_postal_address or "")
    contact = escape(settings.email_reply_to or settings.email_sender_address or "")
    body = f"""<main><div class='wrap' style='padding:40px 18px;max-width:760px'>
<h1>Privacy notice</h1>
<p>{name}{', ' + address if address else ''} uses the details you send through our enquiry forms (name, organisation,
e-mail, phone, country, the products and quantities you ask about and your message) only to answer your enquiry,
prepare quotations and follow up on them.</p>
<p>When you arrive from an advertisement we record which advertisement it was, so we can tell which advertising
works. We may share a one-way encrypted (hashed) form of your e-mail or phone number, and the advertising click
identifier, with the advertising platform that showed you the ad, only to measure results.</p>
<p>We do not sell your details. We keep them while we are dealing with your enquiry and for our business records.
You can ask us to delete them, or stop contacting you, at any time{': ' + contact if contact else ''}.</p>
</div></main>"""
    return _page("Privacy notice", body)


def render_index(pages: list[Any], settings: Any) -> str:
    name = escape(settings.business_name or "Sourcing")
    cards = "".join(
        f"<a class='card' style='text-decoration:none;color:inherit' href='/p/{escape(p.slug)}'><h3>{escape((p.content or {}).get('headline', p.slug))}</h3>"
        f"<p>{escape((p.content or {}).get('subheadline', ''))}</p></a>"
        for p in pages
    )
    body = (f"<header class='top'><div class='wrap'><span class='brand'>{name}</span></div></header>"
            f"<main><div class='wrap hero'><h1>{name}</h1><p class='sub'>B2B sourcing and supply. Choose a product line to request a quotation.</p>"
            f"<div class='grid'>{cards or '<p>Product pages are being prepared.</p>'}</div></div></main>")
    return _page(settings.business_name or "Sourcing", body)


def whatsapp_link(settings: Any, page: Any | None = None) -> str | None:
    number = "".join(ch for ch in (settings.whatsapp_number or "") if ch.isdigit())
    if not number:
        return None
    topic = (page.facts or {}).get("category_title", "your products").lower() if page is not None else "your products"
    text = f"Hello, I would like a quotation for {topic}."
    return f"https://wa.me/{number}?text={quote(text)}"
