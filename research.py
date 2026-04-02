#!/usr/bin/env python3
"""research.py - Claude CLI research layer for Money Maker leads.

Runs AFTER scraper.py, BEFORE enrich.py.
Uses Claude CLI (free via Max subscription) to research each lead on the web.

What it does:
1. Reads existing leads from LEADS_DIR (HOT + WARM)
2. Claude CLI researches each business (owner, socials, email, services)
3. Fills missing fields and re-scores leads
4. Moves lead folders if category changes

Usage:
    python3 research.py --playbook playbooks/auto-repair-rs.json
    python3 research.py --playbook playbooks/auto-repair-rs.json --limit 10
    python3 research.py --playbook playbooks/auto-repair-rs.json --only HOT
    python3 research.py --playbook playbooks/auto-repair-rs.json --resume
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import config
from playbook import load_playbook_from_path
from scoring import score_dict

log = logging.getLogger(__name__)

CHECKPOINT_FILE = "_research_checkpoint.json"


def _has_valid_mx(email: str) -> bool:
    """Check if email domain has MX records.

    Returns True if MX exists OR if dns.resolver is unavailable (fail open).
    """
    try:
        import dns.resolver
    except ImportError:
        return True  # Can't check, assume valid
    try:
        domain = email.split("@")[1]
        dns.resolver.resolve(domain, "MX")
        return True
    except Exception:
        return False

# Fields we try to fill
RESEARCH_FIELDS = [
    "owner",
    "years_in_business",
    "facebook",
    "instagram",
    "email",
    "services",
    "specialties",
]

BATCH_SIZE = 2


# ============================================================
# CLAUDE CLI
# ============================================================


def _extract_json(text: str) -> str:
    """Extract JSON from Claude response that may contain markdown or preamble text."""
    text = text.strip()
    # Strip markdown code block
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    # Find first { and last } for object
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def call_claude(
    prompt: str, model: str = "opus", timeout: int = 60, retries: int = 2
) -> dict | None:
    """Call claude CLI and parse JSON response. Free via Max subscription."""
    # Force Max subscription, never use API credits
    env = {**os.environ, "ANTHROPIC_API_KEY": ""}
    for attempt in range(retries + 1):
        try:
            result = subprocess.run(
                ["claude", "-p", prompt, "--output-format", "json", "--model", model],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
            if result.returncode != 0:
                err_msg = (result.stderr or result.stdout or "no output")[:300]
                print(
                    f"  Claude CLI error (attempt {attempt + 1}/{retries + 1}): {err_msg}"
                )
                if attempt < retries:
                    time.sleep(30)
                    continue
                return None

            output = result.stdout.strip()
            if not output:
                if attempt < retries:
                    time.sleep(30)
                    continue
                return None

            # claude --output-format json wraps in {"type":"result","result":"..."}
            wrapper = json.loads(output)
            if isinstance(wrapper, dict) and "result" in wrapper:
                inner = wrapper["result"]
                if isinstance(inner, str):
                    inner = _extract_json(inner)
                    return json.loads(inner)
                return inner
            return wrapper
        except subprocess.TimeoutExpired:
            log.warning(
                "  Claude CLI timeout (attempt %d/%d, %ds)",
                attempt + 1,
                retries + 1,
                timeout,
            )
            if attempt < retries:
                time.sleep(30)
                continue
        except (json.JSONDecodeError, Exception) as e:
            log.warning(
                "  Claude parse error (attempt %d/%d): %s",
                attempt + 1,
                retries + 1,
                e,
            )
            if attempt < retries:
                time.sleep(30)
                continue
    return None


# ============================================================
# VALIDATION
# ============================================================


def _is_bad_owner(owner: str, business_name: str) -> bool:
    """Check if owner value is garbage (e.g. last word of business name)."""
    if not owner or owner.startswith("_POPUNI"):
        return True
    # Single word that's part of the business name
    owner_lower = owner.lower().strip()
    name_words = {w.lower() for w in re.split(r"[\s&,]+", business_name) if len(w) > 2}
    if owner_lower in name_words:
        return True
    # Common garbage values
    garbage = {
        "services",
        "service",
        "tiling",
        "tiler",
        "repairs",
        "renovations",
        "plumbing",
        "electrical",
        "painting",
        "building",
        "maintenance",
        "group",
        "solutions",
        "company",
        "pty",
        "ltd",
    }
    if owner_lower in garbage:
        return True
    # Too short (single char or abbreviation)
    if len(owner_lower) < 3:
        return True
    return False


def _is_bad_url(url: str) -> bool:
    """Check if a social media URL is incomplete/garbage."""
    if not url:
        return True
    # Just the domain with no path
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if not path or path == "/":
        return True
    # Generic profile.php without ID
    if "profile.php" in url and "id=" not in url:
        return True
    return False


def _count_filled_fields(data: dict[str, Any]) -> int:
    """Count how many research-target fields are filled."""
    count = 0
    for f in RESEARCH_FIELDS:
        val = data.get(f)
        if (
            val
            and val != ""
            and val != []
            and not (isinstance(val, str) and val.startswith("_POPUNI"))
        ):
            count += 1
    return count


def _validate_claude_fields(data: dict[str, Any], business_name: str) -> dict[str, Any]:
    """Validate fields extracted by Claude for a single business."""
    validated: dict[str, Any] = {}

    owner = data.get("owner", "")
    if owner and isinstance(owner, str) and len(owner) > 3:
        if not _is_bad_owner(owner, business_name):
            validated["owner"] = owner

    years = data.get("years_in_business")
    if isinstance(years, (int, float)) and 1 <= years <= 60:
        validated["years_in_business"] = int(years)

    founded = data.get("founded_year")
    if isinstance(founded, (int, float)) and 1950 < founded <= datetime.now().year:
        validated["founded_year"] = int(founded)
        if "years_in_business" not in validated:
            validated["years_in_business"] = datetime.now().year - int(founded)

    for social in ("facebook", "instagram"):
        url = data.get(social, "")
        if url and isinstance(url, str) and not _is_bad_url(url):
            if social == "facebook" and "facebook.com/" in url:
                parsed = urlparse(url)
                path = parsed.path.rstrip("/")
                fb_reject = ["/videos", "/photos", "/events", "/posts", "/groups"]
                segments = [s for s in path.split("/") if s]
                if not any(rej in path for rej in fb_reject) and len(segments) <= 2:
                    validated["facebook"] = url
            elif social == "instagram" and "instagram.com/" in url:
                parsed = urlparse(url)
                path = parsed.path.rstrip("/")
                ig_reject = ["/p/", "/reel/", "/stories/", "/explore/", "/tv/"]
                segments = [s for s in path.split("/") if s]
                if not any(rej in path for rej in ig_reject) and len(segments) <= 1:
                    validated["instagram"] = url

    email = data.get("email", "")
    if email and isinstance(email, str) and "@" in email:
        email_lower = email.lower()
        # Reject fake/generic emails
        reject_patterns = ["example", "noreply", "test@", "no-reply"]
        generic_prefixes = ["info@", "admin@", "sales@", "hello@", "support@", "office@"]
        if any(x in email_lower for x in reject_patterns):
            pass
        elif any(email_lower.startswith(g) for g in generic_prefixes):
            log.info("Rejected generic email prefix: %s", email)
        elif _has_valid_mx(email):
            validated["email"] = email
        else:
            log.info("Rejected email (no MX record): %s", email)

    mobile = data.get("mobile", "")
    if mobile and isinstance(mobile, str) and len(mobile) >= 10:
        # Basic sanity: starts with + or digit, has enough digits
        cleaned = re.sub(r"[^\d+]", "", mobile)
        if len(cleaned) >= 10:
            validated["mobile"] = cleaned

    services = data.get("services", [])
    if isinstance(services, list) and services:
        validated["extra_services"] = [s for s in services if isinstance(s, str)][:5]

    if data.get("licensed") is True:
        validated["licensed"] = True

    return validated


# ============================================================
# CLAUDE BATCH RESEARCH
# ============================================================


def _research_batch_with_claude(
    batch: list[dict[str, Any]],
    niche: str,
    playbook: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Research multiple businesses via single Claude CLI call.

    Claude searches the web autonomously. No DuckDuckGo or other search intermediary.

    Args:
        batch: List of dicts with keys: name, city, website.
        niche: Business niche (e.g. "auto-repair", "tiler").

    Returns:
        Dict mapping business name to validated extracted fields.
    """
    if not batch:
        return {}

    sections = []
    for idx, item in enumerate(batch, 1):
        name = item["name"]
        city = item["city"]
        website = item.get("website", "")
        sections.append(
            f"--- BUSINESS {idx}: {name} ---\n"
            f"City: {city}\n"
            f"Niche: {niche}\n"
            f"Website: {website or 'none'}"
        )

    all_sections = "\n\n".join(sections)
    business_names = [item["name"] for item in batch]
    names_json = json.dumps(business_names)

    prompt = f"""You are a B2B lead researcher. Search the web for each business below and extract structured data.

{all_sections}

For each business, search the internet and find:
- owner: Real person's full name (first + last). NOT the business name or part of it.
- years_in_business: How long they've been operating (integer).
- founded_year: Year business was established (integer).
- facebook: Their Facebook page/profile URL (NOT posts/photos/videos/events).
- instagram: Their Instagram profile URL (NOT posts/reels/stories).
- email: Business contact email. Search IN ORDER: (1) Contact/About page of their website, (2) Facebook page "About" section, (3) Instagram bio, (4) Business directories ({", ".join(playbook.get("directory_sites", [])) if playbook else "local directories"}). Personal email preferred (owner@, firstname@). REJECT: noreply@, info@, admin@, sales@, hello@, support@.
- mobile: Owner's mobile number. Mobile numbers start with {", ".join(playbook.get("mobile_prefixes", [])) if playbook else "mobile prefix"} after country code. Check: website contact page, Facebook About, Instagram bio. Do NOT return landline/office numbers.
- services: Up to 5 specific services they offer.
- licensed: true ONLY if explicitly stated somewhere.

Return a JSON object where each key is the EXACT business name from this list: {names_json}

Format:
{{
  "Business Name": {{
    "owner": "John Smith",
    "years_in_business": 15,
    "founded_year": 2010,
    "facebook": "https://www.facebook.com/pagename",
    "instagram": "https://www.instagram.com/handle",
    "email": "contact@example.com",
    "mobile": "+61400000000",
    "services": ["service1", "service2"],
    "licensed": true
  }}
}}

Rules:
- Include ONLY fields you can confidently verify. Omit uncertain fields.
- If no data found for a business, use empty object {{}}.
- Return ONLY valid JSON, no explanation."""

    data = call_claude(prompt, model="opus", timeout=300)
    if not data or not isinstance(data, dict):
        return {}

    results: dict[str, dict[str, Any]] = {}
    for item in batch:
        name = item["name"]
        biz_data = data.get(name, {})
        if not isinstance(biz_data, dict):
            continue
        validated = _validate_claude_fields(biz_data, name)
        if validated:
            results[name] = validated

    return results


# ============================================================
# RESEARCH LOGIC
# ============================================================


@dataclass
class ResearchResult:
    """What we found for a single lead."""

    lead_key: str = ""
    fields_before: int = 0
    fields_after: int = 0
    fields_added: list[str] = field(default_factory=list)
    old_score: int = 0
    new_score: int = 0
    old_category: str = ""
    new_category: str = ""
    sources_used: list[str] = field(default_factory=list)
    category_changed: bool = False


def _load_lead_data(folder: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Load schema_draft.json and data.json for a lead folder."""
    schema_path = os.path.join(folder, "schema_draft.json")
    if not os.path.exists(schema_path):
        log.warning("  Skipping %s: schema_draft.json not found", os.path.basename(folder))
        return None, None
    with open(schema_path, encoding="utf-8") as f:
        schema = json.load(f)

    data_json: dict[str, Any] = {}
    data_path = os.path.join(folder, "data.json")
    if os.path.exists(data_path):
        try:
            with open(data_path, encoding="utf-8") as f:
                data_json = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    return schema, data_json


def research_lead(
    folder: str,
    category: str,
    playbook: dict[str, Any],
    claude_findings: dict[str, Any] | None = None,
    schema: dict[str, Any] | None = None,
    data_json: dict[str, Any] | None = None,
) -> ResearchResult:
    """Research a single lead and update its data files.

    Args:
        folder: Path to lead folder.
        category: Current category (HOT/WARM/COOL).
        playbook: Playbook dict.
        claude_findings: Pre-extracted fields from batch Claude call.
        schema: Pre-loaded schema_draft.json (avoids double read).
        data_json: Pre-loaded data.json (avoids double read).
    """
    result = ResearchResult()
    result.lead_key = f"{category}/{os.path.basename(folder)}"

    if schema is None or data_json is None:
        schema, data_json = _load_lead_data(folder)
    data_path = os.path.join(folder, "data.json")
    schema_path = os.path.join(folder, "schema_draft.json")

    name = schema.get("name", "") or data_json.get("name", "")
    result.fields_before = _count_filled_fields(schema)
    result.old_score = data_json.get("score", 0) or schema.get("_score", 0)
    result.old_category = category

    if not name:
        log.warning("  Skipping lead with no name: %s", folder)
        return result

    log.info("  Researching: %s", name)

    bad_owner = _is_bad_owner(schema.get("owner", ""), name)
    bad_facebook = _is_bad_url(schema.get("facebook", ""))
    bad_instagram = _is_bad_url(schema.get("instagram", ""))

    findings: dict[str, Any] = {}

    # Apply Claude findings (from batch call)
    if claude_findings:
        result.sources_used.append("claude_cli")
        findings.update(claude_findings)
        log.info("    Claude extracted %d fields", len(claude_findings))

    # Apply findings to schema and data.json
    fields_added: list[str] = []

    for field_name, value in findings.items():
        if field_name.startswith("_"):
            continue

        current = schema.get(field_name, "")
        is_empty = not current or (
            isinstance(current, str) and current.startswith("_POPUNI")
        )
        is_bad_val = (
            (field_name == "owner" and bad_owner)
            or (field_name == "facebook" and bad_facebook)
            or (field_name == "instagram" and bad_instagram)
        )

        if not is_empty and not is_bad_val:
            if field_name not in ("extra_services",):
                continue

        if field_name == "extra_services":
            existing = schema.get("services", [])
            if isinstance(existing, list) and isinstance(value, list):
                existing_titles = {
                    s.get("title", "").lower() for s in existing if isinstance(s, dict)
                }
                for svc in value:
                    if isinstance(svc, str) and svc.lower() not in existing_titles:
                        existing.append({"title": svc, "description": "", "price": ""})
                        fields_added.append(f"service:{svc}")
                schema["services"] = existing
        elif field_name == "years_in_business":
            schema["years_established"] = str(value)
            data_json["years_in_business"] = value
            fields_added.append(field_name)
        elif field_name == "founded_year":
            schema["founded"] = str(value)
            fields_added.append(field_name)
        elif field_name == "owner":
            schema["owner"] = value
            parts = value.split()
            if parts:
                schema["owner_short"] = parts[0]
            fields_added.append(field_name)
        elif field_name == "mobile":
            # Only set if no existing mobile
            if not schema.get("phone") and not data_json.get("mobile"):
                schema["phone"] = value
                schema["phone_display"] = value
                data_json["mobile"] = value
                fields_added.append("mobile")
        elif field_name in ("facebook", "instagram", "email"):
            schema[field_name] = value
            data_json[field_name] = value
            fields_added.append(field_name)
        elif field_name == "licensed":
            schema["_licensed"] = True
            fields_added.append("licensed")

    result.fields_added = fields_added
    result.fields_after = _count_filled_fields(schema)

    # Store research metadata
    schema["_researched"] = True
    schema["_research_sources"] = result.sources_used
    schema["_research_date"] = datetime.now(timezone.utc).isoformat()

    # Re-score with new data
    score_data = {**data_json, **schema}
    new_score, new_category, new_breakdown = score_dict(score_data, playbook)

    result.new_score = new_score
    result.new_category = new_category
    result.category_changed = new_category != category

    # Update score in data
    schema["_score"] = new_score
    schema["_category"] = new_category
    schema["_score_breakdown"] = new_breakdown
    data_json["score"] = new_score
    data_json["category"] = new_category
    data_json["score_breakdown"] = new_breakdown

    # Save updated files
    with open(schema_path, "w", encoding="utf-8") as f:
        json.dump(schema, f, ensure_ascii=False, indent=2)

    if data_json:
        with open(data_path, "w", encoding="utf-8") as f:
            json.dump(data_json, f, ensure_ascii=False, indent=2)

    return result


def move_lead_folder(
    folder: str, old_category: str, new_category: str, leads_dir: str
) -> str:
    """Move lead folder from old category to new category directory."""
    folder_name = os.path.basename(folder)
    new_parent = os.path.join(leads_dir, new_category)
    os.makedirs(new_parent, exist_ok=True)

    new_folder = os.path.join(new_parent, folder_name)

    if os.path.exists(new_folder):
        new_folder = new_folder + "_moved"

    shutil.move(folder, new_folder)
    log.info(
        "  MOVED: %s → %s/%s", folder_name, new_category, os.path.basename(new_folder)
    )
    return new_folder


# ============================================================
# CHECKPOINT
# ============================================================


def load_checkpoint(leads_dir: str) -> set[str]:
    """Load research checkpoint."""
    path = os.path.join(leads_dir, CHECKPOINT_FILE)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("researched", []))
    return set()


def save_checkpoint(leads_dir: str, researched: set[str]) -> None:
    """Save research checkpoint."""
    path = os.path.join(leads_dir, CHECKPOINT_FILE)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "researched": sorted(researched),
                "last_updated": datetime.now(timezone.utc).isoformat(),
            },
            f,
            indent=2,
        )


# ============================================================
# QUALITY REPORT
# ============================================================


def _print_quality_report(leads: list[dict[str, str]]) -> None:
    """Print data quality report across all leads."""
    total = len(leads)
    if total == 0:
        return

    field_labels = {
        "owner": "owner",
        "years_established": "years_in_biz",
        "facebook": "facebook",
        "instagram": "instagram",
        "email": "email",
    }

    counts: dict[str, int] = {k: 0 for k in field_labels}
    researched_count = 0

    for lead in leads:
        schema_path = os.path.join(lead["folder"], "schema_draft.json")
        try:
            with open(schema_path, encoding="utf-8") as f:
                schema = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        if schema.get("_researched"):
            researched_count += 1

        for field_key in field_labels:
            val = schema.get(field_key, "")
            if (
                val
                and isinstance(val, str)
                and not val.startswith("_POPUNI")
                and len(val) > 2
            ):
                counts[field_key] += 1
            elif val and not isinstance(val, str):
                counts[field_key] += 1

    print(f"\n  DATA QUALITY REPORT ({total} leads)")
    print(f"  {'Field':<16} {'Filled':>8}   {'%':>5}")
    print(f"  {'-' * 35}")
    for field_key, label in field_labels.items():
        filled = counts[field_key]
        pct = (filled / total * 100) if total else 0
        print(f"  {label:<16} {filled:>4}/{total:<4}  {pct:5.0f}%")
    print(f"  {'-' * 35}")
    print(
        f"  Researched:     {researched_count:>4}/{total:<4}  {researched_count / total * 100:5.0f}%"
    )
    print()


# ============================================================
# CLI
# ============================================================


def main() -> None:
    """Run Claude CLI research on existing leads."""
    parser = argparse.ArgumentParser(description="Research leads via Claude CLI")
    parser.add_argument("--playbook", required=True, help="Path to playbook JSON")
    parser.add_argument(
        "leads_dir",
        nargs="?",
        default=str(config.LEADS_DIR),
        help=f"Path to leads directory (default: {config.LEADS_DIR})",
    )
    parser.add_argument(
        "--only", choices=["HOT", "WARM", "COOL"], help="Only research this category"
    )
    parser.add_argument("--limit", type=int, help="Max leads to research")
    parser.add_argument(
        "--resume", action="store_true", help="Skip already-researched leads"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be researched, don't change files",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show data quality report only, don't research",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )

    playbook = load_playbook_from_path(args.playbook)
    niche = playbook.get("niche", "business")
    leads_dir = os.path.abspath(args.leads_dir)

    if not os.path.isdir(leads_dir):
        log.error("Leads directory not found: %s", leads_dir)
        sys.exit(1)

    # Find leads
    categories = [args.only] if args.only else ["HOT", "WARM"]
    leads: list[dict[str, str]] = []

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
                    "key": f"{cat}/{folder_name}",
                }
            )

    if not leads:
        log.error("No leads found in %s", leads_dir)
        sys.exit(1)

    # Stats-only mode
    if args.stats:
        _print_quality_report(leads)
        return

    # Resume support
    researched_keys: set[str] = set()
    if args.resume:
        researched_keys = load_checkpoint(leads_dir)
        before = len(leads)
        leads = [lead for lead in leads if lead["key"] not in researched_keys]
        if before > len(leads):
            log.info(
                "Resume: skipping %d already-researched leads", before - len(leads)
            )

    if args.limit:
        leads = leads[: args.limit]

    print(f"\n{'=' * 60}")
    print(f"  RESEARCH LAYER: {niche.upper()}")
    print(f"  Leads to research: {len(leads)}")
    print(f"  Categories: {', '.join(categories)}")
    print("  Source: Claude CLI (free via Max subscription)")
    print(f"{'=' * 60}\n")

    if args.dry_run:
        for entry in leads:
            print(f"  Would research: {entry['key']}")
        print(f"\n  Total: {len(leads)} leads (dry run, no changes)")
        return

    # Research in batches
    results: list[ResearchResult] = []
    category_changes: list[tuple[str, str, str, str]] = []
    timing: dict[str, float] = {"claude": 0.0, "lead": 0.0}
    total_start = time.time()
    total = len(leads)

    for batch_start in range(0, total, BATCH_SIZE):
        batch_leads = leads[batch_start : batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
        print(f"\n--- Batch {batch_num}/{total_batches} ({len(batch_leads)} leads) ---")

        # Phase 1: Load data for batch
        data_cache: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        claude_batch: list[dict[str, Any]] = []

        for lead in batch_leads:
            schema, data_json = _load_lead_data(lead["folder"])
            if schema is None:
                print(f"  Skipping {lead['key']}: no schema_draft.json")
                continue
            name = schema.get("name", "") or data_json.get("name", "")
            city = schema.get("city", "") or data_json.get("city", "")
            website = schema.get("website", "") or data_json.get("website", "")
            data_cache[lead["folder"]] = (schema, data_json)

            if name:
                claude_batch.append({"name": name, "city": city, "website": website})

        # Phase 2: Single Claude CLI call for entire batch
        claude_results: dict[str, dict[str, Any]] = {}
        if claude_batch:
            print(f"  Sending {len(claude_batch)} leads to Claude CLI...")
            t0 = time.time()
            claude_results = _research_batch_with_claude(claude_batch, niche, playbook=playbook)
            timing["claude"] += time.time() - t0
            found_count = sum(1 for v in claude_results.values() if v)
            print(f"  Claude found data for {found_count}/{len(claude_batch)} leads")

        # Phase 3: Apply findings per lead
        for idx, lead in enumerate(batch_leads, 1):
            global_idx = batch_start + idx
            print(f"[{global_idx}/{total}] {lead['key']}")

            try:
                schema, data_json = data_cache.get(lead["folder"], (None, None))
                if schema is None:
                    schema, data_json = _load_lead_data(lead["folder"])
                name = schema.get("name", "") or data_json.get("name", "")

                t0 = time.time()
                result = research_lead(
                    folder=lead["folder"],
                    category=lead["category"],
                    playbook=playbook,
                    claude_findings=claude_results.get(name),
                    schema=schema,
                    data_json=data_json,
                )
                timing["lead"] += time.time() - t0
                results.append(result)
                researched_keys.add(lead["key"])

                if result.fields_added:
                    print(f"  + Added: {', '.join(result.fields_added)}")
                else:
                    print("  = No new data found")

                if result.category_changed:
                    print(
                        f"  *** SCORE CHANGE: {result.old_score} → {result.new_score} ({result.old_category} → {result.new_category})"
                    )
                    category_changes.append(
                        (
                            lead["folder"],
                            lead["key"],
                            result.old_category,
                            result.new_category,
                        )
                    )

            except Exception as e:
                log.error("  Error researching %s: %s", lead["key"], e)
                continue

        # Checkpoint after each batch
        save_checkpoint(leads_dir, researched_keys)

    # Final checkpoint
    save_checkpoint(leads_dir, researched_keys)

    # Move folders for category changes
    if category_changes:
        print("\n--- Category Changes ---")
        for folder, key, old_cat, new_cat in category_changes:
            if os.path.exists(folder):
                move_lead_folder(folder, old_cat, new_cat, leads_dir)

    # Summary
    total_fields_added = sum(len(r.fields_added) for r in results)
    leads_enriched = sum(1 for r in results if r.fields_added)
    upgrades = sum(
        1 for r in results if r.category_changed and r.new_score > r.old_score
    )
    downgrades = sum(
        1 for r in results if r.category_changed and r.new_score < r.old_score
    )

    total_elapsed = time.time() - total_start

    def _fmt_time(seconds: float) -> str:
        m, s = divmod(int(seconds), 60)
        return f"{m}m {s:02d}s" if m else f"{s}s"

    print(f"\n{'=' * 60}")
    print("  RESEARCH COMPLETE")
    print(f"  Leads researched: {len(results)}")
    print(f"  Leads with new data: {leads_enriched}")
    print(f"  Total fields added: {total_fields_added}")
    print(f"  Score upgrades: {upgrades} (WARM→HOT or COOL→WARM)")
    print(f"  Score downgrades: {downgrades}")
    print(f"  {'':>2}TIMING")
    print(f"  {'':>4}Claude CLI:      {_fmt_time(timing['claude'])}")
    print(f"  {'':>4}Apply + Score:   {_fmt_time(timing['lead'])}")
    print(f"  {'':>4}Total:           {_fmt_time(total_elapsed)}")
    print(f"{'=' * 60}\n")

    # Detailed field breakdown
    field_counts: dict[str, int] = {}
    for r in results:
        for f in r.fields_added:
            field_counts[f] = field_counts.get(f, 0) + 1

    if field_counts:
        print("  Fields found:")
        for f, count in sorted(field_counts.items(), key=lambda x: -x[1]):
            print(f"    {f}: {count} leads")

    # Quality report
    all_leads: list[dict[str, str]] = []
    for cat in categories:
        cat_dir = os.path.join(leads_dir, cat)
        if not os.path.isdir(cat_dir):
            continue
        for folder_name in sorted(os.listdir(cat_dir)):
            folder_path = os.path.join(cat_dir, folder_name)
            schema_path = os.path.join(folder_path, "schema_draft.json")
            if os.path.isdir(folder_path) and os.path.exists(schema_path):
                all_leads.append({"category": cat, "folder": folder_path})

    if all_leads:
        _print_quality_report(all_leads)


if __name__ == "__main__":
    main()
