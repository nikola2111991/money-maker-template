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
from prompt_rules import format_rules

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
- hero_headline: Short, powerful headline (max 10 words). Answer the question this business's CUSTOMER would type into Google. Write from the customer's perspective, not as praise for the owner.
- hero_subtitle: One sentence expanding on the headline. Include the city name.
- about_headline: Headline for the About page (max 8 words). Reference years in business, review count, or specialization.
- about_subtitle: One sentence summarizing the business for the About page.
- about_story: 2-3 paragraphs. Start with a specific moment: first job, a problem they solved, a turning point. Use specific details from reviews and services. Make it feel authentic. Never start with "Founded in", "Established in", or "With X years of experience". Never end a paragraph with a generic forward-looking statement.
- about_blockquote: Paraphrase the best customer review. Use the customer's voice, not the owner's. If no reviews provided, describe a scene that captures what makes this business different.
- core_values: Array of exactly 3 objects with "title", "description", and "ikona" fields. ikona must be one of: "heart", "clock", "check", "shield". Each value should reference something concrete from customer reviews.
- about_stats: Array of 2-4 objects with "value" (number as string) and "label" fields. Use real data: founding year, rating, review count, years in business.
- benefits_headline: Why clients choose this business (max 8 words)
- services_subtitle: One sentence about their service range
- contact_subtitle: One sentence encouraging contact. Reference something specific like working hours or response time. Never use "don't hesitate" or "feel free".
- faq: Array of 3 objects with "question" and "answer" fields. Questions real customers would ask. Each answer: one concrete sentence, then a call to action (e.g. "Call us to find out.", "Book a free quote.").

Rules:
- Be specific. Use real details from the business data.
- No generic marketing fluff. Every sentence should reference something concrete.
- Use the tone natural for {language_name} business communication.
{format_rules(playbook)}
- Return ONLY valid JSON, no markdown formatting."""


def _outreach_system_prompt(
    niche: str, language_name: str, booking_url: str = "", playbook: dict | None = None
) -> str:
    booking = booking_url or CAL_COM_URL
    return f"""You are an outreach copywriter for a web development service targeting {niche} businesses. Write in {language_name}.

Why this business needs a website (use the most relevant pain points in messages):
- Customers search Google before calling. No website = invisible to new customers.
- Competitors with websites get the calls they're missing.
- Their great reviews are buried on Google Maps. A website puts reviews front and center.
- A website builds trust: customers see services, prices, photos before calling. More qualified leads, less tire-kickers.

You generate outreach messages as a JSON object with these fields:
- whatsapp_initial: WhatsApp/Viber message (max 3 sentences). Structure: (1) Pain point: they have great reviews but no website, so customers can't find them on Google. (2) You built a demo based on their reviews. (3) Demo link. Sign as "Nikola". Never say "I built you a website". Say "I noticed you have X reviews but when someone searches for your service they can't find you".
- email_subject: Email subject line (max 8 words). Reference the pain point (invisibility), not the solution.
- email_initial: Email body (max 6 sentences). (1) You noticed they have X reviews but no website. (2) Customers searching can't find them. (3) You built a demo based on their reviews. (4) Quote a specific review by name. (5) Demo link. (6) No obligation.
- followup_1: Follow-up message for Day 2 (max 3 sentences). Different angle: mention competitors who have websites. Brief and respectful.

Rules:
- Structure every message as: PAIN POINT → PROOF (you researched them) → SOLUTION (demo site). Never lead with the solution.
- NEVER mention price or cost. Price is discussed only on a call.
- Quote or reference a specific customer review by reviewer name. This proves you actually researched their business, not mass-sending.
- Build trust through specificity, not claims.
- No generic templates. Every message must feel personalized.
{format_rules(playbook)}
- Keep booking link as: {booking}
- One follow-up only. If no response after follow-up, move on.
- Sign messages as "Nikola" (the service provider).
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
        max_tokens=1500,
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
