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
15. [E-mail: sending, replies, bounces and opt-outs](#15-e-mail-sending-replies-bounces-and-opt-outs)
16. [Budget and costs](#16-budget-and-costs)
17. [Alerts on your phone](#17-alerts-on-your-phone)
18. [Command reference](#18-command-reference)
19. [API reference](#19-api-reference)
20. [Configuration reference](#20-configuration-reference)
21. [Policy rules reference](#21-policy-rules-reference)
22. [Backups, updates and recovery](#22-backups-updates-and-recovery)
23. [Troubleshooting](#23-troubleshooting)

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
- learns from the outcomes you record, and
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
| Spend more than USD 200 in a month | A hard ceiling in code that configuration cannot raise |
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

This creates an objective across all five product lines and runs ten simulated days end to end, in a separate throwaway database with no real services. It is safe to run on a live server and never touches your real data. It prints a report: pipeline by stage, messages, evidence records, escalations, blocked actions, budget used and learning metrics. With Docker: `docker compose exec api nexus simulate --days 10`.

In the dashboard, create an objective on the Overview tab, press **Run a pass now** a few times, and watch the Pipeline and Reviews tabs fill up.

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
| Your commercial data | Real costs and selling prices | Supplier quotes and market price checks | Your time |

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

Do these once, in this order. Everything can also be done from the command line (section 18).

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
3. **Switch to production with approval mode on:**

   ```ini
   NEXUS_MODE=production
   REQUIRE_OUTREACH_APPROVAL=true
   OUTREACH_DAILY_LIMIT=5
   OUTREACH_MAX_FOLLOWUPS=2
   ```

   Then run `docker compose up -d` to apply. With approval mode on, every first e-mail and follow-up waits in your review queue. You read each draft, edit it if needed, and approve.
4. **After 2–3 weeks**, once the drafts are consistently good, set `REQUIRE_OUTREACH_APPROVAL=false` and raise `OUTREACH_DAILY_LIMIT` step by step. Replies with prices always need your approval, whatever this setting says.
5. **Watch the first month closely:** bounce rate (check the System tab and your mailbox), replies, and whether the "catalogue gap" items are telling you which offers and prices to add.

To stop everything at once, press **Emergency stop** on the Overview tab. To pause only one product line or country, use the pause fields on the Settings tab.

---

## 9. Your daily routine

About 10–15 minutes:

1. **Reviews tab** — decide everything waiting. The badge on the tab shows how many items there are, and Telegram or e-mail alerts tell you when new ones arrive.
2. **Catalogue gaps** — items of kind *catalogue* say which offer or selling price is missing. Add it, and parked deals resume automatically within six hours.
3. **Pipeline tab** — when a deal is won or lost, press **Won** or **Lost**. For a win, enter revenue and margin if you can. These outcomes are what the learning loop measures.
4. **Overview** — glance at budget used, replies and positive-reply rate.

Weekly: check the price book and offers for expired entries, and look at the System tab for failed tasks or compliance events.

---

## 10. The review queue

Anything NEXUS will not do on its own, or has stopped doing, becomes one review item. The same issue raised again adds to the item's counter ("raised 3×") instead of creating duplicates.

| Kind | What it is | Your options |
|---|---|---|
| **outreach approval** | A first e-mail or follow-up that needs you (approval mode, a regulated market outside your licence, missing regulatory check) | Edit the draft, then **Approve and send**; or **Reject** |
| **reply approval** | A drafted answer to a buyer, usually with a suggested price | Edit, then **Approve and send**; or **Reject** |
| **commercial handoff** | A buyer response NEXUS could not draft an answer for | Handle it yourself, then **Mark resolved** |
| **catalogue** | A deal is waiting for a supplier offer or a selling price | Add it in the Catalogue tab; the item closes itself |
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

1. **Search** — queries such as "Kenya hospital tender medical equipment" run through your search provider. Every result is stored as a source document.
2. **Proposals** — the AI reads the results and proposes organisations. Each proposal must cite the result it came from.
3. **Checks in code** — a proposal is accepted only if the organisation's name actually appears in the cited result. Quoted buying signals must appear word-for-word. Rejected proposals are counted in the task output and never "corrected".
4. **Website** — the organisation's own site is found (directories and social networks such as LinkedIn are not accepted as the website), and its home and about pages are read.
5. **Contact** — NEXUS reads contact, procurement and about pages on the organisation's own site. An e-mail address is used only if it is published there. A person's name is used only if it appears on that page. Otherwise NEXUS uses a published role mailbox (procurement@, tenders@, info@ and so on), addressed to the "Procurement office".
6. **No published address, no contact.** The opportunity is closed as stale rather than guessing an address.

The fetcher respects `robots.txt`, identifies itself (`FETCH_USER_AGENT`), waits a few seconds between requests to the same site, and refuses to fetch private or internal network addresses.

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

- The monthly ceiling is **USD 200**, fixed in code. `BUDGET_MONTHLY_LIMIT_USD` can lower it but never raise it.
- The budget is split into planning lines (AI primary 45, AI secondary 20, research 30, e-mail 25, infrastructure 20, storage 5, automation 15, testing 10, reserve 30), changeable with `BUDGET_CATEGORY_LIMITS_JSON`.
- Every paid action reserves its estimated cost first and is refused if that would exceed its line or the month. The real cost is recorded afterwards.
- AI costs are estimated from token counts at rates set at or above list prices, so NEXUS stops early rather than late.
- When the month's budget is used up, paid work stops, you get an alert, and work resumes on the 1st.
- Server, domain and mailbox are paid to those providers directly and are not tracked by NEXUS. Keep their cost in mind within your USD 200.

The Overview tab shows spent, reserved and the month-end forecast. `nexus report` prints the same from the command line.

---

## 17. Alerts on your phone

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

## 18. Command reference

On the server, run commands inside the API container:

```bash
docker compose exec api nexus <command>
```

Locally with Python, use `python -m app.cli <command>`.

| Command | What it does |
|---|---|
| `nexus doctor` | Shows production readiness (missing settings and warnings) and checks the database |
| `nexus doctor --live` | Also logs in to search, SMTP, IMAP and the AI model |
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

## 19. API reference

All endpoints except `/health` and `/u/…` require the dashboard login (HTTP Basic).

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
| GET/POST | `/api/licenses` | List / register licences |
| POST | `/api/licenses/{id}/deactivate` | Stop relying on a licence |

In development (`NEXUS_ENV=development`), interactive API documentation is at `/docs`. It is switched off in production because it would bypass the login.

---

## 20. Configuration reference

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
| `SEARCH_PROVIDER` | none | `serper`, `brave` or `tavily` |
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

**Alerts, budget and safety**

| Setting | Default | Meaning |
|---|---|---|
| `NOTIFY_CHANNELS` | log | `log`, `telegram`, `email` (comma separated) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `OPERATOR_EMAIL` | — | Alert destinations |
| `NOTIFY_MAX_PER_HOUR` | 10 | Alert cap |
| `BUDGET_MONTHLY_LIMIT_USD` | 200 | Can only be lowered |
| `BUDGET_CATEGORY_LIMITS_JSON` | planning split | Per-line limits |
| `REQUIRE_OUTREACH_APPROVAL` | true in the example | Every outreach e-mail waits for you |
| `REQUIRE_HUMAN_APPROVAL_ABOVE_USD` | 0 | Every financial commitment goes to you |
| `ALLOW_REGULATED_AUTONOMOUS_TRANSACTIONS` | false | Leave false |

---

## 21. Policy rules reference

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
| R-FUP-01 / 02 | block | Follow-up cap reached, or sequence already stopped |
| R-FACT-01 | block | Unsupported figure or claim in the message |
| R-FACT-02 | block | Message not personalised |
| R-BUD-01 | block | Budget line or month exhausted |

---

## 22. Backups, updates and recovery

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

## 23. Troubleshooting

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
