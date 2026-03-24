#!/usr/bin/env python3
"""Money Maker Outreach Pipeline CLI.

Track outreach status, follow-ups, responses, and conversions.

Usage:
    python3 pipeline.py next [N]
    python3 pipeline.py contact <lead> --channel whatsapp --hook quote
    python3 pipeline.py followup <lead> --type followup_1 --channel whatsapp
    python3 pipeline.py respond <lead> --outcome positive
    python3 pipeline.py convert <lead> --deal 1500
    python3 pipeline.py due
    python3 pipeline.py stats
    python3 pipeline.py list [--status contacted]
    python3 pipeline.py open <lead>
    python3 pipeline.py auto-ghost [--dry-run]

Standalone: no imports from render.py. Only stdlib + config.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from config import LEADS_DIR

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CATEGORIES: list[str] = ["HOT", "WARM", "COOL"]
FOLLOWUP_SCHEDULE: dict[str, int] = {
    "followup_1": 2,  # Day 2: competitor comparison
    "followup_2": 4,  # Day 4: social proof
    "followup_3": 6,  # Day 6: urgency/scarcity
}
VALID_CHANNELS: list[str] = ["whatsapp", "viber", "email", "phone"]
VALID_HOOKS: list[str] = [
    "quote",
    "competitor",
    "volume",
    "quality",
    "tradition",
    "voice",
]
VALID_OUTCOMES: list[str] = ["positive", "negative", "ghosted"]
VALID_GHOST_REASONS: list[str] = [
    "no_budget",
    "has_site",
    "wrong_contact",
    "not_interested",
    "no_response",
]
VALID_STATUSES: list[str] = [
    "pending",
    "ready",
    "contacted",
    "responded",
    "converted",
    "ghosted",
]
STATUS_FILE = "_pipeline_status.json"
SCHEMA_FILE = "schema_draft.json"
SCORE_RE = re.compile(r"_(\d+)pts$")


# ---------------------------------------------------------------------------
# Lead Discovery
# ---------------------------------------------------------------------------


def find_leads_dir(cli_override: str | None = None) -> Path:
    """Detection chain: CLI arg > env var > config > error."""
    candidates: list[Path] = []
    if cli_override:
        candidates.append(Path(cli_override).expanduser())
    env = os.environ.get("MM_LEADS_DIR")
    if env:
        candidates.append(Path(env).expanduser())
    candidates.append(LEADS_DIR)

    for p in candidates:
        if p.is_dir() and any((p / cat).is_dir() for cat in CATEGORIES):
            return p

    print("ERROR: leads directory not found.")
    print("Checked:", ", ".join(str(c) for c in candidates))
    print("Set MM_LEADS_DIR env var or use --leads-dir.")
    sys.exit(1)


def resolve_lead(ref: str, leads_dir: Path) -> Path | None:
    """Resolve lead reference to folder path.

    Accepts:
      - "HOT/004" -> folder starting with "004_" in HOT/
      - "004" -> search all categories for "004_"
      - Full folder name "HOT/004_Mobilni_vulkanizer_79pts"
    """
    ref = ref.strip().rstrip("/")

    # Full folder name with category
    if "/" in ref:
        parts = ref.split("/", 1)
        cat, name = parts[0].upper(), parts[1]
        cat_dir = leads_dir / cat
        if not cat_dir.is_dir():
            return None
        # Exact match
        exact = cat_dir / name
        if exact.is_dir():
            return exact
        # Prefix match (number shorthand)
        prefix = name + "_"
        for folder in sorted(cat_dir.iterdir()):
            if folder.is_dir() and folder.name.startswith(prefix):
                return folder
        return None

    # Number only: search all categories
    prefix = ref + "_"
    for cat in CATEGORIES:
        cat_dir = leads_dir / cat
        if not cat_dir.is_dir():
            continue
        for folder in sorted(cat_dir.iterdir()):
            if folder.is_dir() and folder.name.startswith(prefix):
                return folder

    return None


def suggest_similar(ref: str, leads_dir: Path, limit: int = 3) -> list[str]:
    """Find similar lead folders for 'did you mean' suggestions."""
    ref_lower = ref.lower().replace("/", "_")
    matches: list[tuple[str, str]] = []
    for cat in CATEGORIES:
        cat_dir = leads_dir / cat
        if not cat_dir.is_dir():
            continue
        for folder in cat_dir.iterdir():
            if folder.is_dir():
                key = f"{cat}/{folder.name}"
                if ref_lower in folder.name.lower():
                    matches.append((key, folder.name))
    return [m[0] for m in matches[:limit]]


def folder_key(lead_path: Path, leads_dir: Path) -> str:
    """Get relative key like 'HOT/001_VULKANIZER_SAŠA_81pts'."""
    return str(lead_path.relative_to(leads_dir))


def scan_all_leads(leads_dir: Path) -> list[dict]:
    """Scan all lead folders. Returns list of lead info dicts."""
    results: list[dict] = []
    for cat in CATEGORIES:
        cat_dir = leads_dir / cat
        if not cat_dir.is_dir():
            continue
        for folder in sorted(cat_dir.iterdir()):
            if not folder.is_dir():
                continue
            schema_path = folder / SCHEMA_FILE
            info: dict = {
                "folder": folder,
                "key": f"{cat}/{folder.name}",
                "category": cat,
                "name": folder.name,
            }
            # Extract score from folder name as fallback
            m = SCORE_RE.search(folder.name)
            info["score_from_name"] = int(m.group(1)) if m else 0

            # Read schema if exists
            if schema_path.exists():
                schema = read_schema(schema_path)
                info.update(schema)
            else:
                info["_score"] = info["score_from_name"]

            results.append(info)
    return results


# ---------------------------------------------------------------------------
# Status Storage
# ---------------------------------------------------------------------------


def load_status(leads_dir: Path) -> dict:
    """Load pipeline status. Returns {version, leads, meta}."""
    path = leads_dir / STATUS_FILE
    if not path.exists():
        return _empty_status()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if "leads" not in data:
            return _empty_status()
        return data
    except (json.JSONDecodeError, OSError) as e:
        # Backup corrupt file
        backup = path.with_suffix(".json.bak")
        try:
            path.rename(backup)
            print(f"WARNING: Corrupt status file backed up to {backup.name}")
        except OSError:
            pass
        print(f"WARNING: Status file corrupt ({e}). Starting fresh.")
        return _empty_status()


def save_status(leads_dir: Path, status: dict) -> None:
    """Atomic write: write .tmp then rename."""
    status["meta"]["last_updated"] = _now_iso()
    path = leads_dir / STATUS_FILE
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _empty_status() -> dict:
    return {
        "version": 1,
        "leads": {},
        "meta": {"created": _now_iso(), "last_updated": _now_iso()},
    }


def get_lead_status(status: dict, key: str) -> str:
    """Derive status string from lead entry."""
    entry = status["leads"].get(key)
    if not entry:
        return "pending"
    if entry.get("deal_value") is not None:
        return "converted"
    if entry.get("outcome") == "ghosted":
        return "ghosted"
    if entry.get("outcome"):
        return "responded"
    if entry.get("contacted_date"):
        return "contacted"
    if entry.get("ready_date"):
        return "ready"
    return "pending"


# ---------------------------------------------------------------------------
# Schema Reader
# ---------------------------------------------------------------------------


def read_schema(schema_path: Path) -> dict:
    """Read schema_draft.json, extract pipeline-relevant fields."""
    try:
        with open(schema_path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"_score": 0}

    return {
        "_score": data.get("_score", 0),
        "_category": data.get("_category", ""),
        "slug": data.get("slug", ""),
        "name": data.get("name", ""),
        "owner_short": data.get("owner_short", ""),
        "city": data.get("city", ""),
        "phone": data.get("phone", ""),
        "phone_display": data.get("phone_display", ""),
        "email": data.get("email", ""),
        "rating": data.get("rating", 0),
        "review_count": data.get("review_count", 0),
        "_review_keywords": data.get("_review_keywords", []),
        "niche": data.get("niche", ""),
    }


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _find_outreach_html(folder: Path) -> Optional[Path]:
    """Find outreach HTML file in lead folder."""
    for f in folder.iterdir():
        if f.name.endswith("-outreach.html") and f.is_file():
            return f
    return None


def cmd_next(n: int, leads_dir: Path) -> None:
    """Show top N uncontacted leads, open outreach pages in browser."""
    import webbrowser

    status = load_status(leads_dir)
    all_leads = scan_all_leads(leads_dir)

    # Filter uncontacted (skip "ready" leads, they already have sites deployed)
    uncontacted = [
        lead for lead in all_leads if get_lead_status(status, lead["key"]) == "pending"
    ]

    # Sort: leads with phone first, then by score descending
    uncontacted.sort(
        key=lambda x: (1 if x.get("phone") else 0, x.get("_score", 0)),
        reverse=True,
    )
    top = uncontacted[:n]

    if not top:
        print("All leads contacted!")
        return

    print(f"\nTop {len(top)} uncontacted leads:\n")
    print(
        f"  {'#':<4} {'Score':<6} {'Cat':<5} {'Name':<28} {'City':<14} {'Rating':<7} {'Rev':<5} {'Phone':<14}"
    )
    print(
        f"  {'─' * 4} {'─' * 6} {'─' * 5} {'─' * 28} {'─' * 14} {'─' * 7} {'─' * 5} {'─' * 14}"
    )

    opened = 0
    for i, lead in enumerate(top, 1):
        phone = lead.get("phone_display") or lead.get("phone", "")
        name = _trunc(lead.get("name", lead["name"]), 27)
        city = _trunc(lead.get("city", ""), 13)
        rating = lead.get("rating", 0)
        reviews = lead.get("review_count", 0)
        score = lead.get("_score", 0)
        cat = lead.get("_category") or lead["category"]

        no_phone = " !" if not lead.get("phone") else ""
        print(
            f"  {i:<4} {score:<6} {cat:<5} {name:<28} {city:<14} {rating:<7} {reviews:<5} {phone}{no_phone}"
        )

        # Open outreach page in browser
        outreach_html = _find_outreach_html(lead["folder"])
        if outreach_html:
            webbrowser.open(f"file://{outreach_html}")
            opened += 1

    print(f"\nOpened {opened}/{len(top)} outreach pages in browser.")
    print(
        "After sending, log each: python3 pipeline.py contact HOT/001 --channel whatsapp"
    )


def cmd_contact(
    lead_ref: str,
    channel: str,
    hook: str,
    leads_dir: Path,
    notes: str = "",
    force: bool = False,
) -> None:
    """Log outreach contact."""
    lead_path = resolve_lead(lead_ref, leads_dir)
    if not lead_path:
        _lead_not_found(lead_ref, leads_dir)
        return

    key = folder_key(lead_path, leads_dir)
    status = load_status(leads_dir)

    # Check if already contacted
    if key in status["leads"] and not force:
        entry = status["leads"][key]
        print(
            f"WARNING: {key} already contacted ({entry['contacted_date']}, {entry['channel']}, {entry['hook']})."
        )
        print("Use --force to override.")
        return

    # Read schema once for email check and niche detection
    schema_path = lead_path / SCHEMA_FILE
    schema: dict[str, object] = {}
    if schema_path.exists():
        schema = read_schema(schema_path)
        if channel == "email" and not schema.get("email"):
            print(f"WARNING: {key} no email address. Consider whatsapp/viber.")

    # Detect niche from schema or folder structure
    niche = str(schema.get("niche", ""))
    if not niche:
        # Infer from category folder parent (e.g. leads/auto-repair-rs/HOT/001)
        try:
            rel = lead_path.relative_to(leads_dir)
            if len(rel.parts) >= 2:
                niche = rel.parts[0]
        except ValueError:
            pass

    status["leads"][key] = {
        "contacted_date": _today_iso(),
        "channel": channel,
        "hook": hook,
        "niche": niche,
        "followups": [],
        "response_date": None,
        "outcome": None,
        "deal_value": None,
        "notes": notes,
    }
    save_status(leads_dir, status)
    print(f"OK: {key} contacted ({channel}, hook={hook}).")
    print(f"    Follow-up 1 due: {_date_plus(_today_iso(), 2)}")


def cmd_followup(
    lead_ref: str,
    followup_type: str,
    channel: str,
    leads_dir: Path,
) -> None:
    """Log sent follow-up."""
    lead_path = resolve_lead(lead_ref, leads_dir)
    if not lead_path:
        _lead_not_found(lead_ref, leads_dir)
        return

    key = folder_key(lead_path, leads_dir)
    status = load_status(leads_dir)

    if key not in status["leads"]:
        print(f"ERROR: {key} not contacted. Use 'contact' first.")
        return

    entry = status["leads"][key]

    # Check for duplicate followup
    existing_types = [f["type"] for f in entry["followups"]]
    if followup_type in existing_types:
        print(f"WARNING: {followup_type} already sent for {key}.")
        return

    entry["followups"].append(
        {
            "date": _today_iso(),
            "type": followup_type,
            "channel": channel,
        }
    )
    save_status(leads_dir, status)
    print(f"OK: {followup_type} sent for {key} ({channel}).")


def cmd_respond(
    lead_ref: str,
    outcome: str,
    leads_dir: Path,
    notes: str = "",
    reason: str = "",
) -> None:
    """Log response outcome."""
    lead_path = resolve_lead(lead_ref, leads_dir)
    if not lead_path:
        _lead_not_found(lead_ref, leads_dir)
        return

    key = folder_key(lead_path, leads_dir)
    status = load_status(leads_dir)

    if key not in status["leads"]:
        print(f"ERROR: {key} not contacted. Use 'contact' first.")
        return

    entry = status["leads"][key]
    entry["outcome"] = outcome
    entry["response_date"] = _today_iso()
    if notes:
        entry["notes"] = notes
    if outcome == "ghosted":
        entry["ghost_reason"] = reason if reason else "no_response"

    save_status(leads_dir, status)
    emoji = {"positive": "+", "negative": "-", "ghosted": "x"}
    reason_str = (
        f" reason={entry.get('ghost_reason', '')}" if outcome == "ghosted" else ""
    )
    print(f"OK: {key} outcome={outcome}{reason_str} [{emoji.get(outcome, '?')}]")

    if outcome == "positive":
        print("    Next step: python3 pipeline.py convert", lead_ref, "--deal <EUR>")


def cmd_convert(
    lead_ref: str,
    deal_value: int,
    leads_dir: Path,
    notes: str = "",
    deal_type: str = "site",
) -> None:
    """Log conversion with deal amount."""
    lead_path = resolve_lead(lead_ref, leads_dir)
    if not lead_path:
        _lead_not_found(lead_ref, leads_dir)
        return

    key = folder_key(lead_path, leads_dir)
    status = load_status(leads_dir)

    if key not in status["leads"]:
        print(f"ERROR: {key} not contacted.")
        return

    entry = status["leads"][key]
    if not entry.get("outcome"):
        print(f"WARNING: {key} has no response. Log respond first or use --force?")

    entry["deal_value"] = deal_value
    entry["deal_type"] = deal_type
    entry["outcome"] = entry.get("outcome") or "positive"
    entry["response_date"] = entry.get("response_date") or _today_iso()
    if notes:
        entry["notes"] = notes

    save_status(leads_dir, status)
    print(f"OK: {key} CONVERTED! Deal: {deal_value} EUR ({deal_type})")


def cmd_due(leads_dir: Path) -> None:
    """Show leads due for follow-up today."""
    status = load_status(leads_dir)
    today = _today_iso()
    today_dt = datetime.strptime(today, "%Y-%m-%d").date()

    overdue: list[tuple[str, str, int]] = []
    due_today: list[tuple[str, str]] = []
    upcoming: list[tuple[str, str, int]] = []

    for key, entry in status["leads"].items():
        # Skip if already has outcome
        if entry.get("outcome"):
            continue

        contacted = entry.get("contacted_date")
        if not contacted:
            continue

        contacted_dt = datetime.strptime(contacted, "%Y-%m-%d").date()
        sent_types = {f["type"] for f in entry.get("followups", [])}

        for fu_type, fu_days in FOLLOWUP_SCHEDULE.items():
            if fu_type in sent_types:
                continue
            due_date = contacted_dt + timedelta(days=fu_days)
            diff = (due_date - today_dt).days

            short_key = _short_key(key)
            if diff < 0:
                overdue.append((short_key, fu_type, abs(diff)))
            elif diff == 0:
                due_today.append((short_key, fu_type))
            elif diff <= 2:
                upcoming.append((short_key, fu_type, diff))
            break  # Only show next pending followup per lead

    if not overdue and not due_today and not upcoming:
        print("No follow-ups due today.")
        return

    print(f"\nFollow-ups ({today}):\n")

    if overdue:
        print("  OVERDUE:")
        for key, fu_type, days in overdue:
            print(f"    {key} . {fu_type} ({days} days overdue)")

    if due_today:
        print("  TODAY:")
        for key, fu_type in due_today:
            print(f"    {key} . {fu_type}")

    if upcoming:
        print("  UPCOMING:")
        for key, fu_type, days in upcoming:
            print(f"    {key} . {fu_type} (in {days} days)")

    total = len(overdue) + len(due_today) + len(upcoming)
    print(f"\n  Total: {total} follow-up(s)")


def cmd_stats(
    leads_dir: Path,
    last_days: int | None = None,
    by_niche: bool = False,
    by_score: bool = False,
    by_city: bool = False,
) -> None:
    """Show pipeline funnel and breakdowns."""
    status = load_status(leads_dir)
    all_leads = scan_all_leads(leads_dir)
    total = len(all_leads)

    entries = status["leads"]

    # Optional date filter
    if last_days:
        cutoff = (_today_dt() - timedelta(days=last_days)).isoformat()
        entries = {
            k: v for k, v in entries.items() if v.get("contacted_date", "") >= cutoff
        }

    contacted = len(entries)
    responded = sum(
        1 for e in entries.values() if e.get("outcome") and e["outcome"] != "ghosted"
    )
    ghosted = sum(1 for e in entries.values() if e.get("outcome") == "ghosted")
    converted = sum(1 for e in entries.values() if e.get("deal_value") is not None)
    revenue = sum(
        e.get("deal_value", 0) for e in entries.values() if e.get("deal_value")
    )
    revenue_site = sum(
        e.get("deal_value", 0)
        for e in entries.values()
        if e.get("deal_value") and e.get("deal_type", "site") == "site"
    )
    revenue_recurring = sum(
        e.get("deal_value", 0)
        for e in entries.values()
        if e.get("deal_value") and e.get("deal_type") == "site+maintenance"
    )

    period = f" (last {last_days} days)" if last_days else " (total)"
    print(f"\nPipeline Stats{period}:\n")
    print(f"  Total leads:     {total}")
    print(f"  Contacted:       {contacted} ({_pct(contacted, total)})")
    print(f"  Responded:       {responded} ({_pct(responded, contacted)})")
    print(f"  Ghosted:         {ghosted} ({_pct(ghosted, contacted)})")
    print(f"  Converted:       {converted} ({_pct(converted, responded)})")
    print(
        f"  Revenue:         {revenue} EUR (site: {revenue_site}, site+maintenance: {revenue_recurring})"
    )

    if contacted == 0:
        print("\n  No data yet. Send first message!")
        print("  python3 pipeline.py next 5")
        return

    # Breakdown by hook
    hook_stats: dict[str, dict[str, int]] = {}
    for e in entries.values():
        h = e.get("hook", "unknown")
        if h not in hook_stats:
            hook_stats[h] = {"sent": 0, "responded": 0}
        hook_stats[h]["sent"] += 1
        if e.get("outcome") and e["outcome"] != "ghosted":
            hook_stats[h]["responded"] += 1

    print("\n  By hook:")
    for h, s in sorted(
        hook_stats.items(), key=lambda x: x[1]["responded"], reverse=True
    ):
        print(
            f"    {h:<12} {s['sent']} sent, {s['responded']} responded ({_pct(s['responded'], s['sent'])})"
        )

    # Breakdown by channel
    ch_stats: dict[str, dict[str, int]] = {}
    for e in entries.values():
        c = e.get("channel", "unknown")
        if c not in ch_stats:
            ch_stats[c] = {"sent": 0, "responded": 0}
        ch_stats[c]["sent"] += 1
        if e.get("outcome") and e["outcome"] != "ghosted":
            ch_stats[c]["responded"] += 1

    print("\n  By channel:")
    for c, s in sorted(ch_stats.items(), key=lambda x: x[1]["responded"], reverse=True):
        print(
            f"    {c:<12} {s['sent']} sent, {s['responded']} responded ({_pct(s['responded'], s['sent'])})"
        )

    # Breakdown by score range (if --by-score flag)
    if by_score:
        score_ranges = [
            (80, 100, "80+"),
            (70, 79, "70-79"),
            (60, 69, "60-69"),
            (0, 59, "<60"),
        ]
        sr_stats: dict[str, dict[str, int]] = {
            r[2]: {"sent": 0, "responded": 0} for r in score_ranges
        }

        score_map: dict[str, int] = {}
        for lead in all_leads:
            score_map[lead["key"]] = lead.get("_score", 0)

        for key, e in entries.items():
            score = score_map.get(key, 0)
            for lo, hi, label in score_ranges:
                if lo <= score <= hi:
                    sr_stats[label]["sent"] += 1
                    if e.get("outcome") and e["outcome"] != "ghosted":
                        sr_stats[label]["responded"] += 1
                    break

        print("\n  By score range:")
        for label in ["80+", "70-79", "60-69", "<60"]:
            s = sr_stats[label]
            if s["sent"] > 0:
                print(
                    f"    {label:<12} {s['sent']} sent, {s['responded']} responded ({_pct(s['responded'], s['sent'])})"
                )

    # Breakdown by ghost reason
    gr_stats: dict[str, int] = {}
    for e in entries.values():
        if e.get("outcome") == "ghosted":
            r = e.get("ghost_reason", "no_response")
            gr_stats[r] = gr_stats.get(r, 0) + 1
    if gr_stats:
        print("\n  Ghost reasons:")
        for r, count in sorted(gr_stats.items(), key=lambda x: x[1], reverse=True):
            print(f"    {r:<18} {count} ({_pct(count, ghosted)})")

    # Breakdown by niche (if --by-niche flag)
    if by_niche:
        niche_stats: dict[str, dict[str, int]] = {}
        for e in entries.values():
            n = e.get("niche", "unknown") or "unknown"
            if n not in niche_stats:
                niche_stats[n] = {"sent": 0, "responded": 0, "converted": 0}
            niche_stats[n]["sent"] += 1
            if e.get("outcome") and e["outcome"] != "ghosted":
                niche_stats[n]["responded"] += 1
            if e.get("deal_value") is not None:
                niche_stats[n]["converted"] += 1

        print("\n  By niche:")
        for n, s in sorted(
            niche_stats.items(), key=lambda x: x[1]["converted"], reverse=True
        ):
            print(
                f"    {n:<20} {s['sent']} sent, {s['responded']} responded, {s['converted']} converted ({_pct(s['converted'], s['sent'])})"
            )

    # Breakdown by city (if --by-city flag)
    if by_city:
        city_stats: dict[str, dict[str, int]] = {}
        city_map: dict[str, str] = {}
        for lead in all_leads:
            city_map[lead["key"]] = lead.get("city", "unknown") or "unknown"

        for key, e in entries.items():
            city = city_map.get(key, "unknown")
            if city not in city_stats:
                city_stats[city] = {"sent": 0, "responded": 0, "converted": 0}
            city_stats[city]["sent"] += 1
            if e.get("outcome") and e["outcome"] != "ghosted":
                city_stats[city]["responded"] += 1
            if e.get("deal_value") is not None:
                city_stats[city]["converted"] += 1

        print("\n  By city:")
        for c, s in sorted(
            city_stats.items(), key=lambda x: x[1]["sent"], reverse=True
        ):
            print(
                f"    {c:<20} {s['sent']} sent, {s['responded']} responded, {s['converted']} converted ({_pct(s['converted'], s['sent'])})"
            )


def cmd_list(status_filter: str | None, leads_dir: Path) -> None:
    """List leads filtered by status."""
    status = load_status(leads_dir)
    all_leads = scan_all_leads(leads_dir)

    results: list[tuple[str, str, str, int]] = []

    for lead in all_leads:
        key = lead["key"]
        s = get_lead_status(status, key)
        if status_filter and s != status_filter:
            continue
        # For pending without filter, skip (too many)
        if not status_filter and s == "pending":
            continue
        name = lead.get("name", lead["name"])
        score = lead.get("_score", 0)
        results.append((key, s, name, score))

    if not results:
        if status_filter:
            print(f"No leads with status '{status_filter}'.")
        else:
            print("No contacted leads.")
        return

    # Sort by score descending
    results.sort(key=lambda x: x[3], reverse=True)

    print(f"\n{'Key':<45} {'Status':<12} {'Score':<6} {'Name'}")
    print(f"{'─' * 45} {'─' * 12} {'─' * 6} {'─' * 30}")

    for key, s, name, score in results:
        print(f"{_trunc(key, 44):<45} {s:<12} {score:<6} {_trunc(name, 30)}")

    print(f"\nTotal: {len(results)}")


def cmd_open(lead_ref: str, leads_dir: Path) -> None:
    """Open outreach HTML in browser."""
    lead_path = resolve_lead(lead_ref, leads_dir)
    if not lead_path:
        _lead_not_found(lead_ref, leads_dir)
        return

    # Find outreach HTML
    html_files = list(lead_path.glob("*-outreach.html"))
    if not html_files:
        # Also check for OUTREACH.md as fallback info
        outreach_md = lead_path / "OUTREACH.md"
        if outreach_md.exists():
            print("No outreach HTML file. OUTREACH.md exists:")
            print(f"  {outreach_md}")
            print("Generate outreach HTML first.")
        else:
            print(f"No outreach files in {lead_path.name}/")
            print("Available files:")
            for f in sorted(lead_path.iterdir()):
                if f.is_file():
                    print(f"  {f.name}")
        return

    target = html_files[0]
    print(f"Opening: {target.name}")
    subprocess.run(["open", str(target)], check=False)


def cmd_sent(
    leads_dir: Path,
    date_filter: str = "today",
    channel: str = "whatsapp",
    hook: str = "quote",
) -> None:
    """Mark all 'ready' leads as contacted (batch operation)."""
    status = load_status(leads_dir)
    today = _today_iso()
    target_date = today if date_filter == "today" else date_filter

    count = 0
    for key, entry in status["leads"].items():
        if get_lead_status(status, key) != "ready":
            continue
        if target_date and entry.get("ready_date") != target_date:
            continue
        entry["contacted_date"] = today
        entry["channel"] = channel
        entry["hook"] = hook
        count += 1

    save_status(leads_dir, status)
    print(f"OK: {count} leads marked as contacted ({channel}, hook={hook}).")
    if count > 0:
        print(f"    Follow-up 1 due: {_date_plus(today, 2)}")


def cmd_open_batch(n: int, leads_dir: Path, due_only: bool = False) -> None:
    """Open N outreach HTMLs at once for assembly-line sending."""
    status = load_status(leads_dir)
    targets: list[str] = []

    if due_only:
        today_dt = _today_dt()
        for key, entry in status["leads"].items():
            if entry.get("outcome"):
                continue
            contacted = entry.get("contacted_date")
            if not contacted:
                continue
            contacted_dt = datetime.strptime(contacted, "%Y-%m-%d").date()
            sent_types = {f["type"] for f in entry.get("followups", [])}
            for fu_type, fu_days in FOLLOWUP_SCHEDULE.items():
                if fu_type not in sent_types:
                    due_date = contacted_dt + timedelta(days=fu_days)
                    if (due_date - today_dt).days <= 0:
                        targets.append(key)
                    break
    else:
        for key in status["leads"]:
            if get_lead_status(status, key) == "ready":
                targets.append(key)

    if not targets:
        hint = " (try without --due)" if due_only else ""
        print(f"No leads to open.{hint}")
        return

    batch = targets[:n]
    opened = 0
    for key in batch:
        lead_path = leads_dir / key
        html_files = list(lead_path.glob("*-outreach.html"))
        if html_files:
            subprocess.run(["open", str(html_files[0])], check=False)
            opened += 1

    remaining = len(targets) - n
    print(f"Opened {opened} outreach files. {max(0, remaining)} remaining.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _today_dt():
    return datetime.now(timezone.utc).date()


def _date_plus(date_str: str, days: int) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d").date()
    return (dt + timedelta(days=days)).isoformat()


def _pct(part: int, whole: int) -> str:
    if whole == 0:
        return "0.0%"
    return f"{part / whole * 100:.1f}%"


def _trunc(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    return s[: max_len - 2] + ".."


def _short_key(key: str) -> str:
    """HOT/001_VULKANIZER_SAŠA_81pts -> HOT/001 VULKANIZER SAŠA"""
    parts = key.split("/", 1)
    if len(parts) < 2:
        return key
    cat = parts[0]
    name = parts[1]
    # Remove score suffix
    name = SCORE_RE.sub("", name)
    # Replace underscores with spaces, collapse multiple
    name = re.sub(r"_+", " ", name).strip()
    return f"{cat}/{name}"


def _lead_not_found(ref: str, leads_dir: Path) -> None:
    print(f"ERROR: Lead '{ref}' not found.")
    suggestions = suggest_similar(ref, leads_dir)
    if suggestions:
        print("Did you mean:")
        for s in suggestions:
            print(f"  {s}")


GHOST_DAYS = 7  # Days without response before auto-ghosting


def cmd_auto_ghost(leads_dir: Path, dry_run: bool = False) -> None:
    """Auto-mark contacted leads as ghosted after GHOST_DAYS without response."""
    status = load_status(leads_dir)
    today = datetime.now(timezone.utc).date()
    ghosted: list[str] = []

    for key, entry in status.get("leads", {}).items():
        if entry.get("outcome"):
            continue  # already has outcome
        contacted = entry.get("contacted_date")
        if not contacted:
            continue
        contacted_date = (
            datetime.fromisoformat(contacted).date()
            if "T" in contacted
            else datetime.strptime(contacted, "%Y-%m-%d").date()
        )
        days_since = (today - contacted_date).days
        if days_since >= GHOST_DAYS:
            if not dry_run:
                entry["outcome"] = "ghosted"
                entry["ghost_reason"] = "no_response"
                entry["response_date"] = today.isoformat()
            ghosted.append(
                f"  {'[DRY] ' if dry_run else ''}Ghosted ({days_since}d): {key}"
            )

    if not dry_run and ghosted:
        save_status(leads_dir, status)

    if ghosted:
        print("\n".join(ghosted))
    print(
        f"\nTotal: {len(ghosted)} leads {'would be ' if dry_run else ''}auto-ghosted (>{GHOST_DAYS} days)"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Money Maker Outreach Pipeline CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--leads-dir", help="Override leads directory path")

    sub = parser.add_subparsers(dest="command", help="Command")

    # next
    p_next = sub.add_parser("next", help="Top N uncontacted by score")
    p_next.add_argument(
        "n", nargs="?", type=int, default=5, help="Number of leads (default: 5)"
    )

    # contact
    p_contact = sub.add_parser("contact", help="Log outreach contact")
    p_contact.add_argument("lead", help="Lead reference (HOT/004, 004, or full name)")
    p_contact.add_argument("--channel", required=True, choices=VALID_CHANNELS)
    p_contact.add_argument("--hook", required=True, choices=VALID_HOOKS)
    p_contact.add_argument("--notes", default="", help="Additional notes")
    p_contact.add_argument(
        "--force", action="store_true", help="Override already contacted lead"
    )

    # followup
    p_fu = sub.add_parser("followup", help="Log sent follow-up")
    p_fu.add_argument("lead", help="Lead reference")
    p_fu.add_argument(
        "--type", required=True, choices=list(FOLLOWUP_SCHEDULE.keys()), dest="fu_type"
    )
    p_fu.add_argument("--channel", required=True, choices=VALID_CHANNELS)

    # respond
    p_resp = sub.add_parser("respond", help="Log response")
    p_resp.add_argument("lead", help="Lead reference")
    p_resp.add_argument("--outcome", required=True, choices=VALID_OUTCOMES)
    p_resp.add_argument(
        "--reason",
        default="",
        choices=VALID_GHOST_REASONS,
        help="Ghost reason (only for ghosted)",
    )
    p_resp.add_argument("--notes", default="", help="Additional notes")

    # convert
    p_conv = sub.add_parser("convert", help="Log conversion")
    p_conv.add_argument("lead", help="Lead reference")
    p_conv.add_argument("--deal", required=True, type=int, help="Deal value (EUR)")
    p_conv.add_argument(
        "--type",
        default="site",
        choices=["site", "site+maintenance"],
        dest="deal_type",
        help="Deal type",
    )
    p_conv.add_argument("--notes", default="", help="Additional notes")

    # due
    sub.add_parser("due", help="Follow-ups due today")

    # stats
    p_stats = sub.add_parser("stats", help="Funnel statistics")
    p_stats.add_argument("--last", type=int, help="Only last N days")
    p_stats.add_argument(
        "--by-niche", action="store_true", help="Show breakdown by niche"
    )
    p_stats.add_argument(
        "--by-score", action="store_true", help="Show breakdown by score range"
    )
    p_stats.add_argument(
        "--by-city", action="store_true", help="Show breakdown by city"
    )

    # list
    p_list = sub.add_parser("list", help="List leads by status")
    p_list.add_argument("--status", choices=VALID_STATUSES, help="Filter by status")

    # open
    p_open = sub.add_parser("open", help="Open outreach HTML")
    p_open.add_argument("lead", help="Lead reference")

    # sent
    p_sent = sub.add_parser("sent", help="Mark ready leads as contacted")
    p_sent.add_argument(
        "date", nargs="?", default="today", help="Date filter: 'today' or YYYY-MM-DD"
    )
    p_sent.add_argument("--channel", default="whatsapp", choices=VALID_CHANNELS)
    p_sent.add_argument("--hook", default="quote", choices=VALID_HOOKS)

    # open-batch
    p_batch = sub.add_parser("open-batch", help="Open N outreach HTMLs at once")
    p_batch.add_argument(
        "n", nargs="?", type=int, default=5, help="Number of leads (default: 5)"
    )
    p_batch.add_argument(
        "--due", action="store_true", help="Open due follow-ups instead of ready leads"
    )

    # auto-ghost
    p_ghost = sub.add_parser(
        "auto-ghost", help=f"Auto-ghost leads contacted >{GHOST_DAYS} days ago"
    )
    p_ghost.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be ghosted without changing",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    leads_dir = find_leads_dir(getattr(args, "leads_dir", None))

    if args.command == "next":
        cmd_next(args.n, leads_dir)
    elif args.command == "contact":
        cmd_contact(
            args.lead, args.channel, args.hook, leads_dir, args.notes, args.force
        )
    elif args.command == "followup":
        cmd_followup(args.lead, args.fu_type, args.channel, leads_dir)
    elif args.command == "respond":
        cmd_respond(args.lead, args.outcome, leads_dir, args.notes, args.reason)
    elif args.command == "convert":
        cmd_convert(args.lead, args.deal, leads_dir, args.notes, args.deal_type)
    elif args.command == "due":
        cmd_due(leads_dir)
    elif args.command == "stats":
        cmd_stats(leads_dir, args.last, args.by_niche, args.by_score, args.by_city)
    elif args.command == "list":
        cmd_list(args.status, leads_dir)
    elif args.command == "open":
        cmd_open(args.lead, leads_dir)
    elif args.command == "sent":
        cmd_sent(leads_dir, args.date, args.channel, args.hook)
    elif args.command == "open-batch":
        cmd_open_batch(args.n, leads_dir, args.due)
    elif args.command == "auto-ghost":
        cmd_auto_ghost(leads_dir, args.dry_run)
    else:
        parser.print_help()

    return 0


if __name__ == "__main__":
    sys.exit(main())
