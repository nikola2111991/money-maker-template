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

OUTREACH_RULES_TEMPLATE: str = """\
CRITICAL TONE RULE:
- Write like a real person texting, NOT a marketing system.
- Sound like you genuinely looked at their business and found it interesting.
- Use casual connectors: "came across", "noticed", "stood out", "caught my eye".
- NEVER sound like a template with filled-in fields. If it reads like mail merge, rewrite it.
- Read your message aloud. If it sounds like an ad, it is wrong.

MESSAGE FLOW (applies to both WhatsApp and Email):
- Every message MUST follow this logical flow. No skipping steps:
  1. HOW you found them: "Was looking at {{niche}}s in {{city}} on Google Maps and saw your business."
  2. WHAT caught your eye: A specific review, their rating, a service they offer.
  3. THE GAP: What they are missing (no website, outdated site, no online presence). This is the bridge that explains WHY you made them a site.
  4. WHAT you did: "I put together a quick site for you using your real reviews and photos."
  5. CTA: Low-pressure, casual.
- WITHOUT step 3 (the gap), the message makes no sense. "Your review is great, here is a website" has no logic. "Your review is great but you have no website to show it on, so I made one" does.

EMAIL (Day 0):
- Max {max_words_email} words.
- Follow the 5-step message flow above. Add P.S. with strongest proof point.
- Sign: "Nikola".
- P.S. options:
  "P.S. {{Competitor}} launched their site last month. They now show up first when someone searches '{{niche}} {{city}}'."
  Or: "P.S. {{N}} of your {{total}} reviews mention {{top_keyword}}. That would make a strong homepage headline."

WHATSAPP (Day 0):
- Max 4 sentences. Sound like a real text message, not a pitch.
- Follow the 5-step message flow above, but keep it tight.
- Example flow: "Hey Todd, was looking at tilers in Perth on Google Maps and saw your business. Jayden Chatfield's review about the screeding and waterproofing caught my eye. Looks like you are doing great work but I noticed you do not have a website, so I put together a quick site using your reviews and photos: {{URL}}. Check it out and let me know what you think."
- Separate each sentence with a blank line for readability.
- Sign: "Nikola".

SUBJECT LINE:
- Max 8 words. Curiosity gap.
- Patterns: "{{Owner}}, noticed something about your competitors" / "{{N}} of {{total}} {{niche}}s in {{city}} have this" / "{{Reviewer}} wrote something about {{Business}}"
- BANNED in subject: business name alone, "website", "demo", "Quick question", "Following up".

STEP 2 DETAIL (what caught your eye, pick ONE per lead):
- A specific review: mention reviewer name + what they said using trade terms. "Jayden Chatfield's review about the screeding and waterproofing caught my eye."
- Their rating or review count: "5.0 with 42 reviews stood out."
- Years in business: "12 years of tiling with no website is a lot of great work going unseen."
- A specific service: "Your waterproofing and bathroom renovations deserve their own page."
- NEVER: "your reviews are great", "based on your X reviews". Be specific, not generic.
- Use trade terms from reviews: "waterproofed and retiled full ensuite" not "did bathroom work".

CTA (rotate per lead, keep casual and low-pressure):
1. "Check it out and let me know what you think."
2. "Have a look when you get a sec."
3. "Worth a look?"
4. "Happy to jump on a quick call if easier."

DATA TO USE (from scraper output, use ALL available data):
- competitor_report.json: use NAMED competitor + their review count + website URL. Good for step 3 (the gap).
- review_analysis.json: use top_keywords. Good for P.S. line.
- reviews[]: pick best review for step 2 (what caught your eye). Quote reviewer by name + specific work.
- review_velocity: if high, good for P.S. or follow-up.
- premium_location: if true, "Homeowners in {{area}} search online before calling."
- years_in_business: weave into step 2 or step 3 naturally.
- services: mention specific services, not "your business."
- facebook/instagram: use for step 3 (the gap) if they have social but no website.

FOLLOW-UPS (keep casual, sound like a real person checking in):
- Day 2 (followup_1): "Hey {{Owner}}, just checking if you had a chance to look at that site I sent. Your competitor {{competitor}} has {{N}} reviews and a full website. Worth comparing."
- Day 4 (followup_2): Share something useful, not salesy. "Thought you might find this interesting: {{N}} of your customers mentioned {{keyword}}. That would make a strong homepage headline."
- Day 6 (followup_3): Max 2 sentences. Casual close. "No stress if it is not for you. I am moving on to other {{niche}}s in {{city}} next week, just wanted to give you first look."

OUTREACH ANGLE (use as guidance, weave naturally into message):
- No website: They are invisible on Google. Competitors with sites get the search traffic.
- Template/bad website: Their site looks outdated or generic. Customers leave quickly.
- Has website but weak: Site exists but has no reviews, no photos, no real content.
- Long tenure + no website: Years of great work with no online presence to show for it.
- Has Instagram but inactive: Social presence exists but is dormant. Customers notice.
- Has Facebook but no website: Social following with nowhere to send them.

BANNED:
- "if you're interested", "no pressure", "feel free", "no obligation", "don't hesitate"
- "There is a demo at", "I built you a website/demo", "I created this for you"
- "I noticed that", "I wanted to reach out" (too corporate)
- "Reply 'interested'", "Reply 'yes'" (sounds like a bot)
- Em dashes, emojis, AI phrases, prices
- Same structure for 3+ leads in a row\
"""

SUBJECT_LINE_RULES: str = """\
- Max 8 words. Curiosity gap.
- Patterns: curiosity / proof / review reference.
- BANNED: business name alone, "website", "demo", "Following up", "Quick question".\
"""

SITE_COPY_RULES: str = """\
HERO:
- hero_headline: Max 10 words. Answer the question the customer would type into Google.
- Write from the customer's perspective, not praise for the owner.
- If top_keywords available, use the most frequent keyword in the headline.
- hero_subtitle: One sentence expanding on headline. MUST include city name.

ABOUT:
- about_headline: Max 8 words. Reference years in business, review count, or specialization.
- about_subtitle: One sentence summarizing the business.
- about_story: 2-3 paragraphs. Start with a specific moment: first job, a problem solved, a turning point.
  Use specific details from reviews and services. Make it feel authentic.
  NEVER start with "Founded in", "Established in", or "With X years of experience".
  NEVER end a paragraph with a generic forward-looking statement.
- about_blockquote: Paraphrase the best customer review using trade terminology.
  Use the customer's voice, not the owner's. Include reviewer name.
- about_stats: Array of 2-4 objects with "value" (string) and "label".
  Use REAL data: founding year, rating, review count, years in business.
  If review_velocity available, add as stat.
  If competitor avg_rating available and lead's rating is higher, add "Above {city} average".

CORE VALUES:
- Exactly 3 objects with "title", "description", and "ikona" fields.
- ikona must be one of: "heart", "clock", "check", "shield".
- Each description MUST be at least 10 words and reference something concrete from customer reviews.
- Use trust_signals from niche_intelligence for titles when available.

BENEFITS:
- benefits_headline: Why clients choose this business (max 8 words).
- Each benefit should address a customer_pain_point from niche_intelligence.

SERVICES:
- services_subtitle: One sentence about their service range.
- Use trade terminology from playbook trade_terms in all service descriptions.

FAQ:
- Array of 3-5 objects with "question" and "answer".
- Questions real customers would ask. Each answer: one concrete sentence, then CTA.
- MUST include at least 1 pricing question ("How much does {service} cost in {city}?").
  Use pricing_ranges from niche_intelligence for realistic answer ranges.
- MUST address customer_deal_breakers from niche_intelligence.
- Include faq_must_include questions from niche_intelligence.

CONTACT:
- contact_subtitle: One sentence encouraging contact.
  MUST include owner's first name: "Call {owner_short}" not "Call us".
  NEVER "don't hesitate", "feel free", "no obligation".

SERVICE AREA:
- service_area: "{city} and surrounding areas including {3-5 nearby suburbs}".
  Use suburbs from playbook service_areas.
  If premium_location, emphasize the premium area.

IMAGES:
- Hero image: ALWAYS use local photo from photos/ directory. NEVER Unsplash placeholder.
- Service/benefit images: local photos first, then playbook image_map, then cycle_images.

SCARCITY:
- If review_velocity is high (2+ per month), add urgency: "Currently booking {N} weeks out."
- If premium_location, mention area demand: "High demand in {area}. Book early."

FAQ QUALITY:
- Each answer: max 2 sentences. One concrete fact with number/timeframe/price, one CTA.
- Never generic answers. Every answer must contain a specific number, timeframe, or price range.

COPY PROGRESSION:
- Hero = confidence (rating + search-intent headline).
- About = connection (specific moment + review quote).
- FAQ = reassurance (deal breakers answered with concrete data).
- CTA = action (owner name + urgency signal).

GENERAL:
- Be specific. Use real details from the business data.
- No generic marketing fluff. Every sentence should reference something concrete.
- Warranty and certifications must be prominently displayed when available.
- Never empty <p></p> tags. Every text element must have real content.\
"""

FOLLOWUP_ANGLES: dict[str, str] = {
    "followup_1": "competitor_comparison",
    "followup_2": "industry_insight",
    "followup_3": "casual_close",
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
        max_words = 80
        if playbook:
            max_words = playbook.get("outreach", {}).get("max_words_email", 80)
        base += "\n" + OUTREACH_RULES_TEMPLATE.format(max_words_email=max_words)
    elif context == "site":
        base += f"\n{SITE_COPY_RULES}"
    elif context == "subject":
        base += f"\n{SUBJECT_LINE_RULES}"
    return base


# Lowercase set for fast validation lookups
BANNED_WORDS_SET: set[str] = {w.lower() for w in BANNED_WORDS}
