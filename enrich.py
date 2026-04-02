#!/usr/bin/env python3
"""enrich.py - Claude CLI enrichment for Money Maker leads.

Uses `claude -p` (Max subscription, $0 cost) to enrich schema_draft.json
with AI-generated copy, owner detection, and outreach messages.

Usage:
    python3 enrich.py --playbook playbooks/tiler-au.json
    python3 enrich.py --playbook playbooks/tiler-au.json --limit 5
    python3 enrich.py --playbook playbooks/tiler-au.json --model opus --resume
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import logging
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
from prompt_rules import BANNED_WORDS_SET, format_rules
from scoring import score_dict

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)

CATEGORIES: list[str] = ["HOT", "WARM", "COOL"]
CHECKPOINT_FILE = "_enrich_checkpoint.json"
PLACEHOLDERS = {"_POPUNI_ime_vlasnika_", "_POPUNI_Prezime_", ""}


def find_leads(leads_dir: str, categories: list[str] | None = None) -> list[dict]:
    """Find lead folders with schema_draft.json."""
    if categories is None:
        categories = list(CATEGORIES)
    leads: list[dict] = []
    for cat in categories:
        cat_dir = os.path.join(leads_dir, cat)
        if not os.path.isdir(cat_dir):
            continue
        for folder_name in sorted(os.listdir(cat_dir)):
            folder_path = os.path.join(cat_dir, folder_name)
            if not os.path.isdir(folder_path):
                continue
            schema_path = os.path.join(folder_path, "schema_draft.json")
            if not os.path.exists(schema_path):
                continue
            leads.append(
                {
                    "category": cat,
                    "folder": folder_path,
                    "folder_name": folder_name,
                    "schema_path": schema_path,
                    "key": f"{cat}/{folder_name}",
                }
            )
    return leads


def load_checkpoint(leads_dir: str) -> set[str]:
    """Load set of already-enriched lead keys."""
    path = os.path.join(leads_dir, CHECKPOINT_FILE)
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return set(data.get("enriched", []))


def save_checkpoint(leads_dir: str, enriched: set[str]) -> None:
    """Save enrichment progress."""
    path = os.path.join(leads_dir, CHECKPOINT_FILE)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "enriched": sorted(enriched),
                "last_updated": datetime.now(timezone.utc).isoformat(),
            },
            f,
            indent=2,
        )


def _faq_hints(playbook: dict) -> str:
    """Return FAQ hint examples from playbook if available."""
    hints = playbook.get("faq_hints", [])
    if not hints:
        return ""
    examples = ", ".join(f'"{h}"' for h in hints[:5])
    return f"Example questions for this niche: {examples}. "


def _hook_hint(schema: dict, data_json: dict | None = None) -> str:
    """Auto-select outreach angle from score_breakdown + competitor data."""
    bd = schema.get("score_breakdown", {})

    # Competitor context
    comp_line = ""
    if data_json and data_json.get("competitor_report"):
        cr = data_json["competitor_report"]
        total = cr.get("total_u_kvartu", cr.get("total_in_area", 0))
        with_site = cr.get("sa_sajtom", cr.get("with_site", 0))
        if total and with_site:
            comp_line = f"\nCOMPETITOR DATA: {with_site} out of {total} competitors in their area have a website. Use this number in outreach."

    if "ssl_broken" in bd or "bad_website" in bd:
        return f"OUTREACH ANGLE: Their website is broken or outdated. Lead with this as the pain point.{comp_line}"
    if "no_website" in bd:
        return f"OUTREACH ANGLE: They have no website. Competitors in their area do. Lead with invisibility on Google.{comp_line}"
    if "no_facebook" in bd and "no_instagram" in bd:
        return f"OUTREACH ANGLE: Zero social media presence. They rely entirely on word of mouth.{comp_line}"
    if "rating_elite" in bd and "reviews_100+" in bd:
        return f"OUTREACH ANGLE: Excellent reputation (high rating, many reviews) but no online presence to match. Their reviews deserve a proper showcase.{comp_line}"
    return f"OUTREACH ANGLE: Open with a specific review quote as conversation starter.{comp_line}"


def build_enrichment_prompt(
    schema: dict, playbook: dict, data_json: dict | None = None
) -> str:
    """Build comprehensive prompt with ALL available data."""
    language = playbook.get("language", "en")
    lang_names = {
        "en": "English",
        "sr": "Serbian",
        "de": "German",
        "fr": "French",
        "es": "Spanish",
        "it": "Italian",
        "nl": "Dutch",
        "hr": "Croatian",
    }
    lang_name = lang_names.get(language, language)
    niche = playbook.get("niche", "business")
    booking_url = playbook.get("booking_url", config.CAL_COM_URL)
    max_words_email = playbook.get("outreach", {}).get("max_words_email", 80)

    # Basic info
    parts = [
        f"Business: {schema.get('name', '')}",
        f"City: {schema.get('city', '')}",
        f"Rating: {schema.get('rating', 0)} ({schema.get('review_count', 0)} reviews)",
    ]
    if schema.get("district"):
        parts.append(f"District: {schema['district']}")
    if schema.get("owner") and schema["owner"] not in PLACEHOLDERS:
        parts.append(f"Owner: {schema['owner']}")
    if schema.get("specialization"):
        parts.append(f"Specialization: {schema['specialization']}")
    if schema.get("years_established"):
        parts.append(f"Years in business: {schema['years_established']}")
    if schema.get("phone_display"):
        parts.append(f"Phone: {schema['phone_display']}")

    # Services
    if schema.get("services"):
        parts.append("\nServices:")
        for s in schema["services"][:8]:
            parts.append(f"  - {s.get('title', '')}: {s.get('description', '')}")

    # ALL reviews (not just 3)
    if schema.get("reviews"):
        parts.append(f"\nCustomer reviews ({len(schema['reviews'])} total):")
        for r in schema["reviews"]:
            name = r.get("reviewer_name", "Customer")
            text = r.get("text", "")[:400]
            rating = r.get("rating", 5)
            parts.append(f"  [{rating}/5] {name}: {text}")

    # Review keywords
    if schema.get("_review_keywords"):
        parts.append(
            f"\nWhat customers praise: {', '.join(schema['_review_keywords'][:8])}"
        )

    # Competitor data (from data.json if available)
    if data_json and data_json.get("competitor_report"):
        cr = data_json["competitor_report"]
        parts.append("\nCompetitor landscape:")
        total = cr.get("total_u_kvartu", cr.get("total_in_area", 0))
        with_site = cr.get("sa_sajtom", cr.get("with_site", 0))
        without_site = cr.get("bez_sajta", cr.get("without_site", 0))
        avg_rating = cr.get("prosek_rating", cr.get("avg_rating", 0))
        parts.append(f"  Total competitors: {total}")
        parts.append(f"  With website: {with_site}, Without: {without_site}")
        parts.append(f"  Area average rating: {avg_rating}")
        for c in cr.get("top_konkurenti", cr.get("top_competitors", []))[:3]:
            has_site = "has site" if c.get("ima_sajt", c.get("has_site")) else "no site"
            parts.append(
                f"  - {c.get('naziv', c.get('name', '?'))} ({c.get('rating', 0)} stars, {has_site})"
            )

    # Website content (scraped from their existing site)
    site_content = schema.get("site_content", "") or (data_json or {}).get(
        "site_content", ""
    )
    if site_content:
        parts.append(f"\nContent from their existing website:\n{site_content[:1500]}")

    # Review velocity
    if data_json and data_json.get("review_analysis", {}).get("review_velocity"):
        parts.append(
            f"\nReview velocity: {data_json['review_analysis']['review_velocity']}"
        )

    # Premium location
    premium_locations = playbook.get("premium_locations", [])
    district = schema.get("district", "")
    if district and any(p.lower() in district.lower() for p in premium_locations):
        parts.append(f"Premium location: Yes ({district})")

    # Social media presence
    fb = schema.get("facebook") or (data_json or {}).get("facebook", "")
    ig = schema.get("instagram") or (data_json or {}).get("instagram", "")
    if fb:
        parts.append(f"Facebook: {fb}")
    else:
        parts.append("Facebook: none")
    if ig:
        parts.append(f"Instagram: {ig}")
    else:
        parts.append("Instagram: none")

    # Digital gap info for outreach
    if not schema.get("website"):
        parts.append(
            f"\nDigital gap: NO WEBSITE. When someone searches '{niche} {schema.get('city', '')}' on Google, this business does not appear."
        )
    elif schema.get("_site_quality_bad") or (
        data_json and data_json.get("site_quality", {}).get("is_bad")
    ):
        site_issues = (data_json or {}).get("site_quality", {}).get("issues", [])
        parts.append(
            f"\nDigital gap: BAD WEBSITE ({', '.join(site_issues) if site_issues else 'poor quality'}). Losing customers to competitors with better sites."
        )

    context = "\n".join(parts)

    prompt = f"""You are a B2B lead researcher, website copywriter, and outreach specialist for {niche} businesses. Write in {lang_name}.

STEP 1 - RESEARCH: Use your knowledge to find missing data for this {niche} business.
Fill in any missing fields from the business data below:
- owner: Full name of the business owner (first + last). NOT the business name.
- facebook: Facebook page/profile URL (NOT posts/photos/videos)
- instagram: Instagram profile URL (NOT posts/reels/stories)
- email: Business email address
- years_in_business: Integer, years of operation
Only include fields you are confident about. Omit uncertain fields. Better empty than wrong.

STEP 2 - QUALIFY: Is this business worth building a website for?
Include "qualified": true or false in your response.
QUALIFIED if: has phone number, rating >= 3.5, operational, 5+ reviews.
DISQUALIFIED if: no phone, closed/permanently closed, rating < 3.0, fake reviews.
If disqualified, include "disqualified_reason" and skip Step 3.

STEP 3 - GENERATE COPY (only if qualified):

WEBSITE COPY:
Generate these site copy fields as JSON:
- hero_headline, hero_subtitle
- about_headline, about_subtitle, about_story, about_blockquote
- core_values (array of 3: title, description, ikona)
- about_stats (array of 2-4: value, label)
- benefits_headline, services_subtitle, contact_subtitle
- service_area
- faq (array of 3-5: question, answer)

{format_rules(playbook, context="site")}

OUTREACH MESSAGES:
{_hook_hint(schema, data_json)}
Why this business needs a website (use the most relevant pain points in messages):
- Customers search Google before calling. No website = invisible to new customers.
- Competitors with websites get the calls they're missing.
- Their great reviews are buried on Google Maps. A website puts reviews front and center.
- A website builds trust: customers see services, prices, photos before calling. More qualified leads, less tire-kickers.
- They're paying for Google Business Profile but getting half the value without a website to link to.

OUTREACH RULES:
- Structure: PAIN POINT → PROOF → SOLUTION. Never lead with solution or compliment.
- Every message MUST include 1 review quote with reviewer name + trade terminology from playbook.
- Use NAMED competitor from competitor_report (top_konkurenti[0]) with their review count.
- Demo link framing: "I put this together for you using your {{N}} reviews: [DEMO_URL]"

- whatsapp_initial: Max 3 sentences. (1) Opener (see OPENER SELECTION). (2) Demo link with reciprocity framing. (3) CTA (rotate). Separate each sentence with blank line. Sign as "Nikola".
- email_subject: Max 8 words. Curiosity gap. BANNED: business name alone, "website", "demo". Patterns: "{{Owner}}, noticed something about your competitors" / "{{N}} of {{total}} have this" / "{{Reviewer}} wrote something about {{Business}}".
- email_initial: Max {max_words_email} words. (1) Opener (see OPENER SELECTION). (2) Review quote with name + trade term. (3) Demo link with reciprocity framing. (4) CTA (rotate). Sign as "Nikola".
- email_ps: P.S. line with strongest proof point. E.g. "P.S. {{Competitor}} launched their site last month. They now show up first for '{{niche}} {{city}}'."
- followup_1: Day 2. Max 3 sentences. Name specific competitor with website + review count.
- followup_2: Day 4. Max 3 sentences. Industry insight or review keyword stat.
- followup_3: Day 6. Max 2 sentences. "Demo stays live for 7 days, then I take it down. [DEMO_URL]"

OPENER SELECTION (choose based on lead data):
- If rating >= 4.8 AND reviews > 30: "{{Owner}}, {{N}} reviews and every one is 5 stars. That is rare in {{city}}."
- If has strong review with trade detail: "{{Owner}}, {{Reviewer}} wrote about the {{specific_work}}. That level of detail is rare."
- If competitor has website, lead does not: "{{Owner}}, {{N}} of {{total}} {{niche}}s in {{city}} have a website. Yours does not show up."
- Default: "{{Owner}}, do most of your jobs come from referrals or Google?"

CTA ROTATION (never same for consecutive leads):
1. "Reply 'interested' and I will walk you through it."
2. "Reply 'yes' if you want it live."
3. "Worth a look? Reply and I will send details."
4. "Reply with your best time for a 5min call."

OWNER (if not provided above):
- owner: Best guess at owner name from business name or reviews. If impossible, return empty string.
- owner_short: Last name or short form.

RULES:
- Be specific. Use real details from the data.
- No generic marketing fluff. Every sentence should reference something concrete.
{format_rules(playbook)}
- Keep booking link as: {booking_url}
- NEVER mention price in outreach. Price is discussed only on a call.
- Return ONLY valid JSON, no markdown formatting.

BUSINESS DATA:
{context}"""

    return prompt


def call_claude(prompt: str, model: str = "opus", timeout: int = 120) -> dict | None:
    """Call claude CLI and parse JSON response."""
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "json", "--model", model],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            log.warning("Claude CLI error: %s", result.stderr[:200])
            return None

        # Parse the JSON output from claude CLI
        output = result.stdout.strip()
        if not output:
            log.warning("Claude CLI returned empty output")
            return None

        # claude --output-format json wraps in {"type":"result","result":"..."}
        try:
            wrapper = json.loads(output)
            if isinstance(wrapper, dict) and "result" in wrapper:
                inner = wrapper["result"]
                if isinstance(inner, str):
                    # Strip markdown fences if present
                    inner = inner.strip()
                    if inner.startswith("```"):
                        inner = inner.split("\n", 1)[1]
                        if inner.endswith("```"):
                            inner = inner[:-3]
                        inner = inner.strip()
                    return json.loads(inner)
                return inner
            return wrapper
        except json.JSONDecodeError:
            # Try parsing as raw JSON
            return json.loads(output)

    except subprocess.TimeoutExpired:
        log.warning("Claude CLI timed out after %ds", timeout)
        return None
    except json.JSONDecodeError as e:
        log.warning("JSON parse error: %s", e)
        return None
    except Exception as e:
        log.warning("Unexpected error: %s", e)
        return None


def validate_enriched(data: dict) -> tuple[dict, list[str]]:
    """Validate and fix Claude enrichment output.

    Returns (cleaned_data, warnings).
    """
    warnings: list[str] = []

    # core_values: must be list of 3 dicts with title, description, ikona
    if "core_values" in data:
        cv = data["core_values"]
        if not isinstance(cv, list):
            warnings.append("core_values not a list, removed")
            del data["core_values"]
        else:
            valid_icons = {"heart", "clock", "check", "shield"}
            cleaned = []
            for item in cv[:3]:
                if isinstance(item, dict) and "title" in item and "description" in item:
                    if item.get("ikona") not in valid_icons:
                        item["ikona"] = "check"
                    cleaned.append(item)
            if len(cleaned) < 3:
                warnings.append(f"core_values has {len(cleaned)} items, need 3")
                del data["core_values"]
            else:
                data["core_values"] = cleaned

    # faq: must be list of 3+ dicts with question, answer
    if "faq" in data:
        faq = data["faq"]
        if not isinstance(faq, list):
            warnings.append("faq not a list, removed")
            del data["faq"]
        else:
            cleaned = [
                f
                for f in faq
                if isinstance(f, dict) and "question" in f and "answer" in f
            ]
            if len(cleaned) < 3:
                warnings.append(f"faq has {len(cleaned)} items, need 3+")
                del data["faq"]
            else:
                data["faq"] = cleaned[:7]

    # about_stats: must be list of 2-4 dicts with value, label
    if "about_stats" in data:
        stats = data["about_stats"]
        if not isinstance(stats, list):
            warnings.append("about_stats not a list, removed")
            del data["about_stats"]
        else:
            cleaned = [
                s
                for s in stats
                if isinstance(s, dict) and "value" in s and "label" in s
            ]
            data["about_stats"] = cleaned[:4]

    # String fields: strip, em dash fix, banned word check
    string_fields = [
        "hero_headline",
        "hero_subtitle",
        "about_headline",
        "about_subtitle",
        "about_story",
        "about_blockquote",
        "benefits_headline",
        "services_subtitle",
        "contact_subtitle",
    ]
    for field in string_fields:
        if field in data and isinstance(data[field], str):
            val = data[field].strip()
            val = val.replace("\u2014", ".")  # em dash fix
            found = [w for w in BANNED_WORDS_SET if w in val.lower()]
            if found:
                warnings.append(f"{field} contains banned words: {found}")
            data[field] = val

    # hero_headline max ~10 words
    if "hero_headline" in data and isinstance(data["hero_headline"], str):
        words = data["hero_headline"].split()
        if len(words) > 12:
            warnings.append(f"hero_headline too long: {len(words)} words")

    return data, warnings


def validate_research_fields(data: dict) -> dict:
    """Validate format of Claude research output. Remove invalid URLs/emails."""
    if "facebook" in data:
        if not re.match(
            r"https?://(www\.)?facebook\.com/", str(data.get("facebook", ""))
        ):
            data.pop("facebook")
    if "instagram" in data:
        if not re.match(
            r"https?://(www\.)?instagram\.com/", str(data.get("instagram", ""))
        ):
            data.pop("instagram")
    if "email" in data:
        if not re.match(r"[^@]+@[^@]+\.[^@]+", str(data.get("email", ""))):
            data.pop("email")
    return data


def merge_enriched(schema: dict, enriched: dict) -> tuple[dict, dict, bool]:
    """Merge enriched fields into schema without overwriting scraped data.

    Returns (schema, outreach, disqualified).
    """
    # Qualification gate
    disqualified = enriched.get("qualified") is False
    if disqualified:
        schema["_disqualified"] = True
        schema["_disqualified_reason"] = enriched.get("disqualified_reason", "Unknown")
        return schema, {}, True

    # Research fields: only fill if empty/missing
    research_fields = [
        "owner",
        "owner_short",
        "facebook",
        "instagram",
        "email",
        "years_in_business",
    ]
    for field in research_fields:
        if field in enriched and enriched[field]:
            current = schema.get(field, "")
            if not current or (isinstance(current, str) and current in PLACEHOLDERS):
                schema[field] = enriched[field]

    # Fields that Claude should fill
    enrichable = {
        "hero_headline",
        "hero_subtitle",
        "about_headline",
        "about_subtitle",
        "about_story",
        "about_blockquote",
        "core_values",
        "about_stats",
        "benefits_headline",
        "services_subtitle",
        "contact_subtitle",
        "faq",
        "whatsapp_initial",
        "email_subject",
        "email_initial",
        "followup_1",
    }

    for key in enrichable:
        if key in enriched and enriched[key]:
            current = schema.get(key)
            if (
                not current
                or current == []
                or (isinstance(current, str) and current in PLACEHOLDERS)
            ):
                schema[key] = enriched[key]

    # Split about_story into paragraphs
    story = schema.get("about_story", "")
    if story and "about_paragraphs" not in schema:
        paragraphs = [p.strip() for p in story.split("\n\n") if p.strip()]
        if len(paragraphs) == 1 and len(story) > 200:
            sentences = story.replace(". ", ".\n").split("\n")
            mid = len(sentences) // 2
            paragraphs = [
                " ".join(sentences[:mid]).strip(),
                " ".join(sentences[mid:]).strip(),
            ]
        schema["about_paragraphs"] = [p for p in paragraphs if p]

    # Save outreach separately
    outreach = {}
    for key in ["whatsapp_initial", "email_subject", "email_initial", "email_ps", "followup_1"]:
        if key in enriched and enriched[key]:
            outreach[key] = enriched[key]
            schema.pop(key, None)

    return schema, outreach, False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Enrich leads with Claude CLI (Max subscription)"
    )
    parser.add_argument(
        "leads_dir",
        nargs="?",
        default=str(config.LEADS_DIR),
        help=f"Path to leads directory (default: {config.LEADS_DIR})",
    )
    parser.add_argument("--playbook", required=True, help="Path to playbook JSON")
    parser.add_argument(
        "--model",
        default="opus",
        choices=["opus", "sonnet"],
        help="Claude model (default: opus)",
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="Process only first N leads"
    )
    parser.add_argument(
        "--resume", action="store_true", help="Skip already-enriched leads"
    )
    parser.add_argument(
        "--only",
        type=str,
        default="HOT",
        help="Categories: HOT,WARM,COOL (default: HOT)",
    )
    parser.add_argument(
        "--timeout", type=int, default=120, help="Timeout per lead in seconds"
    )
    parser.add_argument(
        "--auto-deploy",
        action="store_true",
        help="Auto render + deploy after enrichment",
    )
    args = parser.parse_args()

    leads_dir = os.path.abspath(args.leads_dir)
    if not os.path.isdir(leads_dir):
        log.error("Directory not found: %s", leads_dir)
        sys.exit(1)

    with open(args.playbook, encoding="utf-8") as f:
        playbook = json.load(f)

    categories = [c.strip().upper() for c in args.only.split(",") if c.strip()] or None
    leads = find_leads(leads_dir, categories)

    if args.limit:
        leads = leads[: args.limit]

    if not leads:
        log.error("No leads with schema_draft.json in %s", leads_dir)
        sys.exit(1)

    # Resume support
    enriched_keys: set[str] = set()
    if args.resume:
        enriched_keys = load_checkpoint(leads_dir)
        before = len(leads)
        leads = [lead for lead in leads if lead["key"] not in enriched_keys]
        log.info(
            "Resuming: %d already enriched, %d remaining",
            before - len(leads),
            len(leads),
        )

    log.info(
        "ENRICH - %d leads | Model: %s | Playbook: %s",
        len(leads),
        args.model,
        args.playbook,
    )

    ok_count = 0
    err_count = 0

    for i, lead in enumerate(leads, 1):
        key = lead["key"]
        log.info("[%d/%d] %s", i, len(leads), key)

        # Load schema
        with open(lead["schema_path"], encoding="utf-8") as f:
            schema = json.load(f)

        # Load raw data.json if exists (has competitor_report, review_analysis)
        data_json = None
        data_path = os.path.join(lead["folder"], "data.json")
        if os.path.exists(data_path):
            try:
                with open(data_path, encoding="utf-8") as f:
                    data_json = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        # Build prompt
        prompt = build_enrichment_prompt(schema, playbook, data_json)
        log.info("  Prompt: %d chars", len(prompt))

        # Call Claude CLI (with retry)
        enriched = None
        for attempt in range(3):
            enriched = call_claude(prompt, model=args.model, timeout=args.timeout)
            if enriched:
                break
            if attempt < 2:
                wait = 5 * (attempt + 1)
                log.warning("  Retry %d/2 in %ds...", attempt + 1, wait)
                time.sleep(wait)

        if not enriched:
            log.error("  FAILED after 3 attempts")
            # Save error
            err_path = os.path.join(lead["folder"], "_enrich_error.txt")
            with open(err_path, "w", encoding="utf-8") as f:
                f.write(
                    f"Enrichment failed at {datetime.now(timezone.utc).isoformat()}\n"
                )
                f.write(f"Prompt length: {len(prompt)}\n")
            err_count += 1
            continue

        # Validate enriched data
        enriched, val_warnings = validate_enriched(enriched)
        critical_warnings = [w for w in val_warnings if "need 3" in w]
        if critical_warnings and enriched:
            log.warning("  Critical validation issues, retrying with feedback...")
            fix_prompt = (
                f"Your previous JSON response had these problems: {'; '.join(critical_warnings)}. "
                f"Fix them and return the complete JSON again.\n\nOriginal request:\n{prompt}"
            )
            retry_result = call_claude(
                fix_prompt, model=args.model, timeout=args.timeout
            )
            if retry_result:
                retry_result, retry_warnings = validate_enriched(retry_result)
                if not any("need 3" in w for w in retry_warnings):
                    enriched = retry_result
                    val_warnings = retry_warnings
                    log.info("  Retry fixed validation issues")
        if val_warnings:
            log.warning("  Validation warnings: %s", "; ".join(val_warnings))

        # Validate research fields format
        enriched = validate_research_fields(enriched)

        # Merge enriched data
        schema, outreach, disqualified = merge_enriched(schema, enriched)

        # Re-score with enriched data
        old_score = schema.get("score", 0)
        score_data = dict(schema)
        if data_json:
            for k, v in data_json.items():
                if k not in score_data or not score_data[k]:
                    score_data[k] = v
        new_score, new_cat, new_bd = score_dict(score_data, playbook)
        schema["score"] = new_score
        schema["category"] = new_cat
        schema["score_breakdown"] = new_bd
        log.info("  Re-scored: %d → %d/%s", old_score, new_score, new_cat)

        # Save enriched schema
        with open(lead["schema_path"], "w", encoding="utf-8") as f:
            json.dump(schema, f, ensure_ascii=False, indent=2)

        # Handle disqualification
        if disqualified:
            reason = schema.get("_disqualified_reason", "Unknown")
            log.info("  DISQUALIFIED: %s (%s)", key, reason)
            cool_dir = os.path.join(leads_dir, "COOL")
            os.makedirs(cool_dir, exist_ok=True)
            new_path = os.path.join(cool_dir, lead["folder_name"])
            if not os.path.exists(new_path):
                shutil.move(lead["folder"], new_path)
                log.info("  Moved to COOL/")
            enriched_keys.add(key)
            save_checkpoint(leads_dir, enriched_keys)
            continue

        log.info("  Schema enriched: %s", ", ".join(k for k in enriched if enriched[k]))

        # Save outreach if generated
        if outreach:
            outreach_path = os.path.join(lead["folder"], "_outreach.json")
            with open(outreach_path, "w", encoding="utf-8") as f:
                json.dump(outreach, f, ensure_ascii=False, indent=2)
            log.info("  Outreach saved: %s", outreach_path)

        # Auto deploy
        if args.auto_deploy:
            deploy_result = subprocess.run(
                [
                    "python3",
                    "render.py",
                    lead["schema_path"],
                    os.path.join(lead["folder"], "_builds"),
                    "--playbook",
                    args.playbook,
                    "--deploy",
                ],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=os.path.dirname(os.path.abspath(__file__)),
            )
            if deploy_result.returncode == 0:
                slug = schema.get("slug", "")
                deploy_url = f"{config.DEPLOY_BASE_URL}/{slug}/"
                log.info("  Deployed: %s", deploy_url)
                # Update outreach with real URL
                if outreach:
                    outreach["site_url"] = deploy_url
                    outreach_path = os.path.join(lead["folder"], "_outreach.json")
                    with open(outreach_path, "w", encoding="utf-8") as f:
                        json.dump(outreach, f, ensure_ascii=False, indent=2)
            else:
                log.warning("  Deploy failed: %s", deploy_result.stderr[:200])

        # Update checkpoint
        enriched_keys.add(key)
        save_checkpoint(leads_dir, enriched_keys)
        ok_count += 1

    log.info(
        "DONE - Enriched: %d | Failed: %d | Total: %d", ok_count, err_count, len(leads)
    )


if __name__ == "__main__":
    main()
