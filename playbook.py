"""
playbook.py - Load, validate, and list niche playbooks.

A playbook is a JSON file that defines everything niche-specific:
search queries, cities, phone formats, i18n strings, image maps, etc.
The engine reads a playbook and adapts all behavior accordingly.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from config import PLAYBOOK_DIR

REQUIRED_FIELDS: list[str] = [
    "niche", "country_code", "language", "currency",
    "phone_prefix", "cities", "search_queries", "schema_type",
]

REQUIRED_I18N: list[str] = [
    "lang", "locale", "cta_call", "cta_whatsapp", "footer_rights",
]


def load_playbook(niche: str, country: str) -> dict[str, Any]:
    """Load playbook from playbooks/{niche}-{country}.json."""
    filename = f"{niche}-{country.lower()}.json"
    path = PLAYBOOK_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Playbook not found: {path}")
    with open(path, encoding="utf-8") as f:
        pb = json.load(f)
    errors = validate_playbook(pb)
    if errors:
        raise ValueError(f"Invalid playbook {filename}: {'; '.join(errors)}")
    return pb


def load_playbook_from_path(path: str | Path) -> dict[str, Any]:
    """Load playbook from an arbitrary path."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Playbook not found: {path}")
    with open(path, encoding="utf-8") as f:
        pb = json.load(f)
    errors = validate_playbook(pb)
    if errors:
        raise ValueError(f"Invalid playbook {path.name}: {'; '.join(errors)}")
    return pb


def validate_playbook(pb: dict[str, Any]) -> list[str]:
    """Validate playbook has all required fields. Returns list of errors."""
    errors: list[str] = []
    for field in REQUIRED_FIELDS:
        if not pb.get(field):
            errors.append(f"missing required field: {field}")
    i18n = pb.get("i18n", {})
    if not i18n:
        errors.append("missing i18n section")
    else:
        for field in REQUIRED_I18N:
            if not i18n.get(field):
                errors.append(f"missing i18n.{field}")
    nav = i18n.get("nav", {})
    for label in ("home", "services", "about", "contact"):
        if not nav.get(label):
            errors.append(f"missing i18n.nav.{label}")
    return errors


def list_playbooks() -> list[dict[str, str]]:
    """List all available playbooks with niche and country."""
    results: list[dict[str, str]] = []
    if not PLAYBOOK_DIR.exists():
        return results
    for path in sorted(PLAYBOOK_DIR.glob("*.json")):
        if path.name.startswith("_"):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                pb = json.load(f)
            results.append({
                "file": path.name,
                "niche": pb.get("niche", ""),
                "country": pb.get("country_code", ""),
                "language": pb.get("language", ""),
            })
        except (json.JSONDecodeError, OSError):
            continue
    return results
