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

### Step 0: Niche Research (once per niche/country)

Before first scrape for a new niche, populate `niche_intelligence` in the playbook.

Claude Code autonomously researches:
1. Owner perspective (50 years industry experience simulation):
   - Real pain points of business owners in this niche
   - What would make them pay for a website
   - Required certifications/licences for this country
   - Realistic pricing per service
   - Correct trade terminology
2. Customer perspective:
   - What customers search for when choosing this business type
   - What info is missing on most websites in this niche
   - Deal breakers when evaluating a business online
   - Must-have FAQ questions

Results go into playbook `niche_intelligence` section. Never ask user, populate autonomously.

**NOTE:** Verify pricing and certifications for your specific state/country before running scraper. Claude simulates expertise based on publicly available data.

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
| Already has professional website (custom design, fast, responsive, has booking/payments, NOT a template) | Disqualify |
| Has template website (Wix, Squarespace, WordPress theme) | DO NOT disqualify. This is our target customer. |
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
- **Rescore lead** if research reveals new info (website found, socials found, etc.)

**Phase 2 AUTO-DECISIONS (never ask user, always decide yourself):**

| Discovery | Action |
|-----------|--------|
| Scraper said "no website" but website exists AND is professional (custom, fast, booking) | Disqualify → move to COOL/, log reason, done |
| Scraper said "no website" but website exists AND is template/bad | Keep processing. Recalculate score: remove no_website(15), add bad_website(12) or mediocre_website(6). Angle: "template replacement" |
| Scraper said "no website" but website exists AND is decent (custom, responsive, has content) | Disqualify → COOL/ |
| Scraper said "has website" and website is template/bad | Keep processing. Angle: "template replacement" or "website upgrade" |
| Scraper said "has website" and website is professional | Disqualify → COOL/ |
| Score drops below 28 after rescore | Move to COOL/, done |
| Score drops below 45 (was HOT, now WARM) | Move to WARM/, continue Phase 3 from WARM |
| Score stays HOT after rescore | Continue Phase 3 |
| Brand name mismatch (Google listing vs website) | Note in schema_draft.json brand_notes field, continue. This is a SELLING POINT (brand confusion = problem we solve) |
| Owner name found | Add to schema_draft.json, use in outreach personalization |
| Multiple businesses at same address | Pick the one matching niche, disqualify others |

**RULE: Never ask user for decisions during per-lead processing. Always decide based on the table above. If edge case not covered, default to: continue processing, add note in schema_draft.json.**

**Phase 3: Build + Deploy (2-3min)**
- Generate site copy: hero, about story, services, FAQ, benefits
- Use trade terminology from playbook trade_terms
- Fill schema_draft.json completely
- Run: `python3 render.py {lead_folder}/schema_draft.json out/ --playbook playbooks/tiler-au.json --deploy`
- Generate outreach HTML with personalized messages
- **ALWAYS at the end: `open {lead_folder}/outreach.html`** to show the outreach page in browser

**SITE GENERATION RULES (read from playbook niche_intelligence):**
- Hero image: use photos/photo_01.jpg from lead folder. NEVER Unsplash placeholder.
- core_values: each MUST have description (min 10 words) referencing a concrete review.
- FAQ: include questions from `niche_intelligence.faq_must_include`. At least 1 pricing question ("How much does {service} cost in {city}?").
- CTA: "Call {owner_short}" not "Call us" or "Call {phone}".
- service_area: "{city} and surrounding areas including {3-5 nearby suburbs}" from playbook `service_areas`.
- Warranty: prominently display `niche_intelligence.warranty_standard`.
- Certifications: add section with `niche_intelligence.required_certifications`.
- Pricing: use `niche_intelligence.pricing_ranges` for min/max display format.
- Never empty `<p></p>` tags.

**OUTREACH RULES:**
- Angle: economic impact, not semantics. Lead with competitor data.
- Email max 80 words. WhatsApp max 3 sentences.
- CTA: micro-commitment ("Reply 'interested'"), not "happy to jump on a call".
- Include 1 competitor stat: "{X} of {Y} {niche}s in {city} have a website".
- Follow-up schedule: Day 0 (pitch), Day 2 (competitor comparison), Day 4 (social proof), Day 6 (urgency).
- Two outreach angles (auto-selected from data.json):
  - No website: "Competitors with websites get the customers who search Google. You are missing them."
  - Bad/template website: "Your website looks like a template from 2018. Customers close it in 3 seconds."

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

- Price: $2000 USD per site + $150/month maintenance. NEVER in messages, only on call.
- Outreach: 1 initial message + 3 follow-ups (day 2, 4, 6). Then move on.
- Booking: cal.com/money-maker (share when lead shows interest)
- Trust: through specificity (review quotes, competitor names, concrete numbers)
- No fake credentials. No AI mentions. Developer, not agency.
- Deploy: GitHub Pages (mm-demos repo)

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

- PAIN POINT → PROOF → SOLUTION structure (not compliment first)
- Economic angle: "competitors with websites get the Google traffic you are missing"
- Include 1 competitor stat from competitor_report.json
- Quote specific review by name with concrete work mentioned
- Trade terminology from playbook trade_terms
- Micro-commitment CTA: "Reply 'interested'" not "happy to jump on a call"
- Email max 80 words. WhatsApp max 3 sentences.
- Never: em dashes, emojis, AI phrases, price mentions, "if you're interested", "no pressure", "happy to", "feel free"
- Clean URLs (no markdown links)

## Deploy

GitHub Pages (repo: nikola2111991/mm-demos)
render.py --deploy → ~/Documents/mm-demos/[slug]/ → git push
Live: https://nikola2111991.github.io/mm-demos/[slug]/

## Testing

```bash
python3 -m pytest test_scoring.py test_research.py test_render.py test_pipeline.py -v
```
