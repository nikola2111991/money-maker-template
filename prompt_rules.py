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

OUTREACH_RULES: str = """\
- Email max 80 words, WhatsApp max 3 sentences.
- Economic angle required: lead with "competitors with websites get Google traffic you are missing".
- Must contain 1 competitor stat from competitor_report (e.g. "{X} of {Y} {niche}s in {city} have a website").
- Micro-commitment CTA: "Reply 'interested'" not "happy to jump on a call".
- Banned phrases: "if you're interested", "no pressure", "happy to", "feel free", "no obligation", "don't hesitate".
- Two angles (auto-select based on data.json website field):
  - No website: "Competitors with websites get the customers who search Google. You are missing them."
  - Bad/template website: "Your site looks like a template. Customers close it in 3 seconds."\
"""

SITE_COPY_RULES: str = """\
- core_values descriptions min 10 words each, must reference a concrete review.
- FAQ must include at least 1 high-intent pricing question ("How much does {service} cost in {city}?").
- CTA must use owner name: "Call {owner_short}" not "Call us".
- service_area required: "{city} and surrounding areas including {suburbs}".
- Never empty <p></p> tags. Every text element must have real content.
- Warranty and certifications must be prominently displayed when available in niche_intelligence.\
"""

FOLLOWUP_ANGLES: dict[str, str] = {
    "followup_1": "competitor_comparison",
    "followup_2": "social_proof",
    "followup_3": "urgency_scarcity",
}


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


def _niche_intelligence_rules(playbook: dict | None = None) -> str:
    """Return niche intelligence context from playbook if available."""
    if not playbook:
        return ""
    ni = playbook.get("niche_intelligence", {})
    if not ni:
        return ""
    parts: list[str] = []
    if ni.get("owner_real_problem"):
        parts.append(f"\n- Owner's real problem: {ni['owner_real_problem']}")
    if ni.get("customer_deal_breakers"):
        parts.append(
            f"- Customer deal breakers: {', '.join(ni['customer_deal_breakers'])}"
        )
    if ni.get("required_certifications"):
        parts.append(
            f"- Required certifications: {', '.join(ni['required_certifications'])}"
        )
    if ni.get("warranty_standard"):
        parts.append(f"- Warranty standard: {ni['warranty_standard']}")
    if ni.get("pricing_format"):
        parts.append(f"- Pricing format: {ni['pricing_format']}")
    if ni.get("faq_must_include"):
        parts.append(
            f"- FAQ must include: {'; '.join(ni['faq_must_include'][:5])}"
        )
    if ni.get("outreach_angle"):
        parts.append(f"- Outreach angle: {ni['outreach_angle']}")
    if ni.get("terminology_corrections"):
        corrections = [
            f"'{k}' → '{v}'" for k, v in ni["terminology_corrections"].items()
        ]
        parts.append(f"- Terminology: {'; '.join(corrections[:5])}")
    return "\n".join(parts)


def format_rules(playbook: dict | None = None, context: str = "") -> str:
    """Return combined rules string for embedding in prompts.

    Args:
        playbook: Playbook dict for trade terms and niche intelligence.
        context: "site" for site copy rules, "outreach" for outreach rules,
                 empty for base rules only.
    """
    base = f"{TONE_RULES}\n{BANNED_WORDS_RULE}{trade_rules(playbook)}"
    ni = _niche_intelligence_rules(playbook)
    if ni:
        base += f"\n{ni}"
    if context == "outreach":
        base += f"\n{OUTREACH_RULES}"
    elif context == "site":
        base += f"\n{SITE_COPY_RULES}"
    return base


# Lowercase set for fast validation lookups
BANNED_WORDS_SET: set[str] = {w.lower() for w in BANNED_WORDS}
