"""scoring.py - Shared scoring algorithm for Money Maker leads.

Single source of truth for lead scoring v3.1.
Used by scraper.py (Lead dataclass), research.py (dict), and enrich.py (re-score).

6 Dimensions, max 95 points:
1. REPUTATION (max 20): rating + review count
2. DIGITAL GAP (max 20): website quality + social presence
3. REACHABILITY (max 15): mobile/phone + email + verified
4. BUSINESS SIGNALS (max 18): location, specialty, photos, hours, age, competition
5. ENGAGEMENT (max 10): review quality + recency + known owner
6. URGENCY (max 8): negative reviews + SSL issues

Playbook can override thresholds via "scoring" key.
"""

from __future__ import annotations

from typing import Any

# Default thresholds (playbook can override)
DEFAULT_HOT = 45
DEFAULT_WARM = 28

OWNER_PLACEHOLDERS = {"_POPUNI_ime_vlasnika_", "_POPUNI_Prezime_", ""}


def _calculate(
    rating: float,
    review_count: int,
    website: str,
    site_quality: dict[str, Any],
    facebook: str,
    instagram: str,
    mobile: str,
    phone: str,
    email: str,
    verified: bool,
    district: str,
    address: str,
    premium_locations: list[str],
    specialties: list[str],
    photo_count: int,
    has_hours: bool,
    years_in_business: int,
    negative_flags: list[str],
    avg_review_length: int = 0,
    newest_review_days: int = 999,
    has_owner: bool = False,
    competitor_count: int = 0,
    scoring_config: dict[str, Any] | None = None,
) -> tuple[int, str, dict[str, Any]]:
    """Core scoring logic. Returns (score, category, breakdown)."""
    cfg = scoring_config or {}
    hot = cfg.get("hot_threshold", DEFAULT_HOT)
    warm = cfg.get("warm_threshold", DEFAULT_WARM)

    bd: dict[str, Any] = {}
    t = 0

    # === DIMENSION 1: REPUTATION (max 20) ===
    if rating >= 4.5:
        bd["rating_elite"] = 12
        t += 12
    elif rating >= 4.0:
        bd["rating_strong"] = 9
        t += 9
    elif rating >= 3.5:
        bd["rating_good"] = 6
        t += 6

    if review_count >= 100:
        bd["reviews_100+"] = 8
        t += 8
    elif review_count >= 50:
        bd["reviews_50+"] = 6
        t += 6
    elif review_count >= 20:
        bd["reviews_20+"] = 4
        t += 4
    elif review_count >= 5:
        bd["reviews_5+"] = 2
        t += 2

    # === DIMENSION 2: DIGITAL GAP (max 20) ===
    if not website:
        bd["no_website"] = 15
        t += 15
    elif site_quality.get("is_bad"):
        bd["bad_website"] = 12
        t += 12
        issues = site_quality.get("issues", [])
        if issues:
            bd["site_issues"] = ",".join(issues[:3])
    elif site_quality.get("is_mediocre"):
        bd["mediocre_website"] = 6
        t += 6

    if not facebook:
        bd["no_facebook"] = 3
        t += 3
    if not instagram:
        bd["no_instagram"] = 2
        t += 2

    # === DIMENSION 3: REACHABILITY (max 15) ===
    if mobile:
        bd["has_mobile"] = 10
        t += 10
    elif phone:
        bd["has_phone_only"] = 5
        t += 5
    if email:
        bd["has_email"] = 3
        t += 3
    if verified:
        bd["contact_verified"] = 2
        t += 2

    # === DIMENSION 4: BUSINESS SIGNALS (max 18) ===
    location_text = (district + " " + address).lower()
    if any(p.lower() in location_text for p in premium_locations):
        bd["premium_location"] = 5
        t += 5

    if specialties:
        bd["has_specialty"] = 4
        t += 4

    if photo_count >= 3:
        bd["has_photos"] = 2
        t += 2

    if has_hours:
        bd["has_hours"] = 2
        t += 2

    if 0 < years_in_business <= 2:
        bd["new_business"] = 2
        t += 2

    # Competition (moved from engagement)
    if 0 < competitor_count <= 10:
        bd["low_competition"] = 3
        t += 3
    elif 0 < competitor_count <= 25:
        bd["medium_competition"] = 2
        t += 2

    # === DIMENSION 5: ENGAGEMENT (max 10) ===
    if avg_review_length > 100:
        bd["detailed_reviews"] = 3
        t += 3

    if newest_review_days < 30:
        bd["recent_activity"] = 4
        t += 4
    elif newest_review_days < 90:
        bd["moderate_activity"] = 2
        t += 2

    if has_owner:
        bd["known_owner"] = 3
        t += 3

    # === DIMENSION 6: URGENCY (max 8) ===
    if negative_flags and rating >= 3.5:
        bd["has_negative_reviews"] = 2
        t += 2

    # SSL error on existing site
    if (
        website
        and site_quality.get("issues")
        and "ssl_error" in site_quality.get("issues", [])
    ):
        bd["ssl_broken"] = 3
        t += 3

    # No online channels at all (no website, no socials, no email)
    if not website and not facebook and not instagram and not email:
        bd["zero_online"] = 3
        t += 3

    # Category
    if t >= hot:
        category = "HOT"
    elif t >= warm:
        category = "WARM"
    else:
        category = "COOL"

    return t, category, bd


def score_lead(lead: Any, premium_locations: list[str] | None = None) -> Any:
    """Score a Lead dataclass (used by scraper.py).

    Args:
        lead: Lead dataclass instance with attributes.
        premium_locations: List of premium location strings from playbook.

    Returns:
        The same Lead with score, category, and score_breakdown set.
    """
    review_analysis = lead.review_analysis or {}
    competitor_report = lead.competitor_report or {}

    owner = getattr(lead, "owner", "") or ""
    has_owner = bool(owner and owner not in OWNER_PLACEHOLDERS)

    t, category, bd = _calculate(
        rating=lead.rating,
        review_count=lead.review_count,
        website=lead.website,
        site_quality=lead.site_quality,
        facebook=lead.facebook,
        instagram=lead.instagram,
        mobile=lead.mobile,
        phone=getattr(lead, "phone", "") or "",
        email=lead.email,
        verified=lead.verified,
        district=lead.district,
        address=lead.address,
        premium_locations=premium_locations or [],
        specialties=lead.specialties,
        photo_count=len(lead.photo_urls),
        has_hours=bool(lead.opening_hours),
        years_in_business=lead.years_in_business,
        negative_flags=review_analysis.get("negative_flags", []),
        avg_review_length=review_analysis.get("avg_review_length", 0),
        newest_review_days=review_analysis.get("newest_review_days", 999),
        has_owner=has_owner,
        competitor_count=competitor_report.get("total_u_kvartu", 0),
    )

    lead.score = t
    lead.category = category
    lead.score_breakdown = bd
    return lead


def score_dict(
    data: dict[str, Any], playbook: dict[str, Any]
) -> tuple[int, str, dict[str, Any]]:
    """Score a lead from dict data (used by research.py and enrich.py).

    Args:
        data: Merged dict of schema + data.json fields.
        playbook: Playbook dict with premium_locations and optional scoring config.

    Returns:
        (score, category, score_breakdown)
    """
    review_analysis = data.get("review_analysis", {}) or {}
    competitor_report = data.get("competitor_report", {}) or {}

    # Handle years_in_business as str or int
    years = data.get("years_in_business", 0) or 0
    if isinstance(years, str):
        try:
            years = int(years)
        except ValueError:
            years = 0

    photos = data.get("photo_urls", []) or data.get("photos", []) or []

    owner = data.get("owner", "") or ""
    has_owner = bool(owner and owner not in OWNER_PLACEHOLDERS)

    return _calculate(
        rating=data.get("rating", 0) or 0,
        review_count=data.get("review_count", 0) or 0,
        website=data.get("website", ""),
        site_quality=data.get("site_quality", {}) or {},
        facebook=data.get("facebook", ""),
        instagram=data.get("instagram", ""),
        mobile=data.get("mobile", ""),
        phone=data.get("phone", ""),
        email=data.get("email", ""),
        verified=data.get("verified", False) or data.get("contact_verified", False),
        district=data.get("district", "") or "",
        address=data.get("address", "") or "",
        premium_locations=playbook.get("premium_locations", []),
        specialties=data.get("specialties", []),
        photo_count=len(photos),
        has_hours=bool(data.get("opening_hours")),
        years_in_business=years,
        negative_flags=review_analysis.get("negative_flags", []),
        avg_review_length=review_analysis.get("avg_review_length", 0),
        newest_review_days=review_analysis.get("newest_review_days", 999),
        has_owner=has_owner,
        competitor_count=competitor_report.get("total_u_kvartu", 0),
        scoring_config=playbook.get("scoring"),
    )
