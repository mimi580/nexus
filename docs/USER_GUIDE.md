# NEXUS AI — User Guide

This guide is for the person running NEXUS: setting it up, putting it on a server, feeding it the commercial data it needs, and working with it day to day. You do not need to read the code to use anything described here.

**Contents**

1. [What NEXUS does](#1-what-nexus-does)
2. [How a deal moves through NEXUS](#2-how-a-deal-moves-through-nexus)
3. [Try it on your own computer (simulation)](#3-try-it-on-your-own-computer-simulation)
4. [What you need before going live](#4-what-you-need-before-going-live)
5. [Setting up your outreach e-mail domain](#5-setting-up-your-outreach-e-mail-domain)
6. [Deploying to a server](#6-deploying-to-a-server)
7. [First-time setup in the dashboard](#7-first-time-setup-in-the-dashboard)
8. [Going live safely](#8-going-live-safely)
9. [Your daily routine](#9-your-daily-routine)
10. [The review queue](#10-the-review-queue)
11. [Supplier offers and the price book](#11-supplier-offers-and-the-price-book)
12. [Licences and regulated products](#12-licences-and-regulated-products)
13. [Commercial settings](#13-commercial-settings)
14. [How NEXUS finds buyers and contacts](#14-how-nexus-finds-buyers-and-contacts)
    - [Finding and engaging suppliers](#finding-and-engaging-suppliers)
15. [E-mail: sending, replies, bounces and opt-outs](#15-e-mail-sending-replies-bounces-and-opt-outs)
16. [Budget and costs](#16-budget-and-costs)
17. [Landing pages and enquiries](#17-landing-pages-and-enquiries)
18. [Advertising on Google and Meta](#18-advertising-on-google-and-meta)
19. [How NEXUS learns and improves](#19-how-nexus-learns-and-improves)
20. [Alerts on your phone](#20-alerts-on-your-phone)
21. [Command reference](#21-command-reference)
22. [API reference](#22-api-reference)
23. [Configuration reference](#23-configuration-reference)
24. [Policy rules reference](#24-policy-rules-reference)
25. [Backups, updates and recovery](#25-backups-updates-and-recovery)
26. [Troubleshooting](#26-troubleshooting)

---

## 1. What NEXUS does

NEXUS is an autonomous B2B sales and sourcing assistant for five product lines: refurbished laptops, used/refurbished iPhones, medical equipment, pharmaceutical products and servers/IT equipment. It:

- researches which countries are worth pursuing for each product line,
- finds organisations that could buy (from web search results and their own websites),
- finds a published e-mail address for each organisation,
- qualifies and scores each opportunity, with the reasons shown,
- matches it to a real supplier offer from **your** catalogue and calculates landed cost and margin,
- writes and sends a personalised first e-mail and a limited number of follow-ups,
- reads replies, recognises bounces, auto-replies and opt-outs,
- drafts answers to interested buyers (including an indicative price) **for you to approve**,
- publishes landing pages and runs Google and Meta ads for every line except pharmaceuticals, turning enquiries into deals,
- learns from real outcomes (replies, enquiries, quotes and the deals you record) and changes what it does accordingly, and
- tells you, by Telegram or e-mail, whenever it needs a decision.

### What it will never do

These are enforced by code, not by instructions to the AI:

| NEXUS will never… | Enforced by |
|---|---|
| Invent a company, person, e-mail address, price, stock level, certificate or licence | Everything must come from a retrieved web page, your catalogue or your licence register; the AI's proposals are checked word-for-word against the source |
| Send a price or quote without your approval | Rule R-REPLY-01 |
| Make a financial or legal commitment | Rules R-FIN-01, R-LEG-01 |
| Execute a regulated (medical/pharma) transaction | Rules R-REG-01, R-REG-05 |
| Contact someone who opted out, bounced or complained | Rules R-OUT-01 to R-OUT-04 |
| E-mail the same person twice in a day, or exceed your daily limit | Rules R-RATE-01, R-RATE-02 |
| Spend more than your monthly budget (USD 500 unless you change it) | Every paid action, and all ad spend, is booked against the budget; paid work and ads stop when it is used |
| Advertise pharmaceuticals | Rule R-ADS-01 |
| Launch an ad campaign without your approval (unless you switch that off) | Rule R-ADS-03 |
| Loop forever or run up costs | Every run has iteration, time, cost, action and no-progress limits |

### Two modes

| | Simulation | Production |
|---|---|---|
| Buyers, suppliers, replies | Invented, fictional world | Real web research and real e-mail |
| AI model | Built-in mock (free, no key) | Anthropic Claude (or any OpenAI-compatible API) |
| E-mails | Nothing leaves the machine | Sent through your SMTP mailbox |
| Purpose | Learn the dashboard, test changes | Real business |

The mode is set by `NEXUS_MODE`. Production refuses to run until every required setting is present (the dashboard shows what is missing).

---

## 2. How a deal moves through NEXUS

```
Objective (you)                    e.g. "Medical equipment pipeline in East Africa"
   │
Market research (weekly)           scores your target countries from search evidence
   │
Prospect discovery                 organisations named in search results → DISCOVERED
   │
Company research                   their website + search → RESEARCHED
   │
Contact discovery                  a published e-mail on their own site
   │
Qualification + scoring            explainable score; low scores are dropped → SCORED
   │
Sourcing + economics               your supplier offer + your price book → landed cost, margin → TARGETED
   │                               (no offer / no price → waits in review until you add one)
Sales strategy + first e-mail      checked by policy → OUTREACH   (or → your review queue)
   │
Follow-ups (max 2–3)               stop automatically on any reply, opt-out or bounce → FOLLOW_UP
   │
Reply classified                   interested / price request / RFQ / not interested / unsubscribe / …
   │
Draft answer with suggested price  → your review queue → you edit and approve → NEGOTIATION
   │
You close the deal                 record WON or LOST in the dashboard → LEARNING
```

Every stage change is stored with its reason, and every decision (allowed, blocked or escalated) is in the audit log.

**Where you come in:** the review queue (a few minutes a day), keeping the supplier catalogue and price book current, and recording won/lost deals.

---

## 3. Try it on your own computer (simulation)

You can run the whole platform in simulation without any accounts or keys.

### Option A — Docker Desktop (Windows, Mac or Linux)

1. Install Docker Desktop and Git.
2. Open a terminal (PowerShell on Windows):

   ```bash
   git clone https://github.com/mimi580/nexus.git
   cd nexus
   cp .env.example .env
   python scripts/generate_secrets.py >> .env
   ```

   On Windows PowerShell use `copy .env.example .env` and `python scripts\generate_secrets.py | Add-Content .env`.

3. Open `.env` and set `NEXUS_DOMAIN=localhost`. Leave `NEXUS_MODE=simulation`.
4. Start everything except the HTTPS proxy:

   ```bash
   docker compose up -d --build db migrate api worker
   ```

5. Open <http://localhost:8000>. Log in with `DASHBOARD_USERNAME` from `.env` and the `DASHBOARD_PASSWORD` that `generate_secrets.py` added.

### Option B — Python only

Requires Python 3.12 or newer.

```bash
git clone https://github.com/mimi580/nexus.git && cd nexus
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
echo "DATABASE_URL=sqlite+pysqlite:///./data/nexus.sqlite3" > .env
python -m app.cli init-db
uvicorn app.api.main:app --port 8000     # dashboard at http://localhost:8000 (no login in development)
```

In a second terminal, start the background worker:

```bash
python scripts/worker.py --interval 60
```

### Run a full simulated sales cycle

```bash
python -m app.cli simulate --days 10
```

This creates an objective across all five product lines and runs ten simulated days end to end, in a separate throwaway database with no real services. It is safe to run on a live server and never touches your real data. It prints a report: pipeline by stage, messages, evidence records, escalations, blocked actions, budget used, learning metrics and an `ads` section (campaigns, landing pages and enquiries by source). The simulated world includes a small sample catalogue, so ads are planned, launched on a simulated Google and Meta (no accounts, no money), and some simulated clicks become enquiries that flow through the normal enquiry → deal → quotation path. With Docker: `docker compose exec api nexus simulate --days 10`.

In the dashboard, create an objective on the Overview tab, press **Run a pass now** a few times, and watch the Pipeline and Reviews tabs fill up. To try ads in the dashboard's simulation, add an offer and a selling price for a line (Catalogue tab), set `PUBLIC_SITE_URL=http://localhost:8000` in `.env`, then use **Ads → Plan campaigns**; proposals appear in Reviews, and after approval simulated spend, clicks and enquiries appear as passes run. Simulated ad spend is booked against the simulated budget.

---

## 4. What you need before going live

| Item | Why | Suggested choice | Typical cost |
|---|---|---|---|
| A server | Runs NEXUS 24/7 | Any Ubuntu 22.04/24.04 VPS with 2 GB RAM (Hetzner, DigitalOcean, Contabo…) | USD 5–12/month |
| A domain for the dashboard | HTTPS address for you and unsubscribe links | A subdomain such as `nexus.yourbusiness.com` | Included with your domain |
| A **separate** outreach domain | Keeps your main domain's reputation safe | e.g. `leonardtrade.co` if your main one is `leonardtrading.com` | ~USD 10–15/year |
| A mailbox on the outreach domain | Sending (SMTP) and reading replies (IMAP) | Zoho Mail, Google Workspace or Microsoft 365 | USD 1–7/month |
| An AI key | Research reading, classification, drafting | Anthropic API key with a monthly spend limit set in the Anthropic console | Within the USD 65 AI budget lines |
| A search key | Finding buyers from real sources | Serper (cheapest per query) or Brave Search | A few dollars a month at NEXUS volumes |
| A Telegram bot (optional) | Alerts on your phone | Free | Free |
| Google Ads and/or Meta ad accounts (optional) | Advertising (section 18) | Google Ads with a manager account and API access; Meta business portfolio with a system user | Your ad spend, within the ads budget line |
| A customer-facing domain (optional) | Landing pages for ads | e.g. `yourbrand.com` | ~USD 10–15/year |
| Your commercial data | Real costs and selling prices | Supplier quotes and market price checks | Your time |

**Which search provider:** use **Serper**. It returns Google's results, which cover African businesses far better than other indexes, it is the only one of the three whose business listings (Google Maps: hospitals, pharmacies, schools and wholesalers with their websites) NEXUS can use, and it is the cheapest per query. Brave has its own, smaller index; Tavily returns page text but NEXUS already reads pages itself.

**Search provider notes (as of September 2026 — check current prices):** Serper offers 2,500 free queries and then about USD 1 per 1,000; Brave Search API includes USD 5 of monthly credit (about 1,000 queries) and then USD 5 per 1,000; Tavily has 1,000 free credits a month. NEXUS typically uses a few hundred to about a thousand queries a month. Set `SEARCH_COST_PER_QUERY_USD` to your plan's price (`0.001` for Serper, `0.005` for Brave, `0.008` for Tavily) so the budget is accurate.

**Legal and compliance:** NEXUS identifies you as the sender in every e-mail and honours opt-outs immediately, but it is not legal advice. B2B e-mail rules differ by country. If you e-mail buyers in the EU (the iPhone markets in Romania, Bulgaria and similar), GDPR applies, including a legitimate-interest basis for contacting business addresses and an easy opt-out. Kenya's Data Protection Act 2019 also applies to personal data you hold. Take advice if you are unsure.

---

## 5. Setting up your outreach e-mail domain

Good deliverability is the difference between replies and the spam folder. Do these once, in your domain's DNS settings and your mail provider's admin panel.

1. **Buy a separate domain** for outreach. Never send cold e-mail from your main business domain or from any employer's domain.
2. **Create one mailbox**, for example `francis@leonardtrade.co`. This will be `EMAIL_SENDER_ADDRESS`, `SMTP_USERNAME` and `IMAP_USERNAME`.
3. **Add the DNS records your mail provider gives you:**
   - **MX** — so replies reach the mailbox.
   - **SPF** — a TXT record such as `v=spf1 include:zoho.com ~all` (your provider tells you the exact value).
   - **DKIM** — a TXT record generated in your provider's admin panel. Enable signing after adding it.
   - **DMARC** — a TXT record at `_dmarc.leonardtrade.co`, starting with `v=DMARC1; p=none; rua=mailto:francis@leonardtrade.co`. Move to `p=quarantine` after a few clean weeks.
4. **Create an app password** if your provider supports one (Google and Zoho do). Use it as `SMTP_PASSWORD` and `IMAP_PASSWORD`, not your main password.
5. **Warm up the mailbox.** For the first two weeks send a few normal e-mails by hand and reply to some. Start NEXUS with `OUTREACH_DAILY_LIMIT=5`, then raise it gradually: 10, then 15, then 20 or more once replies are coming in and bounces stay under 3%.

Provider settings:

| Provider | SMTP host / port / security | IMAP host / port |
|---|---|---|
| Zoho Mail | `smtp.zoho.com` / 587 / `starttls` (or `smtppro.zoho.com` on paid plans) | `imap.zoho.com` / 993 |
| Google Workspace | `smtp.gmail.com` / 587 / `starttls` | `imap.gmail.com` / 993 |
| Microsoft 365 | `smtp.office365.com` / 587 / `starttls` | `outlook.office365.com` / 993 |

Check your provider's acceptable-use terms: some transactional e-mail services forbid unsolicited B2B e-mail even at low volume. An ordinary mailbox at low, personalised volume is the safer start.

---

## 6. Deploying to a server

### 6.1 Prepare the server (once)

1. Create an Ubuntu 22.04 or 24.04 server with at least 2 GB RAM.
2. In your DNS, add an **A record** for your dashboard name (for example `nexus.yourbusiness.com`) pointing at the server's IP address.
3. Log in as root and run:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/mimi580/nexus/main/scripts/install_server.sh | bash
   ```

   The script installs Docker, creates a `nexus` user, and opens only SSH, HTTP and HTTPS in the firewall. If the repository is private, copy `scripts/install_server.sh` to the server and run `bash install_server.sh` instead.

### 6.2 Install NEXUS

```bash
sudo -iu nexus
git clone https://github.com/mimi580/nexus.git /opt/nexus    # a private repo needs a GitHub deploy key or token
cd /opt/nexus
cp .env.example .env
python3 scripts/generate_secrets.py >> .env
nano .env
```

In `.env`, set at least:

```ini
NEXUS_ENV=production
NEXUS_MODE=simulation              # keep simulation for the first start
NEXUS_DOMAIN=nexus.yourbusiness.com
PUBLIC_BASE_URL=https://nexus.yourbusiness.com
DASHBOARD_USERNAME=francis
```

Save, then start:

```bash
docker compose up -d --build
docker compose ps                  # db, api, worker and caddy should be "running" or "healthy"
```

Open `https://nexus.yourbusiness.com`. Caddy obtains the HTTPS certificate automatically on first visit, which can take up to a minute. Log in with your username and the generated password (`grep DASHBOARD_PASSWORD .env`).

### 6.3 Check the configuration

```bash
docker compose exec api nexus doctor            # what is missing for production
docker compose exec api nexus doctor --live     # also tests search, SMTP login, IMAP login and the AI model
```

### 6.4 Nightly backups

```bash
crontab -e
# add this line:
15 2 * * *  /opt/nexus/scripts/backup.sh >> /opt/nexus/backups/backup.log 2>&1
```

Backups are kept for 14 days in `/opt/nexus/backups`. Copy them off the server regularly, for example with your VPS provider's snapshot feature or `rclone` to cloud storage.

---

## 7. First-time setup in the dashboard

Do these once, in this order. Everything can also be done from the command line (section 21).

### 7.1 Register your licence (medical and pharmaceutical)

**Licences** tab → *Register a licence*. Enter the details exactly as they appear on the licence: holder name, issuing country, issuing authority, licence number, types (for example `importer, distributor`), categories (`medical_equipment, pharmaceutical`), regional scope (for example `EAC, COMESA`), and the validity dates. Section 12 explains what the regional scope does and does not allow.

### 7.2 Set your commercial rules

**Settings** tab. Review and edit:

- `min_margin_pct` — the lowest gross margin you will accept, per category.
- `typical_order_qty` — used only when a buyer has not stated a quantity.
- `duties_taxes_pct` — duty plus VAT as a fraction per destination country (`0.16` = 16%), from the tariff schedule or your clearing agent. Countries you leave out stay unknown, and every deal there is flagged "duties unknown".
- `required_documents` — the documents a supplier offer must list before a regulated line is offered.
- `target_markets` — the countries research may consider for each category.

Press **Save settings**. Invalid values are rejected with a message.

### 7.3 Load supplier offers and prices

**Catalogue** tab → choose *Supplier offers* → **Download template** → fill it in a spreadsheet → save as CSV → **Import**. Do the same for *Selling prices*. See section 11 for the columns. Without at least one current offer and one price per category, production deals wait in review until you add them.

### 7.4 Create objectives

**Overview** tab → *New objective*. Give it a clear title and tick the product lines. One objective per focus works well, for example:

- "Medical equipment — East Africa hospitals" (medical_equipment)
- "Refurbished laptops — schools and NGOs" (refurbished_laptop)

Objectives stay active until you close them. Pause, close or reactivate one on the **System** tab. While an objective is paused, its research and follow-ups wait; replies from buyers are still processed.

---

## 8. Going live safely

Take these steps in order.

1. **Simulation on the server.** Run with `NEXUS_MODE=simulation` for a day. Get familiar with the dashboard, the review queue and the alerts.
2. **Fill in production settings.** Add the AI key, search key, sender identity, SMTP and IMAP details and notification channels to `.env`. Run `nexus doctor --live` until everything shows `ok` and the `missing` list is empty.
3. **Switch to production:**

   ```ini
   NEXUS_MODE=production
   REQUIRE_OUTREACH_APPROVAL=false
   OUTREACH_DAILY_LIMIT=5
   OUTREACH_MAX_FOLLOWUPS=2
   ```

   Then run `docker compose up -d` to apply. Buyer outreach, follow-ups and supplier RFQs now send on their own, within the policy rules, daily limits and budget. Read a sample of the sent e-mails in your mailbox's Sent folder during the first days. If you want to check every draft before it goes, set `REQUIRE_OUTREACH_APPROVAL=true` and each one will wait in your review queue.
4. **Raise `OUTREACH_DAILY_LIMIT` step by step** (5, then 10, 15, 20…) as the domain warms up and bounces stay low. Replies to buyers with prices, activating supplier quotes, and commitments always need your approval, whatever this setting says.
5. **Watch the first month closely:** bounce rate (check the System tab and your mailbox), replies, and whether the "catalogue gap" items are telling you which offers and prices to add.

To stop everything at once, press **Emergency stop** on the Overview tab. To pause only one product line or country, use the pause fields on the Settings tab.

---

## 9. Your daily routine

About 10–15 minutes:

1. **Reviews tab** — decide everything waiting. The badge on the tab shows how many items there are, and Telegram or e-mail alerts tell you when new ones arrive.
2. **Catalogue gaps** — items of kind *catalogue* say which offer or selling price is missing. Add it, and parked deals resume automatically within six hours.
3. **Suppliers tab** — glance at new suppliers and RFQs. Block anything that looks wrong. Supplier quotes to check arrive in Reviews.
4. **Pipeline tab** — when a deal is won or lost, press **Won** or **Lost**. For a win, enter revenue and margin if you can. These outcomes are what the learning loop measures, and wins are reported to the ad platforms.
5. **Leads and Ads tabs** (if ads are on) — new enquiries, and each campaign's spend, enquiries and cost per enquiry. Quotations for enquiries wait in Reviews: answer them the same day.
6. **Overview** — glance at budget used, replies and positive-reply rate.

Weekly: check the price book and offers for expired entries, look at the System tab for failed tasks or compliance events, and read the Learning tab's summary.

---

## 10. The review queue

Anything NEXUS will not do on its own, or has stopped doing, becomes one review item. The same issue raised again adds to the item's counter ("raised 3×") instead of creating duplicates.

| Kind | What it is | Your options |
|---|---|---|
| **outreach approval** | A first e-mail or follow-up that needs you (approval mode, a regulated market outside your licence, missing regulatory check) | Edit the draft, then **Approve and send**; or **Reject** |
| **reply approval** | A drafted answer to a buyer, usually with a suggested price | Edit, then **Approve and send**; or **Reject** |
| **commercial handoff** | A buyer response NEXUS could not draft an answer for | Handle it yourself, then **Mark resolved** |
| **catalogue** | A deal is waiting for a supplier offer or a selling price | Add it in the Catalogue tab; the item closes itself. NEXUS also starts supplier research for that line |
| **supplier rfq approval** | An RFQ to a supplier waiting because approval mode is on | Edit, then **Approve and send**; or **Reject** |
| **supplier quote** | Price lines read from a supplier's e-mail, as draft offers | Compare with the e-mail, then **Activate in catalogue**; or **Reject** |
| **supplier reply** | A supplier asked a question or replied without a quote | Answer from your mailbox, then **Mark resolved** |
| **compliance** | Missing supplier documents, or a regulatory point raised by a buyer | Investigate, then **Mark resolved** or **Reject** |
| **licence** | A licence is expiring or has expired | Renew and register the new licence, then **Mark resolved** |
| **other** | Anything else escalated, including mail that could not be matched to a contact | Read it and resolve |

**What the buttons do:**

- **Approve and send** re-runs the action through the full policy engine. Only the escalation that sent it to you is waived. Opt-outs, bounces, duplicates, budget and claim checks still apply, so an approved e-mail can still be blocked if something changed, and you will see why. The text in the edit box is exactly what is sent.
- **Reject** closes the deal as lost, stops its follow-ups, and makes sure NEXUS never proposes that same action again (rule R-REV-01).
- **Mark resolved** records that you dealt with it outside NEXUS.

Add a note to any decision. Notes are kept in the audit log.

**Suggested prices on reply approvals.** The price is calculated in code, never by the AI:

- *landed cost per unit* = (supplier cost + shipping + duties + transaction costs) ÷ quantity, using the higher end of any ranges
- *floor* = landed cost ÷ (1 − your minimum margin)
- *suggested price* = the floor, raised to at least the bottom of your price book and capped at its top. If the floor is above the price book, the floor is used and the card warns you what margin the price book would give.

"Costs still unknown" on the card means some cost (often duties) was not available. Treat the price as optimistic until you have checked it.

---

## 11. Supplier offers and the price book

In production these two lists are the **only** source of costs and selling prices. NEXUS will never estimate or invent them.

### Supplier offers (what you can buy)

One row per offer, from a real quote, price list or supplier e-mail.

| Column | Required | Example | Notes |
|---|---|---|---|
| supplier_name | yes | Dubai IT Liquidators FZE | Suppliers are created automatically |
| supplier_country | | UAE | |
| product_category | yes | refurbished_laptop | One of the five category codes |
| product_name | yes | Dell Latitude 5420 i5/16GB/256GB | Specific, as quoted |
| condition | | grade A refurbished | |
| quantity_available | | 200 | Blank = unknown (treated as available) |
| moq | | 20 | Minimum order quantity |
| unit_cost_low_usd | yes | 195 | Convert to USD at today's rate |
| unit_cost_high_usd | | 205 | Blank = same as low |
| incoterm | | FOB Dubai | |
| shipping_cost_usd | | 900 | Per shipment. Blank = unknown and flagged |
| lead_time_days | | 10 | |
| payment_terms | | T/T in advance | |
| documents | | commercial invoice\|certificate of origin | Separate with `\|`. Used for the regulated-document check |
| warranty | | 6 months | |
| valid_until | | 2026-12-31 | YYYY-MM-DD. Expired offers are ignored |
| source | **yes** | quote DIL-2026-114 (email 2026-09-20) | Where this came from |
| reliability_score | | 0.8 | 0–1, your judgement of the supplier |
| notes | | | |

### Selling prices (what buyers pay)

| Column | Required | Example | Notes |
|---|---|---|---|
| product_category | yes | refurbished_laptop | |
| product_name | | Dell Latitude 5420 | Blank = the whole category |
| condition | | grade A | |
| country | | Kenya | Blank = all countries. A country-specific price wins over a general one |
| unit_price_low_usd | yes | 310 | |
| unit_price_high_usd | | 350 | |
| basis | **yes** | three Nairobi reseller listings | How you arrived at it |
| source | **yes** | field survey 2026-09-18 | Where it came from |
| valid_until | | 2026-12-31 | |

**Import rules:** a file is imported all-or-nothing. If any row has a problem, nothing is imported and every problem is listed with its row number, so you can fix the file and try again. Retire outdated entries with the **Retire** button.

---

## 12. Licences and regulated products

Medical equipment and pharmaceuticals are "regulated categories". For them NEXUS applies extra rules.

**Your licence register decides where NEXUS may approach buyers:**

- A buyer in your licence's **issuing country** (Kenya) is a domestic deal. Outreach goes out normally, and any commitment still comes to you.
- A buyer elsewhere in your **declared regional scope** (EAC, COMESA) is a cross-border deal. Outreach is allowed. Any commitment is held with a reminder to confirm that the buyer holds import authorisation in their country and that the product is registered with that country's regulator (for example TMDA in Tanzania, NDA in Uganda, Rwanda FDA). **A Kenyan licence does not authorise import into another country**, and NEXUS will never claim otherwise.
- A buyer **outside** your scope (for example Ghana or Nigeria) is not contacted without your approval (R-REG-04), and a commitment there is blocked (R-REG-05).
- An **expired** licence covers nothing. You get alerts 45 days before expiry and again at expiry.

Registering a licence issued in another country (for example through a partner) makes deals there domestic for that licence.

**E-mails** may say "licensed importer and distributor in Kenya" only when a current licence covers the deal. The fact check blocks any licence, certification or regulatory claim ("CE marked", "FDA approved", "authorised distributor"…) that is not backed by your data, and it blocks medical claims such as "cures" or "no side effects".

**Supplier documents:** before a regulated line is offered to a buyer, the matched supplier offer must list the documents in `required_documents` (by default `export licence` and `batch certificates` for pharmaceuticals, and `CE documentation on file` for medical equipment). Otherwise the deal goes to review as a compliance item. Adjust the list on the Settings tab to match your actual process.

**Controlled substances** are never handled autonomously.

---

## 13. Commercial settings

Edited on the Settings tab or with `nexus settings set KEY 'JSON'`.

| Setting | Default | Meaning |
|---|---|---|
| `min_margin_pct` | 12 for every category | Deals whose best-case gross margin is below this are dropped |
| `typical_order_qty` | laptops 20, iPhones 30, medical 2, pharma 1000, servers 4 | Used only when the buyer gave no quantity; labelled as an assumption |
| `duties_taxes_pct` | empty | Country → fraction, e.g. `{"Kenya": 0.16, "Uganda": 0.18}`. Empty means unknown |
| `transaction_cost_pct` | 0.03 | Bank, FX and payment costs as a fraction of revenue |
| `required_documents` | see section 12 | Category → list of document names |
| `target_markets` | East African countries per category; iPhones also Romania, Bulgaria, Serbia, Moldova | Category → countries research may consider |
| `directories` | buyer: UNGM, tenders.go.ke; supplier: per-category B2B directories | Sites searched with `site:` queries in addition to the open web |
| `supplier_regions` | e.g. laptops: UAE, US, UK, Germany, China, Kenya; pharma: India, China, Kenya, Egypt | Where supplier research looks |
| `rfq_products` | A description per category | What RFQs ask suppliers to quote for |
| `rfq_destination` | Nairobi, Kenya (CIF Mombasa or DAP Nairobi) | Delivery point in RFQs |
| `supplier_min_score` | 0.5 | Minimum score before a supplier receives an RFQ |

Examples:

```bash
nexus settings set min_margin_pct '{"pharmaceutical": 15, "medical_equipment": 18}'
nexus settings set duties_taxes_pct '{"Kenya": 0.16, "Uganda": 0.18, "Tanzania": 0.18}'
nexus settings set target_markets '{"used_iphone": ["Kenya", "Uganda", "Romania"]}'
```

Remove a duty rate by setting it to `null`: `'{"Uganda": null}'`.

---

## 14. How NEXUS finds buyers and contacts

In production every fact about a buyer comes from something NEXUS retrieved and stored:

1. **Search** — queries such as "Kenya hospital tender medical equipment" run through your search provider, plus searches restricted to public directories and tender portals (`directories` → `buyer` in settings, e.g. UNGM and tenders.go.ke). With Serper, NEXUS also reads Google Maps business listings (for example "hospital in Kenya"); a listed organisation with its own website is taken as a prospect directly. Every result is stored as a source document.
2. **Proposals** — the AI reads the results and proposes organisations. Each proposal must cite the result it came from.
3. **Checks in code** — a proposal is accepted only if the organisation's name actually appears in the cited result. Quoted buying signals must appear word-for-word. Rejected proposals are counted in the task output and never "corrected".
4. **Website** — the organisation's own site is found (directories and social networks such as LinkedIn are not accepted as the website), and its home and about pages are read.
5. **Contact** — NEXUS reads contact, procurement and about pages on the organisation's own site. An e-mail address is used only if it is published there. A person's name is used only if it appears on that page. Otherwise NEXUS uses a published role mailbox (procurement@, tenders@, info@ and so on), addressed to the "Procurement office".
6. **Naming the person behind a mailbox.** When only a role mailbox is published, NEXUS runs one search for public LinkedIn profiles of that organisation's procurement, purchasing or supply-chain staff. If a result names the organisation and has a relevant job title, the e-mail to the published mailbox is addressed to that person by name, and the profile link appears on the Pipeline tab so you can connect with them yourself.
7. **No published address, no contact.** The opportunity is closed as stale rather than guessing an address.

**Why NEXUS does not scrape LinkedIn or send LinkedIn messages:** LinkedIn's terms forbid automated scraping and messaging, accounts that do it are restricted or banned, and LinkedIn has won in court on this. NEXUS only reads the snippets that public search engines already show, and never logs in to or visits LinkedIn itself. Connecting on LinkedIn is left to you.

The fetcher respects `robots.txt`, identifies itself (`FETCH_USER_AGENT`), waits a few seconds between requests to the same site, and refuses to fetch private or internal network addresses.

### Finding and engaging suppliers

NEXUS works the supply side as actively as the buy side, with the same rule: only what it can see in a source counts.

1. **Supplier research** runs weekly for every product line in an active objective, immediately when a deal is parked because no supplier offer exists, and whenever you press **Search** on the Suppliers tab. It searches the open web for each supplier region in `supplier_regions` (for example UAE, US, UK and China for laptops; India and China for pharmaceuticals) and the supplier directories in `directories` → `supplier` (Made-in-China, IndiaMART, Europages, Global Sources, DOTmed…). An organisation is accepted only if it is named in a retrieved result.
2. **Supplier check.** NEXUS reads the supplier's own website: products, claimed certifications, export evidence, business address and a published contact address. It scores the supplier from 0 to 1 and flags red flags: no website of its own, only a free webmail address, no evidence it sells the line. Claimed certifications (ISO 13485, WHO-GMP, R2 and so on) are shown as **unverified**. They are what the supplier says, not proof.
3. **Request for quotation.** Suppliers scoring at least `supplier_min_score` (default 0.5) get an RFQ by e-mail. It describes what you buy (`rfq_products`), typical order quantities and buyer countries taken from your live pipeline, and the delivery point (`rfq_destination`). It asks for unit price, MOQ, availability, grading, warranty, lead time, payment terms, Incoterms, validity and, for regulated lines, the required documents. It states your licence where one covers the line, says clearly that it is a request for prices and not an order, and carries the same sender footer and opt-out as all NEXUS e-mail. Up to `SUPPLIER_RFQ_MAX_FOLLOWUPS` follow-ups are sent `SUPPLIER_RFQ_FOLLOWUP_DAYS` apart, threaded; after that the supplier is marked unresponsive. At most `SUPPLIER_RFQ_DAILY_LIMIT` RFQs go out per day, and approval mode holds them for you like buyer e-mails.
4. **Reading the reply.** A quote is read line by line into **draft** supplier offers. Every price, MOQ, quantity and lead time must literally appear in the supplier's e-mail, or it is left out. The draft appears in Reviews as a *supplier quote* next to the original e-mail. Check it, then press **Activate in catalogue**, and only then is it used for pricing. Quotes in another currency must be converted and entered in the Catalogue tab. Questions from suppliers come to you as *supplier reply* items. A supplier that says it cannot supply is marked declined.
5. **Your controls.** On the Suppliers tab you can **Block** a supplier (never contacted again) or **Qualify** one NEXUS was unsure about (it is sent an RFQ on the next pass), and filter by status: lead, qualified, rfq_sent, quoted, approved, declined, unresponsive, rejected or blocked.

Nothing on the supply side commits you to buying: NEXUS never places orders, accepts terms or pays.

Attachments (PDF or Excel price lists) are not read automatically yet. The review item tells you a reply arrived; open the attachment in your mailbox and enter the lines in the Catalogue tab.

---

## 15. E-mail: sending, replies, bounces and opt-outs

**Every e-mail** is personalised, checked by the fact check, and ends with a footer added by code (not by the AI): your business name, your postal address, and an opt-out line with a one-click unsubscribe link. The message also carries the standard `List-Unsubscribe` headers that Gmail and Outlook use for their unsubscribe button. Follow-ups are threaded to the first e-mail.

**Reading the mailbox** (every 15 minutes, needs IMAP):

- **Replies** are matched to the e-mail they answer, then to the sender's address, and classified.
- **Opt-out words** ("unsubscribe", "remove me", "stop emailing"…) suppress the contact immediately, in code, before any AI classification.
- **Bounces** mark the address as bounced so it is never used again.
- **Auto-replies** (out of office) are set aside and do not stop the sequence.
- **Mail that cannot be matched** to any contact goes to your review queue.

NEXUS marks messages it has read as read in the mailbox. You can still read and answer anything yourself in your normal mail client. If you reply to a buyer yourself, record the outcome in the Pipeline tab so NEXUS knows.

**Unsubscribe links** open a confirmation page. The person must press the button, so e-mail security scanners that open links cannot unsubscribe people by accident.

---

## 16. Budget and costs

- The monthly budget is **USD 500** by default. Set `BUDGET_MONTHLY_LIMIT_USD` to change it (higher or lower); there is no built-in ceiling beyond what you set.
- The budget is split into lines: advertising 300, AI primary 45, AI secondary 20, research 30, e-mail 25, infrastructure 20, storage 5, automation 15, testing 10, reserve 30. Change them with `BUDGET_CATEGORY_LIMITS_JSON`; keep the lines adding up to your monthly budget. If you do not run ads, move the 300 elsewhere or lower the budget.
- Every paid action reserves its estimated cost first and is refused if that would exceed its line or the month. The real cost is recorded afterwards.
- Ad spend is reported by Google and Meta and booked against the **ads** line every 6 hours. NEXUS plans daily budgets from what is left (80% of the remainder spread over the rest of the month) and pauses every campaign at 95% of the line.
- AI costs are estimated from token counts at rates set at or above list prices, so NEXUS stops early rather than late.
- When the month's budget is used up, paid work and ads stop, you get an alert, and work resumes on the 1st.
- Server, domain and mailbox are paid to those providers directly and are not tracked by NEXUS.

The Overview tab shows spent, reserved and the month-end forecast. `nexus report` prints the same from the command line.

---

## 17. Landing pages and enquiries

Every ad sends people to a **landing page**: one page per product line and country (for example *Refurbished business laptops for Kenya*). The pages are part of NEXUS; there is nothing else to host.

**What is on a page.** A headline and short benefits, the actual models from your current supplier offers (condition, warranty, minimum order), a "from" price taken from your price book, your licence statement for medical equipment (only when a licence covers that country), how quickly you reply, an enquiry form, a short FAQ, your business name and address, and a link to the privacy notice. Every figure and claim comes from your catalogue, price book or licence register. The AI writes the words; the same fact check used for e-mail, plus the advertising claim rules, decides whether they can be published. If the AI's copy fails, a plain version built from the same facts is published instead, so a page never shows a claim you cannot support.

**The enquiry form** asks for name, organisation, e-mail, phone (optional), quantity (optional) and a message, and requires the visitor to tick consent to be contacted. It has a hidden spam trap and a limit of five enquiries per visitor per hour. It also carries, invisibly, which ad the visitor came from.

**What happens to an enquiry, within minutes:**

1. It is stored as a **lead** with its source (Google, Meta or organic) and the exact ad.
2. NEXUS creates the organisation, contact and deal, and sends a short acknowledgement (no prices, no promises beyond your reply time). You get an alert.
3. The deal is matched to a supplier offer and priced like any other.
4. A quotation is drafted and waits in **Reviews** for your approval (rule R-REPLY-01, as for every reply to a buyer).
5. Google or Meta is told that this click became an enquiry, and later a sale if you record the deal as won. This is what makes the platforms' own bidding find more people like your buyers.

**WhatsApp.** Set `WHATSAPP_NUMBER` and every page gets a WhatsApp button. Clicks are counted per page. Conversations on WhatsApp are yours to handle; NEXUS does not read them.

**Your own domain for the pages.** By default pages are served at `https://<dashboard domain>/p/<page>`. For ads, a customer-facing domain looks better and converts better:

1. Buy the domain (for example `yourbrand.com`) and point `www` and the bare domain (A records) at your server.
2. Copy `deploy/site.caddy.example` to `deploy/sites/site.caddy` and replace `www.yourbrand.com, yourbrand.com` with your domain.
3. In `.env` set `PUBLIC_SITE_URL=https://www.yourbrand.com`, then run `docker compose up -d`.

Only the public pages (`/p/…`, `/privacy`, `/site`) answer on that domain; the dashboard and API do not.

**The Leads tab** lists every page with views, WhatsApp clicks, enquiries and conversion rate, and every enquiry with its source and status. **Publish / refresh page** rebuilds a page after you change offers or prices (pages are also created automatically when ads are planned).

**Privacy.** Raw IP addresses are never stored (a daily-changing pseudonymous id is used for counting). The privacy notice at `/privacy` explains what is collected and that a hashed e-mail or phone and the click id may be shared with the ad platform to measure results. Have it checked against the rules of the countries you advertise in (Kenya's Data Protection Act, the EU GDPR for Romania and Bulgaria).

---

## 18. Advertising on Google and Meta

NEXUS plans, writes, launches, measures and improves ads on **Google Search** and **Meta** (Facebook and Instagram), for every product line **except pharmaceuticals**, which are never advertised (rule R-ADS-01).

### How NEXUS decides what to run

| Decision | How it is made |
|---|---|
| Which product lines | Only lines with a current supplier offer (nothing to sell, no ad) and never pharmaceuticals |
| Which countries | Your `target_markets` per line, ranked by enquiries per dollar seen so far (all markets start equal and earn their place) |
| How many campaigns | At most `ADS_MAX_CAMPAIGNS_PER_PLATFORM` per platform, one country each, so results are clear per market |
| Budget | Each new campaign gets `ADS_DEFAULT_DAILY_BUDGET_USD`, but only while 80% of what is left in the month's ads budget, spread over the remaining days, covers it |
| What the ad says | Each ad leads with one **angle**: price, lead time, condition and warranty, local support, licensed supply (medical only) or product range. An angle is only used when your data supports it (no warranty on file, no warranty ad). Angles that bring enquiries are chosen more often |
| Google keywords | Buyer-intent phrases (wholesale, bulk, supplier, for schools, model names, "in Kenya"), phrase match; plus 30+ negative keywords that keep money away from job seekers, repair searches, downloads and one-unit shoppers |
| Meta audience | The countries you target, ages 25–65, with Meta's automatic audience finding; the B2B wording of the ad does the filtering |
| Meta images | Your product photos (Ads tab), in rotation; until you upload some, a clean generated card with the headline |

### What every ad is checked for before it can run

- **Platform limits:** Google headlines up to 30 characters (3–15 of them), descriptions up to 90 (2–4); Meta headline up to 40, description up to 30, main text up to 500, an allowed call-to-action button.
- **Facts:** every number must come from your catalogue or price book (the same fact check as e-mail).
- **Claims:** no "guaranteed", "best/lowest price", "#1", "risk-free" and similar; used or refurbished goods never described as "new" or "factory sealed"; no implied manufacturer endorsement ("Apple certified", "manufacturer warranty"); no medical claims or approvals ("FDA approved", "CE marked") for medical equipment.
- **Style:** no shouting in capitals, no repeated punctuation (both platforms reject these).

A failing ad is replaced by a plain version built from facts; if that fails too, the campaign is not proposed.

### Approving and launching

By default (`ADS_REQUIRE_LAUNCH_APPROVAL=true`) each new campaign appears in **Reviews** as *ad campaign*, showing the budget, country, every ad's text and the keywords. **Approve and launch** builds it on the platform; **Reject** discards it for good. Campaigns are created paused and switched on only once every part exists, so a half-built campaign never spends.

Set `ADS_REQUIRE_LAUNCH_APPROVAL=false` only once you trust what NEXUS proposes; spending stays inside the ads budget either way.

### After launch (automatic)

Every 6 hours NEXUS pulls spend, impressions and clicks, converts spend to USD, and books it against the **ads** line of the budget. Once a day, for campaigns older than three days:

| Situation | What NEXUS does |
|---|---|
| The ads line reaches 95% of its limit (or the month's budget is used up) | Pauses every campaign and alerts you; resumes them automatically next month or when you raise the limit |
| A campaign has spent the larger of USD 30 or 3× `ADS_TARGET_COST_PER_LEAD_USD` with no enquiry | Pauses it and alerts you (check the page, the offer and the price, then resume it from the Ads tab) |
| An ad has 150+ clicks and less than a 5% chance of being the best in its campaign | Pauses it and writes a replacement with an angle not yet tried there (at least two ads always keep running) |
| Enquiries cost more than 3× your target | Cuts that campaign's daily budget by 30% |
| Across campaigns | Moves daily budget toward the campaigns bringing enquiries (and wins), at most ±30% per campaign per day and never below `ADS_MIN_DAILY_BUDGET_USD` |
| Google search terms that are clearly irrelevant ("free", "jobs", "repair"…, or 20+ clicks sharing no word with your keywords) | Adds them as negative keywords |

Every change is in the audit log with the numbers behind it. From the **Ads** tab you can pause, resume, change a budget or remove any campaign at any time; NEXUS respects what you set.

### Setting up Google Ads (about an hour, plus Google's review time)

1. **Ad account.** At ads.google.com create an account in *expert mode* (skip the guided campaign). Choose **USD** as currency if you can (it cannot be changed later) and your time zone. Add billing.
2. **Manager account.** Create a Google Ads **manager account** (free) and link your ad account to it. The API is requested from the manager account.
3. **Developer token.** In the manager account: *Admin → API Center*. Accept the terms. You get a token with **test access**, which only works with test accounts. Apply for **Basic access** from the same page (describe NEXUS as an internal tool that creates and manages search campaigns for your own business). Approval usually takes a few working days. NEXUS cannot manage your real account until it is approved.
4. **Google Cloud credentials.** At console.cloud.google.com create a project, enable the **Google Ads API**, configure the OAuth consent screen (type *External*, add your own Google address as a test user), then *Credentials → Create credentials → OAuth client ID → Desktop app*. Note the client ID and client secret.
5. **Refresh token.** On your own computer, in the NEXUS folder, run
   `nexus google-ads-token --client-id <ID> --client-secret <SECRET>`
   A browser opens; sign in with the Google account that has access to the ad account. The command prints a `GOOGLE_ADS_REFRESH_TOKEN=` line.
6. **Conversion actions** (so Google learns from enquiries). *Goals → Conversions → New conversion action → Import → Other data sources or CRMs → Track conversions from clicks.* Create one called "Enquiry" (and optionally one called "Won deal"). Open each and copy the number after `ctId=` in the browser address bar. Keep **auto-tagging** on (*Admin → Account settings*), which it is by default.
7. **Put it in `.env` on the server:** `GOOGLE_ADS_DEVELOPER_TOKEN`, `GOOGLE_ADS_CLIENT_ID`, `GOOGLE_ADS_CLIENT_SECRET`, `GOOGLE_ADS_REFRESH_TOKEN`, `GOOGLE_ADS_CUSTOMER_ID` (the ad account number), `GOOGLE_ADS_LOGIN_CUSTOMER_ID` (the manager account number), `GOOGLE_ADS_CURRENCY`, `GOOGLE_ADS_CONVERSION_ACTION_LEAD`, `GOOGLE_ADS_CONVERSION_ACTION_WON`.

### Setting up Meta (about an hour, plus business verification if asked)

1. **Business portfolio.** At business.facebook.com create a business portfolio for your company. Create or add your **Facebook Page**, and create an **ad account** (currency **USD** if you can; time zone; add a payment method).
2. **Verify your domain** (*Business settings → Brand safety → Domains*) using the domain your landing pages are on. Meta may also ask you to verify the business itself; have your registration documents ready.
3. **App.** At developers.facebook.com create an app of type *Business*, connected to your business portfolio, and add the **Marketing API** product.
4. **System user and token.** *Business settings → Users → System users → Add* (role *Admin*). *Assign assets*: your ad account (full control) and your Page. *Generate new token* for your app with the permissions `ads_management`, `ads_read`, `business_management`, `pages_read_engagement` and `pages_manage_ads`, and choose a token that does not expire.
5. **Dataset (pixel) for results.** *Events Manager → Connect data sources → Web*; name it and note its ID. NEXUS sends "Lead" and "Purchase" events to it from the server (Conversions API), so no code on the page is needed.
6. **Put it in `.env` on the server:** `META_ACCESS_TOKEN`, `META_AD_ACCOUNT_ID` (digits only), `META_PAGE_ID`, `META_PIXEL_ID`, `META_AD_ACCOUNT_CURRENCY`.

For ads shown in the EU (Romania, Bulgaria and other member states), the EU Digital Services Act requires naming who benefits from and who pays for the ad; NEXUS fills both with `BUSINESS_NAME`.

### Switching ads on

1. Make sure at least one product line has a current supplier offer and a selling price, and that `PUBLIC_SITE_URL` (or `PUBLIC_BASE_URL`) is set.
2. If an ad account is not in USD, set `ADS_FX_RATES_JSON`, for example `{"KES": 129.5}` (units of that currency per 1 USD). Update it when rates move materially.
3. Set `ADS_ENABLED=true` and apply with `docker compose up -d`.
4. Run `docker compose exec api nexus doctor --live`: it shows each ad account's name, currency and status, or the exact error.
5. Upload a few real product photos in **Ads → Product photos** (strongly recommended for Meta).
6. **Ads → Plan campaigns** (or wait for the weekly planning run). Approve the proposals in **Reviews**.

### Getting ads to convert: what matters most

- **Price and offer.** Ads and pages can only say what your catalogue and price book say. A competitive "from" price and a clear warranty are the strongest angles NEXUS can use; keep them current.
- **Answer fast.** Enquiries go cold within hours. Approve drafted quotations in Reviews the same day, and set `LEAD_RESPONSE_PROMISE` to something you can keep.
- **Real photos** of the stock on Meta.
- **WhatsApp** (`WHATSAPP_NUMBER`): many buyers in the region prefer it to a form.
- **Record outcomes.** Mark deals won or lost in Pipeline. Won deals are reported to the platforms and weigh double when budget is moved.
- **Give it time.** Each campaign needs a week or two and a few hundred clicks before the numbers mean much; NEXUS does not judge an ad before 150 clicks.
- **Medical equipment** ads are allowed on both platforms for business buyers, but each platform may restrict or reject some device types in some countries. A rejected ad shows no impressions in NEXUS; check the platform's own interface for the reason.

### Honest limits

- NEXUS reads spend and clicks, not the platforms' ad-review status. If an ad is disapproved it simply shows no impressions; the platform e-mails you the reason.
- Budgets are converted at the rate you set; a stale rate misstates spend in USD.
- Google keywords are English. Pages and ads are in English.
- Meta's own lead forms and Instagram direct messages are not used; everything goes to your landing page.

---

## 19. How NEXUS learns and improves

NEXUS improves itself from real outcomes, not from the AI's opinion. Every choice it makes repeatedly is a small experiment that it keeps score of:

| What it learns | From which results | Where it is used |
|---|---|---|
| E-mail angle (price, availability, condition, licensed supply) and subject-line style | Positive replies (interested, asking for price or information, RFQ, negotiation) to first e-mails | Every new first e-mail |
| Which segments to contact first | Positive-reply rate per product line and country | Order of outreach |
| Market attractiveness | Research score, blended with actual reply rates once a market has 20+ first e-mails (the evidence's weight grows with volume, up to 70%) | Opportunity scoring and market ranking |
| Opportunity scoring weights | Which contacted deals engaged and which went silent | Scoring every new opportunity |
| Supplier regions | Share of suppliers found in each region that sent a quote | Where supplier research searches first |
| Ad angles, markets and budgets | Enquiries per click and per dollar, and won deals | Ad planning and optimisation (section 18) |

**How choices are made.** NEXUS uses *Thompson sampling*: for each option it draws a plausible success rate from what it has seen and picks the highest draw. Options with good results win most of the time; options with little data still get tried, so a good idea is never starved and a bad one fades out on its own. Nothing needs tuning.

**Scoring weights: versioned and reversible.** Once a week NEXUS refits the scoring weights to the deals it has contacted (it needs at least 30, with 5+ that engaged and 5+ that did not). The new weights are used only if they rank deals better in cross-validation than the current ones; each weight moves at most 0.10 per week. After activation, NEXUS keeps comparing the new weights with the ones they replaced on the deals that arrive afterwards, and **rolls back automatically** if the new ones do worse. You can also roll back yourself.

**The Learning tab** shows, per product line, each option's results, its likely range and its chance of being the best; the scoring weights in use and their history; the last refit decision; and a short plain-language summary. **Recalculate now** refreshes it; **Roll back scoring weights** restores the previous version.

**What helps it learn:** record every won and lost deal, let the system send enough first e-mails per segment (tens, not handfuls), and avoid changing everything at once.

---

## 20. Alerts on your phone

**Telegram (recommended):**

1. In Telegram, message **@BotFather**, send `/newbot`, and follow the prompts. Copy the token it gives you.
2. Start a chat with your new bot and send it any message.
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and find `"chat":{"id": …}`. That number is your chat ID.
4. In `.env`:

   ```ini
   NOTIFY_CHANNELS=log,telegram
   TELEGRAM_BOT_TOKEN=123456:ABC...
   TELEGRAM_CHAT_ID=987654321
   ```

5. `docker compose up -d`, then `docker compose exec api nexus notify-test`.

**E-mail:** set `NOTIFY_CHANNELS=log,email` and `OPERATOR_EMAIL` to your personal address. Alerts are sent through the SMTP mailbox.

You receive: new review items (batched every 15 minutes), a daily summary, budget stops and licence expiry warnings. At most `NOTIFY_MAX_PER_HOUR` alerts are sent per hour.

---

## 21. Command reference

On the server, run commands inside the API container:

```bash
docker compose exec api nexus <command>
```

Locally with Python, use `python -m app.cli <command>`.

| Command | What it does |
|---|---|
| `nexus doctor` | Shows production readiness (missing settings and warnings) and checks the database |
| `nexus doctor --live` | Also logs in to search, SMTP, IMAP, the AI model and the Google/Meta ad accounts |
| `nexus google-ads-token --client-id … --client-secret …` | One-time, on your own computer: gets `GOOGLE_ADS_REFRESH_TOKEN` through your browser |
| `nexus simulate --days 10` | Runs the simulated world end to end in a throwaway database (safe on a live server) |
| `nexus run --objective "Title" --categories medical_equipment` | Creates an objective and runs one pass |
| `nexus report --window 30` | Metrics and budget |
| `nexus review list` | Pending review items |
| `nexus review decide REV_ID approve\|reject\|resolve --note "…"` | Decides an item (the draft is sent as is; edit drafts in the dashboard) |
| `nexus outcome OPP_ID won --revenue 12000 --margin 2100 --note "PO 55"` | Records a result |
| `nexus catalogue template offers > offers.csv` | Prints a blank CSV template (`offers` or `prices`) |
| `nexus catalogue import offers offers.csv` | Imports a CSV (all rows or none) |
| `nexus catalogue list offers --category pharmaceutical` | Lists offers or prices |
| `nexus catalogue deactivate offers OFF_ID` | Retires an entry |
| `nexus settings show` / `nexus settings set KEY 'JSON'` | Commercial settings |
| `nexus license add --holder … --country Kenya --authority … --number … --types importer distributor --categories medical_equipment pharmaceutical --regions EAC COMESA --valid-from 2026-01-01 --expires 2027-12-31` | Registers a licence |
| `nexus license list [--all]` / `nexus license deactivate LIC_ID` | Licences |
| `nexus research "Kenya hospital tender" --country Kenya` | Runs one live search (tests your search key) |
| `nexus notify-test` | Sends a test alert |

To copy a CSV into the container: `docker compose cp offers.csv api:/tmp/offers.csv`, then `nexus catalogue import offers /tmp/offers.csv`. The dashboard's Catalogue tab is usually easier.

Worker: `python scripts/worker.py --interval 300` runs continuously; `--once` runs one cycle; `--check-heartbeat` is used by the Docker health check.

---

## 22. API reference

All endpoints except `/health`, `/u/…` and the public site (`/p/…`, `/privacy`, `/site`) require the dashboard login (HTTP Basic).

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness, mode, production readiness (public) |
| GET/POST | `/u/{token}` | Unsubscribe page / confirm (public) |
| GET | `/api/state` | Everything the Overview shows |
| GET | `/api/readiness` | Missing settings and warnings |
| POST | `/api/objectives` | `{title, product_categories, description}` |
| POST | `/api/objectives/{id}/status` | `{status: active\|paused\|completed\|abandoned}` |
| POST | `/api/run` | Runs one pass plus due jobs |
| POST | `/api/control` | `{paused, emergency_stop, paused_categories, paused_geographies}` |
| GET | `/api/reviews?status=pending` | Review items (`pending`, `approved`, `rejected`, `resolved`, `all`) |
| POST | `/api/reviews/{id}/decision` | `{decision: approve\|reject\|resolve, note, subject?, body?}` |
| GET | `/api/opportunities?stage=` | Opportunities with contact and economics |
| POST | `/api/opportunities/{id}/outcome` | `{result: won\|lost, revenue_usd?, margin_usd?, note}` |
| GET/POST | `/api/catalogue/offers` | List / add a supplier offer |
| POST | `/api/catalogue/offers/{id}/deactivate` | Retire an offer |
| GET/POST | `/api/catalogue/prices` | List / add a selling price |
| POST | `/api/catalogue/prices/{id}/deactivate` | Retire a price |
| POST | `/api/catalogue/import?kind=offers\|prices` | CSV body; all or nothing |
| GET | `/api/catalogue/template/{kind}` | CSV header |
| GET/PUT | `/api/settings/commercial` | Read / update commercial settings |
| GET | `/api/suppliers?status=` | Supplier leads with score, red flags, contact and RFQ status |
| POST | `/api/suppliers/{id}/status` | `{status: blocked\|qualified\|lead\|approved}` |
| POST | `/api/suppliers/research` | `{product_category, regions?}`: search for suppliers now |
| GET/POST | `/api/licenses` | List / register licences |
| POST | `/api/licenses/{id}/deactivate` | Stop relying on a licence |
| GET | `/p/{slug}` , `/p/{slug}/wa`, `/privacy`, `/site` | Public landing page, WhatsApp redirect, privacy notice, index (public) |
| POST | `/p/{slug}/enquiry` | Enquiry form (public, form-encoded) |
| GET/POST | `/api/pages` | Pages with views, WhatsApp clicks, enquiries / `{product_category, country}` to publish or refresh |
| GET | `/api/leads` | Enquiries with source and status |
| GET | `/api/ads/campaigns` | Campaigns with spend, clicks, enquiries, cost per enquiry and every ad |
| POST | `/api/ads/plan` | `{platform?, product_category?, country?}`: plan campaigns now |
| POST | `/api/ads/campaigns/{id}` | `{action: pause\|resume\|budget\|remove, daily_budget_usd?}` |
| GET/POST | `/api/ads/assets?category=&caption=` | Product photos (POST the image as the request body) |
| POST | `/api/ads/assets/{id}/deactivate` | Stop using a photo |
| GET | `/api/learning?refresh=` | What NEXUS has learned (see section 19) |
| POST | `/api/learning/rollback` | Restore the previous scoring weights |

In development (`NEXUS_ENV=development`), interactive API documentation is at `/docs`. It is switched off in production because it would bypass the login.

---

## 23. Configuration reference

All settings live in `.env` on the server. After changing it, run `docker compose up -d` to apply.

**Mode and server**

| Setting | Default | Meaning |
|---|---|---|
| `NEXUS_ENV` | development | `production` requires a dashboard login |
| `NEXUS_MODE` | simulation | `production` = real research and e-mail |
| `WORKER_INTERVAL_SECONDS` | 300 | Time between worker cycles |
| `NEXUS_DOMAIN` | — | Dashboard host name (HTTPS certificate) |
| `PUBLIC_BASE_URL` | — | `https://` + domain; used in unsubscribe links |
| `DATABASE_URL` | set by compose | Only set yourself when running without Docker |
| `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD` | — | Dashboard and API login |

**AI model**

| Setting | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Primary provider |
| `MODEL_PRIMARY` | claude-sonnet-5-5 | Reasoning tasks (market research, strategy, reply drafts) |
| `MODEL_BULK` / `MODEL_CRITIC` | claude-haiku-4-5-20251001 | Extraction, classification, outreach copy |
| `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL_*` | — | Optional OpenAI-compatible provider (also serves as a backup) |

**Research**

| Setting | Default | Meaning |
|---|---|---|
| `SEARCH_PROVIDER` | none | `serper` (recommended; enables business listings), `brave` or `tavily` |
| `SEARCH_API_KEY` | — | |
| `SEARCH_COST_PER_QUERY_USD` | 0.005 | Your plan's price per query |
| `RESEARCH_MAX_QUERIES_PER_TASK` | 3 | Queries per country per prospecting task |
| `RESEARCH_MAX_PAGES_PER_TASK` | 4 | Pages read per organisation when finding a contact |
| `FETCH_USER_AGENT` | NEXUS-research/0.2 | How the fetcher identifies itself |

**Sender identity and e-mail**

| Setting | Default | Meaning |
|---|---|---|
| `BUSINESS_NAME`, `BUSINESS_POSTAL_ADDRESS` | — | Printed in every footer (required) |
| `EMAIL_SENDER_NAME`, `EMAIL_SENDER_ADDRESS`, `EMAIL_REPLY_TO` | — | From and Reply-To |
| `EMAIL_PROVIDER` | simulated | `smtp` in production |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY`, `SMTP_USERNAME`, `SMTP_PASSWORD` | —, 587, starttls | Sending |
| `IMAP_HOST`, `IMAP_PORT`, `IMAP_USERNAME`, `IMAP_PASSWORD`, `IMAP_FOLDER` | —, 993, INBOX | Reading replies |
| `UNSUBSCRIBE_SECRET` | generated | Signs unsubscribe links. Changing it invalidates old links |
| `OUTREACH_DAILY_LIMIT` | 10 in the example | Total outreach e-mails per 24 hours |
| `OUTREACH_PER_COMPANY_DAY_LIMIT` | 1 | Per contact per 24 hours |
| `OUTREACH_MAX_FOLLOWUPS` | 2 in the example | Follow-ups after the first e-mail |
| `SUPPLIER_RFQ_DAILY_LIMIT` | 10 | RFQs to suppliers per 24 hours |
| `SUPPLIER_RFQ_MAX_FOLLOWUPS` | 2 | Follow-ups after an unanswered RFQ |
| `SUPPLIER_RFQ_FOLLOWUP_DAYS` | 5 | Days between RFQ follow-ups |

**Alerts, budget and safety**

| Setting | Default | Meaning |
|---|---|---|
| `NOTIFY_CHANNELS` | log | `log`, `telegram`, `email` (comma separated) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `OPERATOR_EMAIL` | — | Alert destinations |
| `NOTIFY_MAX_PER_HOUR` | 10 | Alert cap |
| `BUDGET_MONTHLY_LIMIT_USD` | 500 | Monthly budget; any positive amount |
| `BUDGET_CATEGORY_LIMITS_JSON` | planning split | Per-line limits |
| `REQUIRE_OUTREACH_APPROVAL` | false | `true` makes every buyer outreach e-mail, follow-up and supplier RFQ wait for your approval |
| `REQUIRE_HUMAN_APPROVAL_ABOVE_USD` | 0 | Every financial commitment goes to you |
| `ALLOW_REGULATED_AUTONOMOUS_TRANSACTIONS` | false | Leave false |

**Public site and advertising**

| Setting | Default | Meaning |
|---|---|---|
| `PUBLIC_SITE_URL` | `PUBLIC_BASE_URL` | Address the landing pages are served from (section 17) |
| `WHATSAPP_NUMBER` | — | Digits with country code; adds a WhatsApp button to pages |
| `LEAD_RESPONSE_PROMISE` | within one business day | Shown on pages and in acknowledgements; keep it realistic |
| `ADS_ENABLED` | false | Master switch for advertising in production |
| `ADS_REQUIRE_LAUNCH_APPROVAL` | true | New campaigns wait in Reviews |
| `ADS_DEFAULT_DAILY_BUDGET_USD` | 5 | Starting daily budget per campaign |
| `ADS_MIN_DAILY_BUDGET_USD` | 1 | Budget moves never go below this |
| `ADS_TARGET_COST_PER_LEAD_USD` | 15 | What an enquiry is worth; drives pausing, cuts and the market prior |
| `ADS_MAX_CAMPAIGNS_PER_PLATFORM` | 6 | Upper limit of live campaigns per platform |
| `ADS_PRUNE_MIN_CLICKS` | 150 | Clicks an ad needs before it can be judged |
| `ADS_FX_RATES_JSON` | — | e.g. `{"KES": 129.5}` when an ad account is not in USD |
| `GOOGLE_ADS_*` | — | Developer token, OAuth client ID/secret, refresh token, customer ID, manager (login) customer ID, currency, API version (v25), conversion action IDs |
| `META_*` | — | Access token, ad account ID, Page ID, pixel/dataset ID, account currency, API version (v26.0) |

---

## 24. Policy rules reference

These codes appear in the review queue, the System tab and the audit log. **Block** means the action is refused; **escalate** means it waits for you.

| Rule | Effect | When |
|---|---|---|
| R-SYS-01…04 | block | Emergency stop, global pause, paused category, paused country |
| R-SEC-01 | block | Something that looks like a password or API key was about to leave the system |
| R-DES-01 | block | Any destructive action |
| R-FIN-01 | escalate | A financial commitment above `REQUIRE_HUMAN_APPROVAL_ABOVE_USD` |
| R-LEG-01 | escalate | Any legal commitment |
| R-REV-01 | block | You rejected this exact action before |
| R-REV-02 | allow | Your approval waived an escalation (shown for the record) |
| R-REPLY-01 | escalate | Every reply to a buyer |
| R-APPR-01 | escalate | Approval mode is on |
| R-REG-01 | escalate | A regulated transaction |
| R-REG-02 | escalate | Regulatory position not verified before contacting a medical/pharma buyer |
| R-REG-03 | escalate | A controlled or restricted product |
| R-REG-04 | escalate | Medical/pharma contact outside your licence scope |
| R-REG-05 | block | Medical/pharma commitment outside your licence scope |
| R-REG-06 | escalate | Cross-border medical/pharma commitment: destination import checks |
| R-OUT-00…04 | block | Contact missing, opted out, bounced, no address, or company opted out |
| R-DUP-01 | block | The same message already exists |
| R-RATE-01 / 02 | block | Daily limit, or per-contact frequency |
| R-RATE-03 | block | Daily supplier RFQ limit |
| R-FUP-01 / 02 | block | Follow-up cap reached, or sequence already stopped |
| R-FUP-03 | block | Supplier follow-up cap reached |
| R-FACT-01 | block | Unsupported figure or claim in the message |
| R-FACT-02 | block | Message not personalised |
| R-BUD-01 | block | Budget line or month exhausted |
| R-ADS-00 | block | Ad launch while `ADS_ENABLED=false` (production) |
| R-ADS-01 | block | Ad for a product line that is never advertised (pharmaceuticals) |
| R-ADS-02 | block | Ad copy failed the checks at launch time (facts changed since planning) |
| R-ADS-03 | escalate | New ad campaign (while `ADS_REQUIRE_LAUNCH_APPROVAL=true`) |

---

## 25. Backups, updates and recovery

**Update to a new version:**

```bash
cd /opt/nexus
scripts/backup.sh                 # always back up first
git pull
docker compose up -d --build      # migrations run automatically before the API starts
```

**Restore a backup:**

```bash
scripts/restore.sh backups/nexus-20261001T021500Z.sql.gz
```

The script asks you to type `RESTORE`, stops the API and worker, restores, re-applies migrations and starts them again.

**After a crash or reboot** there is nothing to do. The containers restart automatically, tasks that were running are put back in the queue, and scheduled jobs pick up where they left off without running twice.

**Rotating secrets:** change the value in `.env`, then run `docker compose up -d`. Changing `POSTGRES_PASSWORD` after the database exists also needs the password changed inside PostgreSQL: `docker compose exec db psql -U nexus -c "ALTER USER nexus PASSWORD 'new'"`. Changing `UNSUBSCRIBE_SECRET` invalidates links in e-mails already sent, although reply-to-unsubscribe keeps working.

**Logs:** `docker compose logs -f worker` (or `api`). Logs are structured JSON and never contain API keys or passwords.

---

## 26. Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Dashboard shows "Production is refusing to run until these are set" | Add the listed settings to `.env`, then `docker compose up -d`. `nexus doctor` shows the same list |
| Browser shows a certificate error | DNS for `NEXUS_DOMAIN` does not point at the server yet, or ports 80/443 are blocked. Check with `docker compose logs caddy` |
| `401 login required` | Wrong username or password; see `grep DASHBOARD .env` |
| `503 set DASHBOARD_USERNAME and DASHBOARD_PASSWORD` | Production requires a login; run `generate_secrets.py` or set them |
| `doctor --live` says `smtp_login FAILED` | Wrong host, port or security combination, or you need an app password. Port 587 uses `starttls`; 465 uses `ssl` |
| No replies are processed | IMAP not set (see readiness warnings), or IMAP login failing (`doctor --live`) |
| Many deals sit in "commercial review" | Look at the Reviews tab. Usually catalogue gaps (add offers and prices), licence scope, or approval mode |
| No new prospects | Check `SEARCH_PROVIDER`/`SEARCH_API_KEY` with `nexus research "…"`; check `target_markets`; look for failed tasks on the System tab |
| "no e-mail address published" on many opportunities | Normal for organisations without a website contact page. NEXUS will not guess addresses |
| Everything stopped mid-month | Budget ceiling reached (Overview shows "Hard stop engaged"). Work resumes on the 1st |
| Worker shows `unhealthy` | `docker compose logs worker`. It must finish a cycle within three intervals plus five minutes |
| An approved e-mail was not sent | Open the item. Something may have changed since you approved it (an opt-out, a duplicate, the budget), and the reason is shown |

For anything else, `docker compose logs --tail=200 api worker` shows what happened.
