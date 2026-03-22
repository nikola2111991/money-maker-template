# Money Maker

B2B lead gen engine. Scraper kvalifikuje leadove, Claude Code obradjuje per-lead.

## Setup (new user)

1. Clone repo
2. `cp env.example .env` and fill in your API keys
3. `pip install -r requirements.txt`
4. Copy `playbooks/_template.json` to `playbooks/{niche}-{country}.json`, fill in your niche
5. Create output dirs: `mkdir -p ~/Documents/money-maker/leads/`
6. Create deploy repo: `mkdir ~/Documents/mm-demos && cd ~/Documents/mm-demos && git init`
7. Run: `make scrape PLAYBOOK=playbooks/{your-playbook}.json`

### API Keys

| Key | Required | Where to get | What it does |
|-----|----------|-------------|--------------|
| GOOGLE_API_KEY | Yes | console.cloud.google.com (Places API) | Scrapes businesses, reviews, details |
| SERPAPI_KEY | No | serpapi.com | Full review history (free: 100/month) |
| GOOGLE_SEARCH_API_KEY | No | console.cloud.google.com (Custom Search) | Cross-reference verification |
| GOOGLE_SEARCH_CX | No | programmablesearchengine.google.com | Search engine ID for verification |

### Creating a Playbook

Copy `playbooks/_template.json` and fill in:

| Field | Example (tiler AU) | Description |
|-------|-------|-------------|
| niche | "tiler" | Business type for search |
| country_code | "AU" | ISO country code |
| language | "en" | Content language |
| currency | "AUD" | Price display |
| phone_prefix | "+61" | Country phone prefix |
| mobile_prefixes | ["4"] | Mobile number prefixes (after country code) |
| cities | {"Sydney": [...coords]} | Target cities with lat/lng |
| search_queries | ["tiler", "tiling"] | Google Places search terms |
| trade_terms | ["waterproofing", "grout"] | Niche vocabulary for outreach |
| i18n | {...} | All UI strings in target language |

## Architecture

Engine (reusable code) + Playbook (niche-specific JSON).

Key files:
| File | Purpose |
|------|---------|
| config.py | Central config: paths, URLs, API keys |
| playbook.py | Load, validate, list playbooks |
| scraper.py | Google Places scraper + scoring + SerpApi reviews |
| scoring.py | Scoring algorithm v3.1 (6 dimensions, max ~95 pts) |
| render.py | Jinja2 template renderer + GitHub Pages deploy |
| pipeline.py | Outreach tracking CLI (contact, follow-up, stats) |
| models.py | Pydantic validation for schema_draft.json |
| prompt_rules.py | Shared banned words + tone rules for AI copy |
| utils.py | Cyrillic/diacritics utilities |
| Makefile | CLI shortcuts |

## Playbook System

Each niche+country = one JSON file in `playbooks/`. Template: `playbooks/_template.json`.

To add a new niche:
1. Copy `_template.json` to `{niche}-{country}.json`
2. Fill in: cities, search queries, specialty map, i18n, image map, trade_terms
3. Run: `make scrape PLAYBOOK=playbooks/{file}.json`

## Workflow

### Step 1: Scrape (automated)
```
make scrape PLAYBOOK=playbooks/tiler-au.json
```

Scraper runs Google Places API, checks site quality (12 signals), scores leads, pulls SerpApi reviews for HOT+WARM (graceful fallback if quota exhausted), saves to folders: HOT/, WARM/, COOL/.

### Step 2: Per-lead processing (Claude Code)
User gives lead folder. Claude Code does 3 phases:

**Phase 1: Quick Scan (30s)**
- Read schema_draft.json, data.json, review_analysis.json
- Run disqualification checklist:

| Signal | Action |
|--------|--------|
| Last review older than 2 years | Disqualify (likely closed) |
| Already has professional website (custom, fast, responsive, booking) | Disqualify |
| No phone AND no email anywhere online | Disqualify (unreachable) |
| Wrong niche (scraper picked up wrong business type) | Disqualify |
| Rating below 3.0 | Disqualify |
| Franchise / chain (corporate marketing team) | Disqualify |

- If disqualified: move to COOL/, explain why, done.

**Phase 2: Deep Research (2-3min)**
- Web search: "{business name} {city}" for website, socials, owner
- Web search: "{business name} {city} ABN" for owner name (AU only)
- Check competitor_report.json, look at top 2-3 competitors with sites
- Extract concrete work from reviews (use trade terminology from playbook trade_terms)
- Update schema_draft.json with all new data

**Phase 3: Build + Deploy (2-3min)**
- Generate site copy: hero, about story, services, FAQ, benefits
- Use trade terminology from playbook trade_terms
- Fill schema_draft.json completely
- Run: `python3 render.py HOT/001/schema_draft.json out/ --playbook playbooks/tiler-au.json --deploy`
- Generate outreach HTML with personalized messages

### Step 3: Outreach (manual)
```
python3 pipeline.py next 5
```
Opens outreach pages. Copy message, send via WhatsApp/email.
```
python3 pipeline.py contact HOT/001 --channel whatsapp
python3 pipeline.py due
```

## Business Rules

- Set your price per site. NEVER in messages, only on call.
- Outreach: 1 initial message + 1 follow-up (day 2). Then move on.
- Booking: set MM_CAL_URL in .env (share when lead shows interest)
- Trust: through specificity (review quotes, competitor names, concrete numbers)
- No fake credentials. No AI mentions. Developer, not agency.
- Deploy: GitHub Pages (set MM_DEPLOY_REPO and MM_DEPLOY_URL in .env)

## Scoring v3.1

6 dimensions, max ~95 pts. HOT >= 45, WARM >= 28, COOL < 28.

| Dimension | Max | Signals |
|-----------|-----|---------|
| Reputation | 20 | rating (6/9/12) + review count (2/4/6/8) |
| Digital Gap | 20 | no website (15) / bad website (12) + no FB (3) + no IG (2) |
| Reachability | 15 | mobile (10) / phone only (5) + email (3) + verified (2) |
| Business Signals | 18 | premium location (5) + specialty (4) + photos (2) + hours (2) + new biz (2) + competition (2/3) |
| Engagement | 10 | detailed reviews (3) + recent activity (2/4) + known owner (3) |
| Urgency | 8 | negative reviews if rating >= 3.5 (2) + SSL broken (3) + zero online (3) |

## Site Quality Check (12 signals)

| Signal | Penalty | Issue Key |
|--------|---------|-----------|
| Site down (non-200) | -100 | site_down |
| No HTTPS | -15 | no_https |
| No viewport meta | -25 | not_responsive |
| Table-based layout (3+) | -15 | table_layout |
| HTML < 2000 chars | -30 | nearly_empty |
| Flash content | -25 | flash |
| Under construction | -40 | under_construction |
| Parked domain | -50 | parked_domain |
| Template builder (Wix, etc.) | -30 | template_builder |
| Outdated design (2+ signals) | -20 | outdated_design |
| Placeholder content | -35 | placeholder_content |
| Thin content (<100 words) | -25 | thin_content |
| No contact info on site | -15 | no_contact_on_site |

Score < 50 = bad website (is_bad: true).

## Templates

5 pages: index.html, services.html, about.html, contact.html, 404.html
Base template: base.html. Outreach: outreach-template.html.
6 themes: trusted, modern, performance, family, specialist, clean.

## Outreach Style

- COMPLIMENT → PAIN POINT → PROOF structure
- Trade terminology from playbook trade_terms
- Quote specific review by name with concrete work mentioned
- Never: em dashes, emojis, AI phrases, price mentions
- Clean URLs (no markdown links)

## Deploy

GitHub Pages (set MM_DEPLOY_REPO in .env)
render.py --deploy → $MM_DEPLOY_REPO/[slug]/ → git push
Live: $MM_DEPLOY_URL/[slug]/

## Testing

```bash
python3 -m pytest test_scoring.py test_research.py test_render.py test_pipeline.py -v
```
