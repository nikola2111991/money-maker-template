"""Shared prompt rules for AI copy generation.

Used by copy_generator.py (API) and enrich.py (CLI) to ensure
consistent tone, banned words, and formatting rules across all
AI-generated copy.
"""

from __future__ import annotations

BANNED_WORDS: list[str] = [
    "leverage",
    "streamline",
    "elevate",
    "utilize",
    "comprehensive",
    "cutting-edge",
    "state-of-the-art",
    "premier",
    "solutions",
    "dedicated to excellence",
    "passion for",
    "committed to",
    "trusted partner",
]

TONE_RULES: str = """\
- Never use em dashes. Use commas, colons, or periods instead.
- Never mention prices.
- Vary sentence length. Mix short (3-5 words) with medium (8-12). Never three same-length sentences in a row.
- Write like talking to someone in person. If you wouldn't say it out loud, don't write it.
- Prefer customer quotes over original writing. Paraphrase reviews, don't invent.
- Describe scenes, not features. "Monday: old tiles. Thursday: new bathroom." not "We provide fast tiling services."
- Never start three consecutive items with the same word or structure.\
"""

BANNED_WORDS_RULE: str = f"- Banned words: {', '.join(BANNED_WORDS)}."


def trade_rules(playbook: dict | None = None) -> str:
    """Return trade terminology rule from playbook if available."""
    if not playbook:
        return ""
    terms = playbook.get("trade_terms", [])
    if not terms:
        return ""
    examples = ", ".join(terms[:10])
    return (
        f"\n- Use correct trade terminology for this niche. "
        f"Key terms: {examples}. "
        f"When referencing a review, comment on the specific work mentioned "
        f"using these terms. Show you understand what the work involves."
    )


def format_rules(playbook: dict | None = None) -> str:
    """Return combined rules string for embedding in prompts."""
    return f"{TONE_RULES}\n{BANNED_WORDS_RULE}{trade_rules(playbook)}"


# Lowercase set for fast validation lookups
BANNED_WORDS_SET: set[str] = {w.lower() for w in BANNED_WORDS}
