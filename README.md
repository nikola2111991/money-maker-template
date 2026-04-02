# Money Maker

B2B lead generation system for service businesses. Finds businesses that need a website, builds them a personalized demo site using their Google reviews, and contacts them with a tailored outreach sequence.

## What it does

1. **Scrape** Google Maps for businesses in your niche/city
2. **Score** each lead across 6 dimensions (max 95 points): reputation, digital gap, reachability, business signals, engagement, urgency
3. **Research** missing data (owner name, email, mobile, services) using Claude CLI ($0 via Max subscription)
4. **Generate** a demo website using their real reviews, photos, and competitor data
5. **Generate** personalized outreach messages (WhatsApp, email, 3 follow-ups)
6. **Deploy** demo sites to GitHub Pages
7. **Track** outreach, responses, and conversions via CLI

## Quick start

```bash
# 1. Clone and install
git clone https://github.com/nikola2111991/money-maker-template.git
cd money-maker-template
pip install -r requirements.txt

# 2. Configure environment
cp env.example .env
# Edit .env with your API keys (see below)

# 3. Create your playbook (or use the example)
cp playbooks/_template.json playbooks/my-niche.json
# Edit with your niche, country, cities, search terms

# 4. Scrape leads
python3 scraper.py --playbook playbooks/my-niche.json --cities top8

# 5. Research missing data (free via Claude Max)
python3 research.py --playbook playbooks/my-niche.json --only HOT

# 6. Process individual leads
claude "Obradi lead: HOT/001"

# 7. Track outreach
python3 pipeline.py next 5
python3 pipeline.py contact HOT/001 --channel whatsapp
python3 pipeline.py due
python3 pipeline.py stats
```

## API keys needed

| Key | Required | Where to get it |
|-----|----------|----------------|
| `GOOGLE_API_KEY` | Yes | [Google Cloud Console](https://console.cloud.google.com/) (Places API) |
| `ANTHROPIC_API_KEY` | Optional | [Anthropic](https://console.anthropic.com/) (only if using API, CLI is free with Max) |
| `SERPAPI_KEY` | Optional | [SerpAPI](https://serpapi.com/) (for full review history) |

## Environment variables

```bash
# Required
GOOGLE_API_KEY=your_google_places_api_key

# Deployment (set these to your GitHub Pages repo)
MM_DEPLOY_URL=https://yourusername.github.io/your-demos
MM_DEPLOY_REPO=~/Documents/your-demos/

# Personal branding
MM_CAL_URL=cal.com/your-calendar
MM_SENDER_EMAIL=you@yourdomain.com
```

## Playbook system

Every niche/country gets its own JSON config. The playbook controls everything: language, currency, phone format, cities, search terms, scoring thresholds, outreach channels.

```bash
# Copy the template
cp playbooks/_template.json playbooks/plumber-us.json
```

Key fields to configure:
- `niche`: Business type (e.g., "plumber")
- `country_code`: ISO code (e.g., "US")
- `language`: Language for copy (e.g., "en")
- `cities`: Target cities with coordinates
- `search_queries`: Google search terms
- `exclude_words`: Filter out wrong niches
- `mobile_prefixes`: For WhatsApp number detection
- `trade_terms`: Industry vocabulary for authentic copy

See `playbooks/tiler-au.json` for a complete example.

## Pipeline commands

```bash
python3 pipeline.py next 5              # Top 5 uncontacted leads
python3 pipeline.py contact HOT/004 --channel whatsapp
python3 pipeline.py followup 004 --type followup_1 --channel whatsapp
python3 pipeline.py respond 004 --outcome positive
python3 pipeline.py convert 004 --deal 500
python3 pipeline.py due                  # Follow-ups due today
python3 pipeline.py stats               # Funnel report
python3 pipeline.py open 004            # Open outreach page in browser
```

## Lead scoring (6 dimensions, max 95 points)

| Dimension | Max | What it measures |
|-----------|-----|-----------------|
| Reputation | 20 | Rating + review count |
| Digital Gap | 20 | No website = more points (they need one) |
| Reachability | 15 | Mobile number = 10pts, email = 3pts |
| Business Signals | 18 | Premium location, specialty, photos |
| Engagement | 10 | Detailed reviews, recent activity, known owner |
| Urgency | 8 | Negative reviews, broken SSL, zero online presence |

Categories: **HOT** (45+), **WARM** (28-44), **COOL** (below 28)

## File structure

```
money-maker-template/
├── config.py              # Central config (reads from .env)
├── scraper.py             # Google Places API scraping
├── research.py            # Claude CLI data enrichment
├── enrich.py              # AI copy generation
├── render.py              # Jinja2 template rendering + deploy
├── batch_deploy.py        # Batch render + deploy all leads
├── pipeline.py            # Outreach tracking CLI
├── scoring.py             # Lead scoring algorithm
├── prompt_rules.py        # AI copy rules (tone, banned words, structure)
├── copy_generator.py      # Alternative: Claude API copy generation
├── playbook.py            # Playbook validation
├── models.py              # Pydantic schemas
├── base.html              # Jinja2 base template
├── index.html             # Homepage template
├── about.html             # About page template
├── services.html          # Services page template
├── contact.html           # Contact page template
├── 404.html               # Error page
├── outreach-template.html # Outreach message page
├── style.css              # Site styles
├── main.js                # Client-side JS
├── playbooks/
│   ├── _template.json     # Starter playbook template
│   └── tiler-au.json      # Example: Australian tilers
├── env.example            # Environment template
├── requirements.txt       # Python dependencies
├── CLAUDE.md              # Instructions for Claude Code
└── test_*.py              # Tests
```

## Costs

| Item | Cost |
|------|------|
| Google Places API | ~$0.02 per lead |
| Claude research + enrich | $0 (Max subscription) |
| GitHub Pages hosting | $0 |
| **Total per lead** | **~$0.02** |

## How outreach works

Each processed lead gets an outreach page with:
- WhatsApp button (pre-filled message)
- Email with copy button
- Demo site link
- 3 follow-up messages (Day 2, Day 4, Day 6)

Messages are personalized with:
- Real reviewer names and quotes
- Specific trade terms from reviews
- Competitor names and data
- Years in business, services offered

## Outreach schedule

| Day | Action |
|-----|--------|
| 0 | Initial contact (WhatsApp + Email) |
| 2 | Follow-up 1: Competitor comparison |
| 4 | Follow-up 2: Useful insight |
| 6 | Follow-up 3: Casual close |
| 8+ | No response = move to next lead |

## Tests

```bash
python3 -m pytest test_*.py -q
```

## License

Private. Do not distribute without permission.
