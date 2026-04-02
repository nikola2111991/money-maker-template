"""copy_generator.py - AI-powered copy generation for Money Maker sites and outreach.

Uses Claude API to generate:
1. Site copy: hero headline/subtitle, about story, benefits, FAQ
2. Outreach messages: initial contact + 1 follow-up

All copy is generated in the language specified by the playbook.
Uses prompt caching: system prompt is cached across sequential calls (90% cheaper).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from anthropic import Anthropic, APIError, APITimeoutError, RateLimitError

from config import CAL_COM_URL
from enrich import validate_enriched
from prompt_rules import format_rules, FOLLOWUP_ANGLES, BANNED_WORDS_SET

log = logging.getLogger(__name__)


def _call_with_retry(client: Anthropic, max_retries: int = 3, **kwargs) -> Any:
    """Call Claude API with exponential backoff retry on transient errors."""
    for attempt in range(max_retries + 1):
        try:
            return client.messages.create(**kwargs)
        except (RateLimitError, APITimeoutError) as e:
            if attempt == max_retries:
                raise
            wait = 2**attempt + 1  # 2s, 3s, 5s
            log.warning(
                "  API %s, retry %d/%d in %ds...",
                type(e).__name__,
                attempt + 1,
                max_retries,
                wait,
            )
            time.sleep(wait)
        except APIError as e:
            if e.status_code and e.status_code >= 500 and attempt < max_retries:
                wait = 2**attempt + 1
                log.warning(
                    "  API %s, retry %d/%d in %ds...",
                    e.status_code,
                    attempt + 1,
                    max_retries,
                    wait,
                )
                time.sleep(wait)
            else:
                raise


# System prompts (cached across calls for same niche/language)


def _site_system_prompt(
    niche: str, language: str, language_name: str, playbook: dict | None = None
) -> str:
    return f"""You are a website copywriter for {niche} businesses. Write in {language_name}.

You generate website copy as a JSON object with these fields:
- hero_headline, hero_subtitle
- about_headline, about_subtitle, about_story, about_blockquote
- core_values (array of 3: title, description, ikona)
- about_stats (array of 2-4: value, label)
- benefits_headline
- services_subtitle
- contact_subtitle
- service_area
- faq (array of 3-5: question, answer)

{format_rules(playbook, context="site")}
- Use the tone natural for {language_name} business communication.
- Return ONLY valid JSON, no markdown formatting."""


def _outreach_system_prompt(
    niche: str, language_name: str, booking_url: str = "", playbook: dict | None = None
) -> str:
    booking = booking_url or CAL_COM_URL

    return f"""You are an outreach copywriter for a web development service targeting {niche} businesses. Write in {language_name}.

You generate outreach messages as a JSON object with these fields:
- whatsapp_initial, email_subject, email_initial, email_ps, followup_1, followup_2, followup_3

{format_rules(playbook, context="outreach")}
- NEVER mention price or cost.
- Sign all messages as "Nikola".
- Keep booking link as: {booking}
- Return ONLY valid JSON, no markdown formatting."""


def _build_context(lead_data: dict[str, Any]) -> str:
    """Build business context string from lead data."""
    parts = [
        f"Business name: {lead_data.get('name', '')}",
        f"Owner: {lead_data.get('owner', '')}",
        f"City: {lead_data.get('city', '')}",
        f"Rating: {lead_data.get('rating', 0)} ({lead_data.get('review_count', 0)} reviews)",
    ]
    if lead_data.get("specialization"):
        parts.append(f"Specialization: {lead_data['specialization']}")
    if lead_data.get("services"):
        service_names = [s.get("title", "") for s in lead_data["services"][:8]]
        parts.append(f"Services: {', '.join(service_names)}")
    if lead_data.get("reviews"):
        parts.append("\nCustomer reviews:")
        for r in lead_data["reviews"][:3]:
            name = r.get("reviewer_name", "Anonymous")
            text = r.get("text", "")[:500]
            parts.append(f"  Review by {name}: {text}")
    if lead_data.get("years_established"):
        parts.append(f"Years in business: {lead_data['years_established']}")
    if lead_data.get("_review_keywords"):
        parts.append(
            f"What customers praise: {', '.join(lead_data['_review_keywords'][:5])}"
        )
    if lead_data.get("_competitor_report"):
        cr = lead_data["_competitor_report"]
        parts.append("\nCompetitor landscape:")
        parts.append(
            f"  Total in area: {cr.get('total_u_kvartu', cr.get('total_in_area', 0))}"
        )
        parts.append(f"  With website: {cr.get('sa_sajtom', cr.get('with_site', 0))}")
        parts.append(
            f"  Without website: {cr.get('bez_sajta', cr.get('without_site', 0))}"
        )
        parts.append(
            f"  Average rating: {cr.get('prosek_rating', cr.get('avg_rating', 0))}"
        )
    if lead_data.get("_review_velocity"):
        parts.append(f"\nReview velocity: {lead_data['_review_velocity']}")
    if lead_data.get("_premium_location"):
        parts.append("Premium location: Yes")
    if lead_data.get("_service_areas"):
        parts.append(f"Service areas: {', '.join(lead_data['_service_areas'])}")
    if lead_data.get("_has_website"):
        parts.append(f"Has website: {lead_data['_has_website']}")
    return "\n".join(parts)


def _parse_json_response(text: str) -> dict[str, Any]:
    """Parse JSON from Claude response, stripping markdown fences if present."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    return json.loads(text)


def generate_site_copy(
    lead_data: dict[str, Any],
    playbook: dict[str, Any],
    model: str = "claude-sonnet-4-20250514",
) -> dict[str, Any]:
    """Generate site copy for a lead using Claude API.

    Uses system prompt with cache_control for prompt caching.
    When processing multiple leads sequentially, the system prompt
    is cached after the first call (90% cost reduction).
    """
    client = Anthropic()
    language = playbook.get("language", "en")
    niche = playbook.get("niche", "business")
    lang_name = _language_name(language)

    system_prompt = _site_system_prompt(niche, language, lang_name, playbook)
    context = _build_context(lead_data)

    response = _call_with_retry(
        client,
        model=model,
        max_tokens=2000,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": f"Generate website copy for this business:\n\n{context}",
            }
        ],
    )

    usage = response.usage
    log.info(
        "  Site copy tokens: in=%d out=%d cached=%d",
        usage.input_tokens,
        usage.output_tokens,
        getattr(usage, "cache_read_input_tokens", 0),
    )

    result = _parse_json_response(response.content[0].text)

    # Validate Claude output
    result, warnings = validate_enriched(result)
    if warnings:
        log.warning("  Copy validation warnings: %s", "; ".join(warnings))

    # Split about_story into paragraphs
    story = result.get("about_story", "")
    if story and "about_paragraphs" not in result:
        paragraphs = [p.strip() for p in story.split("\n\n") if p.strip()]
        if len(paragraphs) == 1 and len(story) > 200:
            sentences = story.replace(". ", ".\n").split("\n")
            mid = len(sentences) // 2
            paragraphs = [
                " ".join(sentences[:mid]).strip(),
                " ".join(sentences[mid:]).strip(),
            ]
        result["about_paragraphs"] = [p for p in paragraphs if p]

    return result


def generate_outreach(
    lead_data: dict[str, Any],
    playbook: dict[str, Any],
    site_url: str = "",
    model: str = "claude-sonnet-4-20250514",
) -> dict[str, Any]:
    """Generate outreach messages for a lead using Claude API.

    Uses system prompt with cache_control for prompt caching.
    """
    client = Anthropic()
    language = playbook.get("language", "en")
    niche = playbook.get("niche", "business")
    lang_name = _language_name(language)

    booking_url = playbook.get("booking_url", "")
    system_prompt = _outreach_system_prompt(niche, lang_name, booking_url, playbook)
    context = _build_context(lead_data)
    if site_url:
        context += f"\nDemo site URL: {site_url}"

    response = _call_with_retry(
        client,
        model=model,
        max_tokens=2000,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": f"Generate outreach messages for this business owner:\n\n{context}",
            }
        ],
    )

    usage = response.usage
    log.info(
        "  Outreach tokens: in=%d out=%d cached=%d",
        usage.input_tokens,
        usage.output_tokens,
        getattr(usage, "cache_read_input_tokens", 0),
    )

    result = _parse_json_response(response.content[0].text)

    # Check for banned words in outreach fields
    for key in ("whatsapp_initial", "email_initial", "email_subject",
                "email_ps", "followup_1", "followup_2", "followup_3"):
        val = result.get(key, "")
        if isinstance(val, str):
            found = [w for w in BANNED_WORDS_SET if w in val.lower()]
            if found:
                log.warning("  Outreach %s contains banned words: %s", key, found)

    # Validate outreach output
    result, warnings = validate_enriched(result)
    if warnings:
        log.warning("  Outreach validation warnings: %s", "; ".join(warnings))

    result["booking_url"] = f"https://{booking_url or CAL_COM_URL}"
    return result


def _language_name(code: str) -> str:
    """Convert language code to full name."""
    names = {
        "sr": "Serbian",
        "en": "English",
        "de": "German",
        "fr": "French",
        "es": "Spanish",
        "it": "Italian",
        "pt": "Portuguese",
        "nl": "Dutch",
        "pl": "Polish",
        "cs": "Czech",
        "hr": "Croatian",
        "bs": "Bosnian",
        "sl": "Slovenian",
        "ro": "Romanian",
        "hu": "Hungarian",
        "bg": "Bulgarian",
        "el": "Greek",
        "tr": "Turkish",
        "sv": "Swedish",
        "da": "Danish",
        "no": "Norwegian",
        "fi": "Finnish",
    }
    return names.get(code, code)
