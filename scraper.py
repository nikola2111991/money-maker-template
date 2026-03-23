#!/usr/bin/env python3
"""
Money Maker Lead Scraper
=========================
Google Places API v1 (New) + Playbook-driven + 4-Gate Qualification + Scoring v2.1
+ Review Analysis + Competitor Reports + Site Quality Check + Schema Draft
+ Checkpoint/Resume + SSL Domain Blacklist + 5 Follow-up Variants

Usage:
    python scraper.py --playbook playbooks/auto-repair-rs.json --cities top8
    python scraper.py --playbook playbooks/auto-repair-rs.json --cities "Beograd,Novi Sad" --target 200
    python scraper.py --playbook playbooks/auto-repair-rs.json --resume
"""

import os
import re
import json
import csv
import time
import random
import hashlib
import requests
import shutil
import logging
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Tuple
from urllib.parse import quote
from collections import defaultdict

from utils import cyr_to_lat, has_cyrillic, strip_diacritics
from config import LEADS_DIR, DEPLOY_BASE_URL, GOOGLE_API_KEY, GOOGLE_SEARCH_API_KEY, GOOGLE_SEARCH_CX
from playbook import load_playbook, load_playbook_from_path
from scoring import score_lead

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

try:
    from PIL import Image, ImageOps
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

HAS_SERPAPI = False  # Removed: Claude Code reads reviews directly per-lead

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, **kwargs):
        desc = kwargs.get('desc', '')
        total = len(iterable) if hasattr(iterable, '__len__') else '?'
        for i, item in enumerate(iterable, 1):
            print(f"\r  {desc} {i}/{total}", end='', flush=True)
            yield item
        print()

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

# ============================================================
# CONSTANTS (extracted magic numbers)
# ============================================================

MAX_VERIFY_COUNT = 150
MAX_SLUG_LENGTH = 55
MIN_REVIEW_QUOTE_LENGTH = 30
MAX_QUOTE_CHARS = 80

# ============================================================
# CONFIGURATION (module-level defaults, overwritten by init_from_playbook)
# ============================================================

_playbook: Optional[Dict] = None
CITIES: Dict[str, str] = {}
CITIES_CYRILLIC: Dict[str, str] = {}
SEARCH_QUERIES_LATIN: List[str] = []
SEARCH_QUERIES_CYRILLIC: List[str] = []
PREMIUM_LOCATIONS: List[str] = []
NOT_AUTO_SERVIS: List[str] = []

# ============================================================
# DATA MODEL
# ============================================================

@dataclass
class Lead:
    # Basic
    name: str = ""
    city: str = ""
    district: str = ""
    address: str = ""
    place_id: str = ""
    business_status: str = ""  # NEW: OPERATIONAL / CLOSED_PERMANENTLY / etc
    
    # Contact
    mobile: str = ""
    phone: str = ""
    email: str = ""
    
    # Online presence
    website: str = ""
    facebook: str = ""
    instagram: str = ""
    google_maps_url: str = ""
    
    # Reputation
    rating: float = 0.0
    review_count: int = 0
    reviews: List[Dict] = field(default_factory=list)
    
    # Photos
    photos: List[str] = field(default_factory=list)
    photo_urls: List[str] = field(default_factory=list)
    
    # Intelligence
    specialties: List[str] = field(default_factory=list)
    years_in_business: int = 0
    opening_hours: List[str] = field(default_factory=list)
    
    # Site quality
    site_quality: Dict = field(default_factory=dict)
    site_content: str = ""  # Extracted text from business website

    # Review analysis
    review_analysis: Dict = field(default_factory=dict)

    # Competitor data
    competitor_report: Dict = field(default_factory=dict)

    # Scoring
    score: int = 0
    category: str = "COOL"
    score_breakdown: Dict = field(default_factory=dict)
    
    # Verification
    contact_sources: Dict = field(default_factory=dict)
    verified: bool = False
    
    # Flags
    flags: List[str] = field(default_factory=list)


# ============================================================
# PHONE / EMAIL UTILITIES (proven from v5, no changes)
# ============================================================

def normalize_phone(phone: str) -> Optional[str]:
    if not phone:
        return None
    prefix = _playbook.get('phone_prefix', '+381') if _playbook else '+381'
    prefix_digits = prefix.lstrip('+')  # e.g. "381"
    cleaned = re.sub(r'[^\d+]', '', phone)
    if cleaned.startswith('00' + prefix_digits):
        cleaned = prefix + cleaned[2 + len(prefix_digits):]
    elif cleaned.startswith(prefix_digits) and not cleaned.startswith('+'):
        cleaned = prefix + cleaned[len(prefix_digits):]
    elif cleaned.startswith('0') and len(cleaned) >= 9:
        cleaned = prefix + cleaned[1:]
    pattern = r'^\+' + re.escape(prefix_digits) + r'[0-9]{8,9}$'
    if re.match(pattern, cleaned):
        return cleaned
    return None

def is_mobile(phone: str) -> bool:
    n = normalize_phone(phone)
    if not n:
        return False
    mobile_prefixes = _playbook.get('mobile_prefixes', ['60','61','62','63','64','65','66','69']) if _playbook else ['60','61','62','63','64','65','66','69']
    prefix_len = len((_playbook.get('phone_prefix', '+381') if _playbook else '+381'))
    after_prefix = n[prefix_len:]
    return len(n) >= 10 and any(after_prefix.startswith(p) for p in mobile_prefixes)

def extract_phones(text: str) -> List[str]:
    prefix = _playbook.get('phone_prefix', '+381') if _playbook else '+381'
    prefix_digits = prefix.lstrip('+')  # e.g. "61" or "381"
    patterns = [
        r'\+' + prefix_digits + r'[\s\-/]?\d{1,3}[\s\-/]?\d{3,4}[\s\-/]?\d{3,4}',
        r'00' + prefix_digits + r'[\s\-/]?\d{1,3}[\s\-/]?\d{3,4}[\s\-/]?\d{3,4}',
        r'0\d{1,3}[\s\-/]?\d{3,4}[\s\-/]?\d{3,4}',
    ]
    phones = []
    for p in patterns:
        for m in re.findall(p, text or ''):
            n = normalize_phone(m)
            if n and n not in phones:
                phones.append(n)
    return phones

def extract_emails(text: str) -> List[str]:
    if not text:
        return []
    return list(set([
        m.lower() for m in re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
        if not any(x in m.lower() for x in ['.png','.jpg','.gif','.svg','.css','.js','wixpress','sentry'])
    ]))


# ============================================================
# SPECIALTY DETECTION
# ============================================================

SPECIALTY_MAP: Dict[str, str] = {}

def detect_specialties(lead: Lead) -> List[str]:
    """Detect specialties from name + reviews"""
    specs = set()
    texts = [lead.name.lower()]
    for r in lead.reviews:
        texts.append(r.get("text", "").lower())
    
    combined = " ".join(texts)
    for keyword, spec in SPECIALTY_MAP.items():
        if keyword in combined:
            specs.add(spec)
    return list(specs)


def estimate_years(lead: Lead) -> int:
    """Estimate years in business from oldest review"""
    if not lead.reviews:
        return 0
    oldest = None
    for r in lead.reviews:
        t = r.get("time", 0)
        if t and (oldest is None or t < oldest):
            oldest = t
    if oldest:
        years = (datetime.now().timestamp() - oldest) / (365.25 * 86400)
        return max(int(years), 1)
    return 0


# ============================================================
# REVIEW ANALYSIS (NEW)
# ============================================================

REVIEW_KEYWORD_GROUPS: Dict[str, List[str]] = {}
NEGATIVE_PATTERNS: Dict[str, List[str]] = {}

# Word-boundary patterns to prevent false positives (e.g. "bol" in "najbolji")
_NEGATIVE_REGEX: Dict[str, list] = {}


def init_from_playbook(pb: dict) -> None:
    """Initialize module constants from playbook data."""
    global _playbook, CITIES, CITIES_CYRILLIC, SEARCH_QUERIES_LATIN, SEARCH_QUERIES_CYRILLIC
    global PREMIUM_LOCATIONS, NOT_AUTO_SERVIS, SPECIALTY_MAP, REVIEW_KEYWORD_GROUPS, NEGATIVE_PATTERNS
    global _NEGATIVE_REGEX
    _playbook = pb
    CITIES = pb.get('cities', {})
    CITIES_CYRILLIC = pb.get('cities_alt', {})
    SEARCH_QUERIES_LATIN = pb.get('search_queries', [])
    SEARCH_QUERIES_CYRILLIC = pb.get('search_queries_alt', [])
    PREMIUM_LOCATIONS = pb.get('premium_locations', [])
    NOT_AUTO_SERVIS = pb.get('exclude_words', [])
    SPECIALTY_MAP = pb.get('specialty_map', {})
    REVIEW_KEYWORD_GROUPS = pb.get('review_keywords', {})
    NEGATIVE_PATTERNS = pb.get('negative_patterns', {})
    # Rebuild negative regex
    _NEGATIVE_REGEX = {}
    for flag, pats in NEGATIVE_PATTERNS.items():
        _NEGATIVE_REGEX[flag] = [re.compile(r'(?<![a-zA-ZčćšžđČĆŠŽĐа-яА-ЯљњџЉЊЏ])' + re.escape(p) + r'(?![a-zA-ZčćšžđČĆŠŽĐа-яА-ЯљњџЉЊЏ])', re.IGNORECASE) for p in pats]

def _parse_relative_time(text: str) -> float:
    """Parse relative time like '2 months ago' to unix timestamp. Returns 0 if unparseable."""
    if not text:
        return 0
    text = text.lower().strip()
    m = re.match(r'(?:an?\s+|(\d+)\s+)(day|week|month|year)s?\s+ago', text)
    if not m:
        return 0
    count = int(m.group(1)) if m.group(1) else 1
    unit = m.group(2)
    days_map = {"day": 1, "week": 7, "month": 30, "year": 365}
    days = count * days_map.get(unit, 30)
    return datetime.now().timestamp() - (days * 86400)


def analyze_reviews(reviews: List[Dict]) -> Dict:
    """Extract keywords, sentiment, USP candidates, negative patterns from reviews"""
    if not reviews:
        return {
            "top_keywords": [], "negative_flags": [], "review_velocity": "unknown",
            "keyword_hints": [], "faq_hints": [], "best_quotes": [],
        }
    
    # Combine all review texts
    texts = [r.get("text", "").lower() for r in reviews if r.get("text")]
    if not texts:
        return {
            "top_keywords": [], "negative_flags": [], "review_velocity": "unknown",
            "keyword_hints": [], "faq_hints": [], "best_quotes": [],
        }
    
    # Count keyword group mentions (substring matching is safe here - keywords are 3+ char stems)
    keyword_counts = {}
    for group_name, keywords in REVIEW_KEYWORD_GROUPS.items():
        count = 0
        for text in texts:
            for kw in keywords:
                if kw in text:
                    count += 1
                    break  # Count once per review per group
        if count > 0:
            keyword_counts[group_name] = count
    
    # Sort by frequency
    top_keywords = sorted(keyword_counts.items(), key=lambda x: -x[1])
    
    # Negative patterns (word-boundary regex to prevent false positives like "bol" in "najbolji")
    negative_flags = []
    for flag_name, regexes in _NEGATIVE_REGEX.items():
        for text in texts:
            for rgx in regexes:
                if rgx.search(text):
                    negative_flags.append(flag_name)
                    break
            if flag_name in negative_flags:
                break
    
    # Review velocity + recency
    times = [r.get("time", 0) for r in reviews if r.get("time")]
    if not times:
        # Fallback: parse relative_time_description
        for r in reviews:
            rel = r.get("relative_time_description", "")
            estimated = _parse_relative_time(rel)
            if estimated > 0:
                times.append(estimated)
    velocity = "unknown"
    days_since = 999
    if times:
        newest = max(times)
        days_since = (datetime.now().timestamp() - newest) / 86400
        if days_since < 90:
            velocity = "active"
        elif days_since < 365:
            velocity = "moderate"
        elif days_since < 730:
            velocity = "slow"
        else:
            velocity = "stagnant"
    
    # Best quotes (5★, longer than 30 chars)
    best_quotes = []
    for r in reviews:
        if r.get("rating", 0) >= 5 and len(r.get("text", "")) > MIN_REVIEW_QUOTE_LENGTH:
            best_quotes.append({
                "author": r.get("author", ""),
                "text": r.get("text", "")[:200],
            })
    
    # Generate keyword hints
    keyword_hints = [f"{name} ({count}x u recenzijama)" for name, count in top_keywords[:4]]
    
    # Generate FAQ hints
    faq_hints = []
    if "bezbolno" in keyword_counts:
        faq_hints.append({"pitanje": "Da li intervencije bole?", "hint": "bezbolnost čest keyword u recenzijama"})
    if "deca" in keyword_counts:
        faq_hints.append({"pitanje": "Da li primate decu?", "hint": "deca pominju se u recenzijama"})
    if "cena" in keyword_counts:
        faq_hints.append({"pitanje": "Koliko koštaju usluge?", "hint": "cena pomenuta u recenzijama"})
    if "čekanje" in negative_flags:
        faq_hints.append({"pitanje": "Koliko se čeka na red?", "hint": "negativan signal - adresirati u FAQ"})
    if "cena_visoka" in negative_flags:
        faq_hints.append({"pitanje": "Da li su cene pristupačne?", "hint": "negativan signal - adresirati u FAQ"})
    
    # Average review length (engagement quality proxy)
    avg_review_length = sum(len(t) for t in texts) // len(texts) if texts else 0

    return {
        "top_keywords": [{"keyword": k, "count": c} for k, c in top_keywords],
        "negative_flags": list(set(negative_flags)),
        "review_velocity": velocity,
        "keyword_hints": keyword_hints,
        "faq_hints": faq_hints,
        "best_quotes": best_quotes[:3],
        "avg_review_length": avg_review_length,
        "newest_review_days": int(days_since),
    }


# ============================================================
# SITE QUALITY CHECK (NEW)
# ============================================================

def _extract_site_text(html: str) -> str:
    """Extract meaningful text from HTML using BeautifulSoup. Returns max 2000 chars."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "iframe"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    # Remove very short fragments (menu items, buttons)
    lines = [line.strip() for line in text.split('.') if len(line.strip()) > 20]
    text = '. '.join(lines)
    return text[:2000]


def check_site_quality(url: str, session: requests.Session) -> Dict:
    """Check if existing website is good or bad. Returns quality score 0-100."""
    result = {"quality_score": 100, "is_bad": False, "issues": [], "checked": False, "site_content": ""}

    if not url:
        return result

    if not url.startswith('http'):
        url = 'https://' + url

    try:
        resp = session.get(url, timeout=8, allow_redirects=True)
        result["checked"] = True
        html = resp.text.lower()
        score = 100
        issues = []

        # Site doesn't load
        if resp.status_code != 200:
            return {"quality_score": 0, "is_bad": True, "issues": ["site_down"], "checked": True, "site_content": ""}

        # Extract text content for Claude
        result["site_content"] = _extract_site_text(resp.text)

        # No HTTPS
        if not resp.url.startswith('https'):
            score -= 15
            issues.append("no_https")

        # Not responsive (no viewport meta)
        if 'viewport' not in html:
            score -= 25
            issues.append("not_responsive")

        # Table-based layout (ancient design)
        if html.count('<table') > 3:
            score -= 15
            issues.append("table_layout")

        # Very small page (broken or placeholder)
        if len(html) < 2000:
            score -= 30
            issues.append("nearly_empty")

        # Flash
        if '.swf' in html or 'flash' in html:
            score -= 25
            issues.append("flash")

        # Under construction / coming soon
        if any(x in html for x in ['under construction', 'coming soon', 'u izradi', 'uskoro']):
            score -= 40
            issues.append("under_construction")

        # Parked domain
        if any(x in html for x in ['domain is for sale', 'buy this domain', 'parked', 'ovaj domen']):
            score -= 50
            issues.append("parked_domain")

        # Template / free website builder (-30)
        if any(x in html for x in ['wix.com', 'squarespace.com', 'weebly.com',
                                     'wordpress.com', 'godaddy.com/websites',
                                     'site123.com', 'jimdo.com']):
            score -= 30
            issues.append("template_builder")
        elif 'generator' in html:
            gen_match = re.search(r'<meta[^>]*generator[^>]*content=["\']([^"\']+)', html)
            if gen_match:
                gen = gen_match.group(1).lower()
                if any(b in gen for b in ['wix', 'squarespace', 'weebly', 'wordpress.com',
                                           'site123', 'jimdo', 'duda', 'webflow']):
                    score -= 30
                    issues.append("template_builder")

        # Outdated design signals (-20)
        outdated_signals = [
            'jquery-ui', 'bootstrap/2.', 'bootstrap/3.0', 'bootstrap/3.1',
            '<marquee', 'bgcolor=', '<blink', '<center>',
        ]
        if sum(1 for s in outdated_signals if s in html) >= 2:
            score -= 20
            issues.append("outdated_design")

        # Placeholder / dummy content (-35)
        placeholder_terms = ['lorem ipsum', 'your company', 'sample page',
                             'example.com', 'test page', 'default page',
                             'website coming soon', 'site under development']
        if any(x in html for x in placeholder_terms):
            score -= 35
            issues.append("placeholder_content")

        # Too little real content (-25)
        if result.get("site_content"):
            word_count = len(result["site_content"].split())
            if word_count < 100:
                score -= 25
                issues.append("thin_content")

        # No contact info on site (-15)
        if result.get("site_content"):
            content = result["site_content"]
            has_phone = bool(re.search(r'\b\d{2,4}[\s\-]?\d{3,4}[\s\-]?\d{3,4}\b', content))
            has_email = bool(re.search(r'[\w\.-]+@[\w\.-]+\.\w+', content))
            if not has_phone and not has_email:
                score -= 15
                issues.append("no_contact_on_site")

        result["quality_score"] = max(0, score)
        result["is_bad"] = score < 50
        result["is_mediocre"] = 50 <= score < 70
        result["issues"] = issues

    except requests.exceptions.SSLError:
        result = {"quality_score": 20, "is_bad": True, "issues": ["ssl_error"], "checked": True, "site_content": ""}
    except requests.exceptions.ConnectionError:
        result = {"quality_score": 0, "is_bad": True, "issues": ["connection_error"], "checked": True, "site_content": ""}
    except requests.exceptions.Timeout:
        result = {"quality_score": 10, "is_bad": True, "issues": ["timeout"], "checked": True, "site_content": ""}
    except Exception as e:
        log.debug(f"Ignored: {e}")
        result["checked"] = False

    return result


# ============================================================
# 4-GATE QUALIFICATION
# ============================================================

def qualify_lead(lead: Lead) -> Tuple[bool, str]:
    """
    4-Gate qualification process.
    Returns (passed: bool, reason: str)
    """
    # GATE 1: Does it exist?
    if lead.business_status == "CLOSED_PERMANENTLY":
        return False, "gate1_closed_permanently"
    if lead.business_status == "CLOSED_TEMPORARILY":
        return False, "gate1_closed_temporarily"
    if not lead.name:
        return False, "gate1_no_name"
    
    # GATE 2: Is it an auto servis?
    name_lower = lead.name.lower()
    for term in NOT_AUTO_SERVIS:
        if term in name_lower:
            return False, f"gate2_not_auto_servis_{term}"
    
    # GATE 3: Worth contacting?
    # Smart rating filter based on review count
    if lead.review_count >= 30 and lead.rating > 0 and lead.rating < 3.0:
        return False, "gate3_bad_rating_high_volume"
    if 10 <= lead.review_count < 30 and lead.rating > 0 and lead.rating < 3.5:
        return False, "gate3_bad_rating_medium_volume"
    if 5 <= lead.review_count < 10 and lead.rating > 0 and lead.rating < 3.5:
        return False, "gate3_bad_rating_low_volume"
    if 1 <= lead.review_count < 5 and lead.rating > 0 and lead.rating < 4.0:
        return False, "gate3_bad_rating_very_low_volume"
    # 0 reviews = accept (new practice)
    
    # Must have at least one contact method
    if not lead.mobile and not lead.email and not lead.phone and not lead.website:
        return False, "gate3_no_contact"
    
    # GATE 4: Is it active?
    if lead.reviews:
        times = [r.get("time", 0) for r in lead.reviews if r.get("time")]
        if times:
            newest = max(times)
            days_since = (datetime.now().timestamp() - newest) / 86400
            # Last review 3+ years ago AND no phone = probably dead
            if days_since > 1095 and not lead.mobile and not lead.phone:
                return False, "gate4_probably_inactive"
            # Flag as possibly inactive
            if days_since > 730:
                lead.flags.append("possibly_inactive")
    
    return True, "qualified"


# ============================================================
# SCORING v2.1
# ============================================================

# ============================================================
# GOOGLE PLACES API
# ============================================================

class PlacesAPI:
    """Google Places API (New) v1 - uses places.googleapis.com endpoints."""

    # Field masks for cost optimization
    # Text Search: basic fields only (Pro SKU ~$0.032)
    SEARCH_FIELDS = "places.id,places.displayName,places.formattedAddress,places.rating,places.userRatingCount,places.businessStatus"
    # Place Details: full fields (Enterprise SKU ~$0.025)
    DETAIL_FIELDS = ("displayName,formattedAddress,nationalPhoneNumber,internationalPhoneNumber,"
                     "websiteUri,rating,userRatingCount,photos,googleMapsUri,types,"
                     "regularOpeningHours,reviews,businessStatus,location")

    def __init__(self, api_key: str):
        self.key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            "X-Goog-Api-Key": api_key,
            "Content-Type": "application/json",
        })
        self.call_count = 0
        self.cost_estimate = 0.0

    def _track(self, cost_per_call: float = 0.0):
        self.call_count += 1
        self.cost_estimate += cost_per_call

    def _post_with_retry(self, url: str, body: dict, field_mask: str, cost: float, max_retries: int = 3) -> Optional[Dict]:
        """POST with retry for 429/5xx. Exponential backoff."""
        headers = {"X-Goog-FieldMask": field_mask}
        for attempt in range(max_retries):
            try:
                resp = self.session.post(url, json=body, headers=headers, timeout=15)
                self._track(cost)

                if resp.status_code == 429:
                    wait = min(30, 5 * (2 ** attempt))
                    log.warning(f"⚠️ API 429 rate limit, čekam {wait}s (pokušaj {attempt+1}/{max_retries})")
                    time.sleep(wait)
                    continue

                if resp.status_code >= 500:
                    wait = 3 * (2 ** attempt)
                    log.warning(f"⚠️ API {resp.status_code}, retry za {wait}s")
                    time.sleep(wait)
                    continue

                if resp.status_code == 200:
                    return resp.json()

                log.warning(f"⚠️ API {resp.status_code}: {resp.text[:200]}")
                return None
            except requests.exceptions.Timeout:
                log.warning(f"⚠️ Timeout, retry {attempt+1}/{max_retries}")
                time.sleep(3 * (attempt + 1))
            except Exception as e:
                log.error(f"Request error: {e}")
                return None

        log.error(f"❌ Max retries ({max_retries}) reached for {url}")
        return None

    def _get_with_retry(self, url: str, field_mask: str, cost: float, max_retries: int = 3) -> Optional[Dict]:
        """GET with retry for 429/5xx. Exponential backoff."""
        headers = {"X-Goog-FieldMask": field_mask}
        for attempt in range(max_retries):
            try:
                resp = self.session.get(url, headers=headers, timeout=15)
                self._track(cost)

                if resp.status_code == 429:
                    wait = min(30, 5 * (2 ** attempt))
                    time.sleep(wait)
                    continue
                if resp.status_code >= 500:
                    time.sleep(3 * (2 ** attempt))
                    continue
                if resp.status_code == 200:
                    return resp.json()

                log.warning(f"⚠️ API {resp.status_code}: {resp.text[:200]}")
                return None
            except requests.exceptions.Timeout:
                time.sleep(3 * (attempt + 1))
            except Exception as e:
                log.error(f"Request error: {e}")
                return None
        return None

    def text_search(self, query: str, lat: str, lng: str, radius: int = 20000) -> List[Dict]:
        """Search for places via Places API v1. Returns list in LEGACY-compatible format."""
        url = "https://places.googleapis.com/v1/places:searchText"
        body = {
            "textQuery": query,
            "locationBias": {
                "circle": {
                    "center": {"latitude": float(lat), "longitude": float(lng)},
                    "radius": float(radius),
                }
            },
            "languageCode": _playbook.get('language', 'sr') if _playbook else 'sr',
            "pageSize": 20,
        }

        all_results = []
        data = self._post_with_retry(url, body, self.SEARCH_FIELDS, 0.032)
        if not data:
            return []

        all_results.extend(self._normalize_search_results(data.get("places", [])))

        # Pagination (up to 3 pages = 60 results)
        for _ in range(2):
            token = data.get("nextPageToken")
            if not token:
                break
            time.sleep(2)
            body["pageToken"] = token
            data = self._post_with_retry(url, body, self.SEARCH_FIELDS, 0.032)
            if not data:
                break
            all_results.extend(self._normalize_search_results(data.get("places", [])))
            body.pop("pageToken", None)

        return all_results

    def _normalize_search_results(self, places: List[Dict]) -> List[Dict]:
        """Convert v1 search results to legacy-compatible format for minimal code changes."""
        results = []
        for p in places:
            results.append({
                "place_id": p.get("id", ""),
                "name": p.get("displayName", {}).get("text", "") if isinstance(p.get("displayName"), dict) else "",
                "formatted_address": p.get("formattedAddress", ""),
                "rating": p.get("rating", 0.0),
                "user_ratings_total": p.get("userRatingCount", 0),
                "business_status": p.get("businessStatus", ""),
            })
        return results

    def get_details(self, place_id: str) -> Optional[Dict]:
        """Get detailed info via Places API v1. Returns LEGACY-compatible format."""
        url = f"https://places.googleapis.com/v1/places/{place_id}"
        params = {"languageCode": _playbook.get('language', 'sr') if _playbook else 'sr'}
        # Use GET with query params for details
        headers = {"X-Goog-FieldMask": self.DETAIL_FIELDS}
        for attempt in range(3):
            try:
                resp = self.session.get(url, params=params, headers=headers, timeout=15)
                self._track(0.025)  # Enterprise SKU

                if resp.status_code == 429:
                    time.sleep(min(30, 5 * (2 ** attempt)))
                    continue
                if resp.status_code >= 500:
                    time.sleep(3 * (2 ** attempt))
                    continue
                if resp.status_code == 200:
                    return self._normalize_details(resp.json())

                log.warning(f"⚠️ Details API {resp.status_code} for {place_id}")
                return None
            except requests.exceptions.Timeout:
                time.sleep(3 * (attempt + 1))
            except Exception as e:
                log.error(f"Details error: {e}")
                return None
        return None

    def _normalize_details(self, p: Dict) -> Dict:
        """Convert v1 details to legacy-compatible format."""
        # Photos: v1 returns 'name' (resource path), legacy used 'photo_reference'
        photos = []
        for photo in p.get("photos", []):
            name = photo.get("name", "")
            if name:
                photos.append({"photo_reference": name})  # store resource name as photo_reference

        # Reviews: v1 format → legacy format
        reviews = []
        for r in p.get("reviews", []):
            author_attr = r.get("authorAttribution", {})
            text_obj = r.get("text", {})
            # Convert publishTime (RFC 3339) to unix timestamp
            pub_time = r.get("publishTime", "")
            unix_time = 0
            if pub_time:
                try:
                    from datetime import timezone
                    dt = datetime.fromisoformat(pub_time.replace("Z", "+00:00"))
                    unix_time = int(dt.timestamp())
                except Exception as e:
                    log.debug(f"Ignored: {e}")
            reviews.append({
                "author_name": author_attr.get("displayName", ""),
                "rating": r.get("rating", 5),
                "text": text_obj.get("text", "") if isinstance(text_obj, dict) else str(text_obj),
                "time": unix_time,
                "relative_time_description": r.get("relativePublishTimeDescription", ""),
            })

        # Opening hours
        opening_hours = {}
        reg_hours = p.get("regularOpeningHours", {})
        if reg_hours:
            opening_hours["weekday_text"] = reg_hours.get("weekdayDescriptions", [])

        return {
            "name": p.get("displayName", {}).get("text", "") if isinstance(p.get("displayName"), dict) else "",
            "formatted_address": p.get("formattedAddress", ""),
            "formatted_phone_number": p.get("nationalPhoneNumber", ""),
            "international_phone_number": p.get("internationalPhoneNumber", ""),
            "website": p.get("websiteUri", ""),
            "rating": p.get("rating", 0.0),
            "user_ratings_total": p.get("userRatingCount", 0),
            "photos": photos,
            "url": p.get("googleMapsUri", ""),
            "types": p.get("types", []),
            "opening_hours": opening_hours,
            "reviews": reviews,
            "business_status": p.get("businessStatus", ""),
        }

    def download_photo(self, photo_ref: str, filepath: str, max_width: int = 800) -> bool:
        """Download a photo via Places API v1 with retry. Cost: $0.007 per call.
        photo_ref is now a resource name like 'places/XXX/photos/YYY'."""
        # v1 photo URL: https://places.googleapis.com/v1/{NAME}/media?maxWidthPx=800
        url = f"https://places.googleapis.com/v1/{photo_ref}/media?maxWidthPx={max_width}&skipHttpRedirect=true"
        for attempt in range(3):
            try:
                # First get the photo URI
                resp = self.session.get(url, timeout=15)
                self._track(0.007)
                if resp.status_code == 200:
                    data = resp.json()
                    photo_url = data.get("photoUri", "")
                    if photo_url:
                        # Download actual image
                        img_resp = requests.get(photo_url, timeout=15)
                        if img_resp.status_code == 200 and len(img_resp.content) > 1000:
                            with open(filepath, 'wb') as f:
                                f.write(img_resp.content)
                            # Optimize za web
                            if HAS_PILLOW:
                                try:
                                    with Image.open(filepath) as img:
                                        img = ImageOps.exif_transpose(img) or img
                                        if img.mode in ('RGBA', 'LA', 'P'):
                                            img = img.convert('RGB')
                                        if img.width > 800:
                                            ratio = 800 / img.width
                                            resampling = getattr(Image, 'Resampling', Image).LANCZOS
                                            img = img.resize((800, round(img.height * ratio)), resampling)
                                        img.save(filepath, "JPEG", quality=80, optimize=True)
                                except Exception as e:
                                    log.debug(f"Ignored: {e}")
                            return True
                if resp.status_code == 403:
                    break  # expired reference, don't retry
            except requests.exceptions.Timeout:
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))
                    continue
            except Exception as e:
                log.debug(f"Ignored: {e}")
                break
        return False


# ============================================================
# WEB ENRICHMENT (from v5, improved)
# ============================================================

class WebEnricher:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        self.failed_domains = set()  # FIX: domain blacklist za SSL/Connection errore
    
    def _get_domain(self, url: str) -> str:
        """Extract domain from URL for blacklist tracking"""
        from urllib.parse import urlparse
        try:
            return urlparse(url).netloc.lower()
        except Exception as e:
            log.debug(f"Ignored: {e}")
            return url.lower()
    
    def is_domain_blocked(self, url: str) -> bool:
        """Check if domain previously failed with SSL/Connection error"""
        return self._get_domain(url) in self.failed_domains
    
    def enrich(self, url: str) -> Dict:
        """Scrape website for email, social links, additional phones"""
        result = {"emails": [], "phones": [], "mobile": None,
                  "facebook": None, "instagram": None, "ssl_failed": False}
        
        if not url or not HAS_BS4:
            return result
        if not url.startswith('http'):
            url = 'https://' + url
        
        # FIX: skip ako je domen već failovao
        if self.is_domain_blocked(url):
            result["ssl_failed"] = True
            return result
        
        base = url.rstrip('/')
        pages = [base, f"{base}/kontakt", f"{base}/contact", f"{base}/o-nama", f"{base}/about"]
        
        for page_url in pages:
            try:
                resp = self.session.get(page_url, timeout=8, allow_redirects=True)
                if resp.status_code != 200:
                    continue
                
                soup = BeautifulSoup(resp.text, 'html.parser')
                text = soup.get_text()
                
                # Phones
                for p in extract_phones(text):
                    if p not in result['phones']:
                        result['phones'].append(p)
                        if is_mobile(p) and not result['mobile']:
                            result['mobile'] = p
                
                # Emails
                result['emails'].extend(extract_emails(text))
                for a in soup.find_all('a', href=True):
                    if a['href'].startswith('mailto:'):
                        email = a['href'].replace('mailto:', '').split('?')[0].strip()
                        if email and '@' in email:
                            result['emails'].append(email.lower())
                
                # Social links
                for a in soup.find_all('a', href=True):
                    href = a['href'].lower()
                    if 'facebook.com' in href and '/share' not in href and not result['facebook']:
                        result['facebook'] = a['href']
                    if 'instagram.com' in href and '/share' not in href and not result['instagram']:
                        result['instagram'] = a['href']
                
                time.sleep(0.3)
            except (requests.exceptions.SSLError, requests.exceptions.ConnectionError) as e:
                # FIX: SSL/Connection error → blacklist domain, break odmah
                domain = self._get_domain(page_url)
                self.failed_domains.add(domain)
                result["ssl_failed"] = True
                log.warning(f"⚠️ {domain} → SSL/Connection error, blacklisted (skip remaining pages)")
                break  # NE pokušavaj /kontakt, /contact itd
            except requests.exceptions.Timeout:
                break  # Timeout na base → nema smisla pokušavati subpage
            except Exception as e:
                log.debug(f"Ignored: {e}")

        result['emails'] = list(set(result['emails']))
        return result


# ============================================================
# DIRECTORY VERIFICATION (from v5)
# ============================================================

class DirectoryVerifier:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        self._blocked = False
        self._fail_count = 0

    def search_google(self, name: str, city: str) -> Dict:
        """Google Custom Search API for additional contacts. Gracefully stops after 3 failures."""
        result: Dict = {"phones": [], "emails": [], "facebook": None, "instagram": None}

        # If blocked 3+ times, stop wasting time
        if self._blocked:
            return result

        # No API credentials: return empty (don't crash)
        if not GOOGLE_SEARCH_API_KEY or not GOOGLE_SEARCH_CX:
            log.debug("Google Custom Search API key or CX not configured. Skipping.")
            return result

        try:
            search_terms = _playbook.get('search_verify_terms', 'kontakt') if _playbook else 'kontakt'
            query = f'"{name}" {city} {search_terms}'
            url = "https://www.googleapis.com/customsearch/v1"
            params = {
                "key": GOOGLE_SEARCH_API_KEY,
                "cx": GOOGLE_SEARCH_CX,
                "q": query,
            }
            resp = self.session.get(url, params=params, timeout=8)

            # Detect rate limiting (429) or server errors
            if resp.status_code == 429 or resp.status_code >= 500:
                self._fail_count += 1
                if self._fail_count >= 3:
                    self._blocked = True
                    log.warning("Google Custom Search blocked (%d). Skipping remaining verifications.", resp.status_code)
                return result

            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", [])

                all_text = ""
                for item in items:
                    snippet = item.get("snippet", "")
                    link = item.get("link", "")
                    title = item.get("title", "")
                    all_text += f" {snippet} {title} {link}"

                    # Extract facebook/instagram from result links
                    if not result["facebook"] and re.search(r'facebook\.com/', link):
                        result["facebook"] = link
                    if not result["instagram"] and re.search(r'instagram\.com/', link):
                        result["instagram"] = link

                result["phones"] = extract_phones(all_text)
                result["emails"] = extract_emails(all_text)

                # Also check snippets for social links
                if not result["facebook"]:
                    fb = re.search(r'(https?://(?:www\.)?facebook\.com/[^\s",]+)', all_text)
                    if fb:
                        result["facebook"] = fb.group(1)
                if not result["instagram"]:
                    ig = re.search(r'(https?://(?:www\.)?instagram\.com/[^\s",]+)', all_text)
                    if ig:
                        result["instagram"] = ig.group(1)

                # Reset fail count on success
                self._fail_count = 0
        except Exception as e:
            log.debug(f"Google Custom Search error: {e}")
            self._fail_count += 1
            if self._fail_count >= 3:
                self._blocked = True
                log.warning("Google Custom Search unreachable. Skipping remaining verifications.")
        return result


# ============================================================
# COMPETITOR ANALYSIS (NEW)
# ============================================================

def build_competitor_reports(leads: List[Lead]) -> None:
    """Build competitor reports per district/city. Mutates leads in-place."""
    # Group leads by city + district
    groups = defaultdict(list)
    for lead in leads:
        # Normalize district
        district = lead.district.strip() if lead.district else lead.city
        key = f"{lead.city}|{district}"
        groups[key].append(lead)
    
    for key, group in groups.items():
        city, district = key.split("|", 1)
        is_city_level = (district == city)  # FIX: tačno kad nema kvarta
        total = len(group)
        with_website = len([l for l in group if l.website and not l.site_quality.get("is_bad")])
        with_bad_website = len([l for l in group if l.website and l.site_quality.get("is_bad")])
        without_website = len([l for l in group if not l.website])
        ratings = [l.rating for l in group if l.rating > 0]
        avg_rating = round(sum(ratings) / len(ratings), 1) if ratings else 0
        
        # Sort by rating to find top competitors
        sorted_group = sorted(group, key=lambda x: (x.rating, x.review_count), reverse=True)
        
        for lead in group:
            # Find top 3 competitors (not self)
            top_competitors = []
            for comp in sorted_group:
                if comp.place_id == lead.place_id:
                    continue
                if len(top_competitors) >= 3:
                    break
                top_competitors.append({
                    "naziv": comp.name,
                    "rating": comp.rating,
                    "recenzija": comp.review_count,
                    "ima_sajt": bool(comp.website),
                    "loš_sajt": comp.site_quality.get("is_bad", False),
                })
            
            # Determine lead's advantage
            advantage = ""
            if not lead.website and lead.rating > 0:
                higher_rated_with_site = [l for l in group if l.website and l.rating < lead.rating]
                if higher_rated_with_site:
                    advantage = f"Higher rating than {len(higher_rated_with_site)} competitors who have websites"
                elif lead.rating >= avg_rating:
                    advantage = f"Rating above area average ({avg_rating:.1f}), no website"
            elif lead.website and lead.rating > avg_rating:
                advantage = f"Rating above area average ({avg_rating:.1f})"
            
            lead.competitor_report = {
                "grad": city,
                "kvart": district,
                "is_city_level": is_city_level,
                "total_u_kvartu": total,
                "sa_sajtom": with_website,
                "sa_lošim_sajtom": with_bad_website,
                "bez_sajta": without_website,
                "prosek_rating": avg_rating,
                "prednost": advantage,
                "top_konkurenti": top_competitors,
            }


# ============================================================
# DISTRICT EXTRACTION
# ============================================================

# Belgrade municipalities for better district detection
BG_MUNICIPALITIES = [
    "Vračar", "Vracar", "Stari Grad", "Savski Venac", "Zvezdara",
    "Palilula", "Voždovac", "Čukarica", "Rakovica", "Novi Beograd",
    "Zemun", "Surčin", "Barajevo", "Grocka", "Lazarevac", "Mladenovac",
    "Obrenovac", "Sopot", "Dorćol", "Dorcol", "Dedinje", "Senjak",
    "Banjica", "Karaburma", "Medaković", "Konjarnik",
]

def extract_district(address: str, city: str) -> str:
    """Extract district/municipality from address"""
    if not address:
        return ""
    
    addr_lower = address.lower()
    
    # For Belgrade, try to match municipality
    if city == "Beograd":
        for muni in BG_MUNICIPALITIES:
            if muni.lower() in addr_lower:
                return muni
    
    # For Novi Sad
    if city == "Novi Sad":
        for ns_part in ["Liman", "Grbavica", "Detelinara", "Novo Naselje", "Petrovaradin", "Centar", "Telep", "Podbara", "Sajlovo", "Klisa"]:
            if ns_part.lower() in addr_lower:
                return ns_part
    
    # Niš
    if city == "Niš":
        for part in ["Medijana", "Palilula", "Pantelej", "Crveni Krst", "Niška Banja", "Centar", "Ćele Kula", "Duvanište"]:
            if part.lower() in addr_lower:
                return part
    
    # Kragujevac
    if city == "Kragujevac":
        for part in ["Centar", "Aerodrom", "Pivara", "Stanovo", "Erdoglija", "Bresnica"]:
            if part.lower() in addr_lower:
                return part
    
    # Subotica
    if city == "Subotica":
        for part in ["Centar", "Zorka", "Prozivka", "Dudova Šuma", "Mali Bajmok"]:
            if part.lower() in addr_lower:
                return part
    
    return ""



# ============================================================

# Content mappings removed: scraper leaves content fields empty.
# AI (enrich.py / copy_generator.py / Claude Code) fills them per-lead.


# ============================================================
# HELPERS - slug, phone format, doctor extraction
# ============================================================

def make_slug(name: str, city: str, district: str = "", place_id: str = "") -> str:
    """Generate unique slug. Includes district if available, plus short hash for uniqueness."""
    parts = [city, name]
    if district and district.lower() != city.lower():
        parts = [city, district, name]
    text = "-".join(parts).lower().strip()
    for src, dst in [('č','c'),('ć','c'),('š','s'),('ž','z'),('đ','dj')]:
        text = text.replace(src, dst)
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s]+', '-', text)
    text = re.sub(r'-+', '-', text).strip('-')
    slug = text[:MAX_SLUG_LENGTH]
    # Append short hash from place_id for guaranteed uniqueness
    if place_id:
        slug += "-" + hashlib.md5(place_id.encode()).hexdigest()[:4]
    return slug


def _has_hungarian_chars(text: str) -> bool:
    """Detect Hungarian characters/patterns in text (Vojvodina minority names)."""
    hungarian_chars = set('áéíóöőúüűÁÉÍÓÖŐÚÜŰ')
    if any(c in hungarian_chars for c in text):
        return True
    # Common Hungarian dental name patterns
    hungarian_patterns = ['sz', 'gy', 'cs', 'zs', 'ny', 'ly']
    text_lower = text.lower()
    return any(p in text_lower for p in hungarian_patterns) and not any(c in text for c in 'čćšžđČĆŠŽĐ')


_SERBIAN_DICT: Optional[Dict[str, str]] = None

def _load_serbian_dict() -> Dict[str, str]:
    """Lazy-load serbian_diacritics.json (85k word mapping, ~2MB)."""
    global _SERBIAN_DICT
    if _SERBIAN_DICT is not None:
        return _SERBIAN_DICT
    dict_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "serbian_diacritics.json")
    if os.path.exists(dict_path):
        with open(dict_path, "r", encoding="utf-8") as f:
            _SERBIAN_DICT = json.load(f)
        log.info(f"Serbian diacritics dict loaded: {len(_SERBIAN_DICT)} entries")
    else:
        log.warning(f"serbian_diacritics.json not found at {dict_path}, using fallback")
        _SERBIAN_DICT = {}
    return _SERBIAN_DICT


def fix_serbian_diacritics(text: str) -> str:
    """Fix missing Serbian diacritics (č, ć, š, ž, đ) using 85k word dictionary.

    Covers all Serbian words, names, and inflections from Hunspell sr-Latn.
    Falls back to surname-ending rules (-vić, -čić) for words not in dictionary.
    Skips Hungarian names (Vojvodina) to avoid corrupting them.
    """
    if not text:
        return text

    # Skip Hungarian names - diacritics fix would corrupt them
    if _has_hungarian_chars(text):
        return text

    sr_dict = _load_serbian_dict()

    def _fix_word(word: str) -> str:
        """Fix a single word using dictionary lookup with case preservation."""
        # Exact match
        if word in sr_dict:
            return sr_dict[word]
        # Lowercase match with case transfer
        lower = word.lower()
        if lower in sr_dict:
            fixed = sr_dict[lower]
            if word[0].isupper():
                fixed = fixed[0].upper() + fixed[1:]
            if word.isupper():
                fixed = fixed.upper()
            return fixed
        return word

    # Split on word boundaries, fix each word
    result = re.sub(r'[A-Za-zčćšžđČĆŠŽĐa-z]+', lambda m: _fix_word(m.group()), text)

    # Surname ending fallback (for words not in dictionary)
    result = re.sub(r'\b(\w{3,})vic\b', lambda m: m.group(1) + 'vić' if m.group() not in sr_dict else m.group(), result)
    result = re.sub(r'\b(\w{3,})Vic\b', lambda m: m.group(1) + 'Vić' if m.group() not in sr_dict else m.group(), result)
    result = re.sub(r'\b(\w{2,})cic\b', lambda m: m.group(1) + 'čić' if m.group() not in sr_dict else m.group(), result)

    return result



# extract_doktor() removed: dental-specific, not used in current playbooks.


def extract_vlasnik(name: str) -> Tuple[str, str]:
    """Extract owner name from business name. Returns (full, short) or ("", "")."""
    name_lat = cyr_to_lat(name)
    words = name_lat.split()
    if len(words) >= 2:
        last = words[-1]
        biz_words = set(_playbook.get('business_type_words', [])) if _playbook else set()
        biz_words |= {"plus", "pro", "express", "point", "team", "group",
                      "service", "services", "solutions", "pty", "ltd"}
        city_names = {c.lower() for c in CITIES.keys()}
        if (last[0].isupper() and len(last) >= 4
            and last.lower() not in biz_words
            and last.lower() not in city_names):
            return last, last
    return "", ""


def format_mobilni(phone: str) -> Tuple[str, str]:
    """Returns (raw_without_prefix, display_format)"""
    if not phone:
        return "", ""
    prefix = _playbook.get('phone_prefix', '+381') if _playbook else '+381'
    stripped = phone.replace(prefix, "").replace(" ", "").replace("-", "").replace("/", "")
    if stripped.startswith("0"):
        stripped = stripped[1:]
    if len(stripped) < 7:
        return stripped, stripped
    # Format based on country
    if prefix == '+61':
        display = f"0{stripped[:3]} {stripped[3:6]} {stripped[6:]}"
    else:
        display = f"0{stripped[:2]}/{stripped[2:5]}-{stripped[5:]}"
    return stripped, display



def shorten_name(name: str) -> str:
    # Strip niche-specific prefixes from playbook
    strip_prefixes = _playbook.get('name_strip_prefixes', []) if _playbook else []
    for prefix in strip_prefixes:
        nl = name.lower()
        if nl.startswith(prefix.lower()):
            short = name[len(prefix):].strip().strip('"').strip("'")
            if short:
                return short
    if len(name) > 25:
        truncated = name[:25]
        last_space = truncated.rfind(' ')
        if last_space > 10:
            return truncated[:last_space]
        return name[:25]
    return name



# auto_hero() removed: content left empty for AI/Claude Code to fill per-lead.


def get_best_review_quote(reviews: List[Dict], latinize: bool = True) -> Tuple[str, str]:
    for min_r in [5, 4]:
        for r in reviews:
            if r.get("rating", 0) >= min_r and len(r.get("text", "")) > 20:
                text = r["text"]
                end = min(len(text), MAX_QUOTE_CHARS)
                for sep in ['. ', '! ', '? ']:
                    idx = text.find(sep)
                    if 15 < idx < MAX_QUOTE_CHARS:
                        end = idx + 1
                        break
                quote = text[:end].strip()
                author = r.get("author", "Klijent")
                if latinize:
                    quote = cyr_to_lat(quote)
                    author = cyr_to_lat(author)
                return author, quote
    return "", ""


def get_competitor_with_site(comp_report: Dict) -> Tuple[str, str, float]:
    """Returns (full_name, short_name, rating)."""
    for c in comp_report.get("top_konkurenti", []):
        if c.get("ima_sajt"):
            full = c.get("naziv", "")
            short = shorten_name(full) if full else ""
            return full, short, c.get("rating", 0)
    return "", "", 0



# generate_faq() removed: content left empty for AI/Claude Code to fill per-lead.


# ============================================================
# SCHEMA DRAFT - render.py compatible, 90% pre-filled
# ============================================================

def _clean_unicode_quotes(s: str) -> str:
    """Replace Unicode quotation marks with ASCII apostrophe for JSON safety."""
    if not isinstance(s, str):
        return s
    return s.replace('\u201E', "'").replace('\u201C', "'").replace('\u201D', "'").replace('\u201F', "'")


def generate_schema_draft(lead: 'Lead') -> Dict:
    # Pre-transliterate Cyrillic fields from Google
    name = cyr_to_lat(lead.name) if lead.name else lead.name
    address = cyr_to_lat(lead.address) if lead.address else lead.address
    
    slug = make_slug(name, lead.city, lead.district, lead.place_id)
    vlasnik, vlasnik_kratko = extract_vlasnik(name)
    mobilni_raw, mobilni_display = format_mobilni(lead.mobile)
    naziv_kratak = shorten_name(name)
    ra = lead.review_analysis or {}

    # Reviews (real data, not template content)
    recenzije = []
    for r in lead.reviews:
        if r.get("rating", 0) >= 4 and len(r.get("text", "")) > 20:
            recenzije.append({"reviewer_name": cyr_to_lat(r.get("author", "Customer")), "text": _clean_unicode_quotes(cyr_to_lat(r.get("text", "")))[:300], "rating": r.get("rating", 5)})

    # Radno vreme
    radno_vreme = []
    for line in lead.opening_hours:
        parts = line.split(": ", 1)
        if len(parts) == 2:
            radno_vreme.append({"day": parts[0], "time": parts[1]})

    draft = {
        # REQUIRED
        "base_url": f"{DEPLOY_BASE_URL}/{slug}",
        "slug": slug,
        "name": name,
        "name_short": naziv_kratak,
        "owner": vlasnik or "_POPUNI_ime_vlasnika_",
        "owner_short": vlasnik_kratko or "_POPUNI_Prezime_",
        "city": lead.city,
        "district": lead.district if (lead.district and lead.district != lead.city) else "",
        "is_city_level": not lead.district or lead.district == lead.city,
        "address": address,
        "phone": mobilni_raw,
        "phone_display": mobilni_display,
        "rating": lead.rating,
        "review_count": lead.review_count,
        "hero_headline": "",
        "hero_subtitle": "",
        # REQUIRED arrays (content left empty for AI/Claude Code to fill)
        "benefits": [],
        "services": [],
        "problems": [],
        "reviews": recenzije[:5],
        "faq": [],
        # OPTIONAL
        "email": lead.email or "",
        "facebook": lead.facebook or "",
        "instagram": lead.instagram or "",
        "google_maps_url": lead.google_maps_url or "",
        "hero_image": f"photos/{os.path.basename(lead.photos[0])}" if lead.photos else "",
        "years_established": lead.years_in_business if lead.years_in_business and lead.years_in_business >= 5 else "",
        "founded": "",
        "specialization": ", ".join(lead.specialties) if lead.specialties else "",
        "hours": radno_vreme,
        "core_values": [],
        "about_story": "",
        "about_subtitle": "", "services_subtitle": "", "contact_subtitle": "",
        "name_genitive": "", "name_locative": "",
        "benefits_headline": "", "benefits_subtitle": "",
        # META (render.py ignores _ keys)
        "_generated": datetime.now().isoformat(),
        "_scraper_version": "7.0-playbook",
        "_schema_type": _playbook.get('schema_type', 'LocalBusiness') if _playbook else 'LocalBusiness',
        "_niche": _playbook.get('niche', '') if _playbook else '',
        "_score": lead.score,
        "_category": lead.category,
        "_review_keywords": [k.get("keyword") for k in ra.get("top_keywords", [])],
        "_needs_review": ["owner"] if not vlasnik else [],
    }

    # Validate schema structure (warn, don't block)
    try:
        from models import SchemaDraft
        SchemaDraft(**draft)
    except Exception as e:
        log.warning("Schema validation warning for %s: %s", draft.get("name", "?"), e)

    return draft



# Outreach generation removed: handled per-lead in Claude Code


# ============================================================
# CLAUDE_PROMPT.md - per-lead instructions for Claude Code
# ============================================================

def generate_claude_prompt(lead: 'Lead', folder_name: str) -> str:
    rev_text = ""
    for r in lead.reviews:
        if r.get("text"):
            stars = "★" * int(r.get("rating", 5))
            rev_text += f"- {r.get('author', '?')} {stars}: {r.get('text', '')[:200]}\n"

    kw_text = ""
    for kw in (lead.review_analysis or {}).get("top_keywords", []):
        kw_text += f"- {kw.get('keyword', '?')} ({kw.get('count', 0)}x)\n"

    cr = lead.competitor_report or {}
    comp_text = ""
    for c in cr.get("top_konkurenti", cr.get("top_competitors", [])):
        sajt = "has site" if c.get("ima_sajt", c.get("has_site")) else "no site"
        comp_text += f"- {c.get('naziv', c.get('name', '?'))} ({c.get('rating', 0)}★, {c.get('recenzija', c.get('reviews', 0))} rev, {sajt})\n"

    niche = _playbook.get('niche', 'business') if _playbook else 'business'
    specs = ", ".join(lead.specialties) if lead.specialties else niche

    return f"""Process this lead following the 3-phase workflow in CLAUDE.md.

## LEAD DATA
Name: {lead.name}
City: {lead.city}
District: {lead.district or "N/A"}
Address: {lead.address}
Mobile: {lead.mobile or "N/A"}
Phone: {lead.phone or "N/A"}
Email: {lead.email or "N/A"}
Website: {lead.website or "NONE"}
Google Maps: {lead.google_maps_url}
Rating: {lead.rating} ({lead.review_count} reviews)
Specializations: {specs}
Years in business: {lead.years_in_business or "Unknown"}
Score: {lead.score}/100 ({lead.category})

## REVIEWS
{rev_text or "No reviews available."}

## REVIEW KEYWORDS
{kw_text or "Not enough data."}

## COMPETITION ({cr.get('kvart', cr.get('area', lead.district or lead.city))})
Total: {cr.get('total_u_kvartu', cr.get('total_in_area', '?'))} | With site: {cr.get('sa_sajtom', cr.get('with_site', '?'))} | Without site: {cr.get('bez_sajta', cr.get('without_site', '?'))}
{comp_text or "No data."}
"""


# ============================================================
# BRIEF.md - compact lead summary
# ============================================================

def generate_brief(lead: 'Lead') -> str:
    wa_num = lead.mobile.replace('+', '').replace(' ', '') if lead.mobile else ""
    wa = f"[WhatsApp](https://wa.me/{wa_num})" if wa_num else "N/A"
    viber = f"[Viber](viber://chat?number=%2B{wa_num})" if wa_num else "N/A"
    niche = _playbook.get('niche', 'business') if _playbook else 'business'
    specs = ", ".join(lead.specialties) if lead.specialties else niche
    kvart_display = lead.district if (lead.district and lead.district != lead.city) else "-"

    signals = []
    if not lead.website and lead.rating >= 4.5 and lead.review_count >= 20:
        signals.append("DIGITAL GAP")
    if lead.website and hasattr(lead, 'site_quality') and lead.site_quality and lead.site_quality.get("is_bad"):
        signals.append("BAD SITE")
    if any(loc.lower() in (lead.address or "").lower() for loc in (_playbook.get('premium_locations', []) if _playbook else [])):
        signals.append("Premium")

    signal_str = " | ".join(signals) if signals else ""

    return f"""# {lead.name} ({lead.score}pts {lead.category})
{signal_str}

| Field | Value |
|-------|-------|
| City / District | {lead.city} / {kvart_display} |
| Address | {lead.address} |
| Mobile | {lead.mobile or "N/A"} |
| Phone | {lead.phone or "N/A"} |
| Email | {lead.email or "N/A"} |
| Rating | {lead.rating} ({lead.review_count} rev) |
| Specializations | {specs} |
| Website | {lead.website or "NONE"} |
| {wa} | {viber} |

## FILES
| File | What |
|------|------|
| CLAUDE_PROMPT.md | Paste into Claude Code |
| schema_draft.json | Data only, fill content |
| data.json | Raw data |
"""


def write_dashboard(qualified: list, filepath: str):
    hot = [l for l in qualified if l.category == "HOT"]
    warm = [l for l in qualified if l.category == "WARM"]
    cool = [l for l in qualified if l.category == "COOL"]
    with_mob = [l for l in qualified if l.mobile]
    no_site = [l for l in qualified if not l.website]
    
    lines = [
        f"# DASHBOARD - {datetime.now().strftime('%Y-%m-%d')}",
        f"\n## Brojevi",
        f"- Ukupno: **{len(qualified)}**",
        f"- HOT: **{len(hot)}** | WARM: **{len(warm)}** | COOL: **{len(cool)}**",
        f"- Sa mobilnim: **{len(with_mob)}** ({len(with_mob)*100//max(len(qualified),1)}%)",
        f"- Bez sajta: **{len(no_site)}** ({len(no_site)*100//max(len(qualified),1)}%)",
        f"\n## TOP 10 (HOT + WhatsApp)",
    ]
    top = sorted([l for l in hot if l.mobile], key=lambda x: x.score, reverse=True)[:10]
    for i, l in enumerate(top, 1):
        wa = l.mobile.replace("+", "")
        lines.append(f"{i}. **{l.name}** - {l.score}pts - {l.rating}★ ({l.review_count} rec) - [WhatsApp](https://wa.me/{wa})")
    
    lines.append(f"\n## Po gradu")
    cities = {}
    for l in qualified:
        cities.setdefault(l.city, []).append(l)
    for c, ls in sorted(cities.items(), key=lambda x: -len(x[1])):
        h = len([l for l in ls if l.category == "HOT"])
        lines.append(f"- **{c}**: {len(ls)} (HOT:{h})")
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines))


# ============================================================
# CHECKPOINT SYSTEM (FIX: resume posle crash-a)
# ============================================================

CHECKPOINT_DIR = "auto-leads-checkpoint"

def save_checkpoint(step: int, data: dict, api_calls: int = 0, api_cost: float = 0.0):
    """Save progress after each step. Includes step number and serialized leads."""
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    checkpoint = {
        "step": step,
        "timestamp": datetime.now().isoformat(),
        "api_calls": api_calls,
        "api_cost": api_cost,
        "data": data,
    }
    filepath = os.path.join(CHECKPOINT_DIR, f"step_{step}.json")
    tmp_path = filepath + ".tmp"
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(checkpoint, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, filepath)
    log.info(f"Checkpoint saved: step {step} ({filepath})")

def load_checkpoint(step: int) -> Optional[dict]:
    """Load checkpoint for a specific step. Returns None if not found."""
    filepath = os.path.join(CHECKPOINT_DIR, f"step_{step}.json")
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        log.info(f"📂 Checkpoint loaded: step {step} (saved {data.get('timestamp', '?')})")
        return data
    except Exception as e:
        log.warning(f"⚠️ Failed to load checkpoint step {step}: {e}")
        return None

def leads_to_dicts(leads: dict) -> list:
    """Serialize leads dict (place_id -> Lead) to JSON-safe list"""
    result = []
    for pid, lead in leads.items():
        d = asdict(lead)
        d['_place_id_key'] = pid
        result.append(d)
    return result

def dicts_to_leads(data: list) -> dict:
    """Deserialize list of dicts back to place_id -> Lead dict"""
    result = {}
    for d in data:
        pid = d.pop('_place_id_key', d.get('place_id', ''))
        lead = Lead(**{k: v for k, v in d.items() if k in Lead.__dataclass_fields__})
        result[pid] = lead
    return result

def leads_list_to_dicts(leads: list) -> list:
    """Serialize list of Leads to JSON-safe list"""
    return [asdict(lead) for lead in leads]

def dicts_to_leads_list(data: list) -> list:
    """Deserialize list of dicts back to list of Leads"""
    return [Lead(**{k: v for k, v in d.items() if k in Lead.__dataclass_fields__}) for d in data]


# ============================================================
# MAIN PIPELINE
# ============================================================

def main(target: int = 300, api_key: str = "", cities_filter: str = "top8", resume: bool = False, no_clean: bool = False):
    # Load .env file if it exists (manual parsing, no dependency on python-dotenv)
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, 'r') as _ef:
            for _line in _ef:
                _line = _line.strip()
                if _line and not _line.startswith('#') and '=' in _line:
                    _k, _v = _line.split('=', 1)
                    os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

    if api_key:
        log.warning("API key u CLI argumentima je vidljiv u shell history. Koristi .env fajl.")
    if not api_key:
        api_key = GOOGLE_API_KEY
    if not api_key:
        api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        print("No API key! Use --key YOUR_KEY or set GOOGLE_API_KEY env var")
        return
    
    # Parse cities (take first N from playbook, works for any country)
    city_keys = list(CITIES.keys())
    if cities_filter == "top4":
        active = {k: CITIES[k] for k in city_keys[:4]}
    elif cities_filter == "top8":
        active = {k: CITIES[k] for k in city_keys[:8]}
    elif cities_filter == "all":
        active = CITIES
    else:
        names = [c.strip() for c in cities_filter.split(",")]
        active = {k: v for k, v in CITIES.items() if k in names}
    
    niche_label = _playbook.get('niche', 'unknown') if _playbook else 'unknown'
    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║          MONEY MAKER LEAD SCRAPER                                ║
║          Niche: {niche_label:12} | Cities: {len(active)} | Target: {target}              ║
╚══════════════════════════════════════════════════════════════════╝
    """)
    
    api = PlacesAPI(api_key)
    web = WebEnricher()
    verifier = DirectoryVerifier()
    
    all_leads = {}  # place_id -> Lead
    
    # ================================================================
    # STEP 1: SEARCH
    # ================================================================
    step1_ck = load_checkpoint(1) if resume else None
    if step1_ck:
        all_leads = dicts_to_leads(step1_ck["data"])
        api.call_count = step1_ck.get("api_calls", 0)
        api.cost_estimate = step1_ck.get("api_cost", 0.0)
        print(f"[1/6] ⏩ Loaded from checkpoint ({len(all_leads)} leads)\n")
    else:
        print("[1/6] Google Places Search...\n")
    
    if not step1_ck:
        for city, coords in active.items():
            lat, lng = coords.split(',')
            city_cyr = CITIES_CYRILLIC.get(city, city)
            
            # Latin queries
            for template in SEARCH_QUERIES_LATIN:
                query = template.format(city=city)
                results = api.text_search(query, lat, lng)
                new = 0
                for r in results:
                    pid = r.get("place_id")
                    if pid and pid not in all_leads:
                        lead = Lead(
                            name=r.get("name", ""),
                            city=city,
                            address=r.get("formatted_address", ""),
                            rating=r.get("rating", 0.0),
                            review_count=r.get("user_ratings_total", 0),
                            place_id=pid,
                            business_status=r.get("business_status", ""),
                        )
                        all_leads[pid] = lead
                        new += 1
                if new > 0:
                    print(f"  📍 {city} '{query}' → +{new}")
                time.sleep(0.2)
            
            # Cyrillic queries
            for template in SEARCH_QUERIES_CYRILLIC:
                query = template.format(city=city_cyr)
                results = api.text_search(query, lat, lng)
                new = 0
                for r in results:
                    pid = r.get("place_id")
                    if pid and pid not in all_leads:
                        lead = Lead(
                            name=r.get("name", ""),
                            city=city,
                            address=r.get("formatted_address", ""),
                            rating=r.get("rating", 0.0),
                            review_count=r.get("user_ratings_total", 0),
                            place_id=pid,
                            business_status=r.get("business_status", ""),
                        )
                        all_leads[pid] = lead
                        new += 1
                if new > 0:
                    print(f"  📍 {city} '{query}' → +{new}")
                time.sleep(0.2)
        
        # Save checkpoint after step 1
        save_checkpoint(1, leads_to_dicts(all_leads), api.call_count, api.cost_estimate)
    
    print(f"\n✅ Unique places found: {len(all_leads)}")
    print(f"   API calls: {api.call_count} | Est. cost: ${api.cost_estimate:.2f}")
    
    # ================================================================
    # STEP 2: GET DETAILS
    # ================================================================
    leads_list = sorted(all_leads.values(), key=lambda x: (x.rating, x.review_count), reverse=True)
    detail_count = min(target * 2, len(leads_list))
    leads_to_detail = leads_list[:detail_count]
    
    serpapi_key = ""  # SerpApi removed: Claude Code reads reviews per-lead

    step2_ck = load_checkpoint(2) if resume else None
    if step2_ck:
        leads_to_detail = dicts_to_leads_list(step2_ck["data"])
        api.call_count = step2_ck.get("api_calls", 0)
        api.cost_estimate = step2_ck.get("api_cost", 0.0)
        # Reclassify phone/mobile using current playbook (fixes stale checkpoint data)
        reclass_count = 0
        for lead in leads_to_detail:
            if lead.phone and not lead.mobile and is_mobile(lead.phone):
                lead.mobile = lead.phone
                lead.contact_sources.setdefault("mobile", []).extend(lead.contact_sources.pop("phone", []))
                lead.phone = ""
                reclass_count += 1
        print(f"\n[2/6] ⏩ Loaded from checkpoint ({len(leads_to_detail)} leads)")
        if reclass_count:
            print(f"       Reclassified {reclass_count} phones → mobile (playbook update)")
        print()
    else:
        print(f"\n[2/6] Getting Details (top {detail_count})...\n")
        
        for lead in tqdm(leads_to_detail, desc="  Details"):
            details = api.get_details(lead.place_id)
            if not details:
                continue
            
            # Business status
            lead.business_status = details.get("business_status", lead.business_status)
            
            # Phone
            phone_raw = details.get("international_phone_number") or details.get("formatted_phone_number", "")
            if phone_raw:
                n = normalize_phone(phone_raw)
                if n:
                    if is_mobile(n):
                        lead.mobile = n
                        lead.contact_sources.setdefault("mobile", []).append("google_api")
                    else:
                        lead.phone = n
                        lead.contact_sources.setdefault("phone", []).append("google_api")
            
            # Website, URL, Address, Rating
            lead.website = details.get("website", "")
            # FIX: Facebook/Instagram nije pravi sajt
            if lead.website:
                w = lead.website.lower()
                if "facebook.com" in w or "fb.com" in w:
                    lead.facebook = lead.facebook or lead.website
                    lead.website = ""
                elif "instagram.com" in w:
                    lead.instagram = lead.instagram or lead.website
                    lead.website = ""
                elif "linktr.ee" in w or "linktree.com" in w:
                    lead.website = ""
            lead.google_maps_url = details.get("url", "")
            lead.address = details.get("formatted_address", lead.address)
            lead.rating = details.get("rating", lead.rating)
            lead.review_count = details.get("user_ratings_total", lead.review_count)
            
            # District
            lead.district = extract_district(lead.address, lead.city)
            
            # Opening hours
            oh = details.get("opening_hours", {})
            lead.opening_hours = oh.get("weekday_text", [])
            
            # Photos
            photos = details.get("photos", [])
            photo_refs = [p.get("photo_reference") for p in photos[:10] if p.get("photo_reference")]
            if photos and not photo_refs:
                log.warning(f"Photos exist but no photo_reference for {lead.name}")
            lead.photo_urls = photo_refs
            
            # Reviews (Places API - max 5)
            reviews_raw = details.get("reviews", [])
            lead.reviews = []
            for rev in reviews_raw[:5]:
                lead.reviews.append({
                    "author": rev.get("author_name", ""),
                    "rating": rev.get("rating", 5),
                    "text": rev.get("text", ""),
                    "time": rev.get("time", 0),
                    "relative_time": rev.get("relative_time_description", ""),
                })

            # Specialties & years
            lead.specialties = detect_specialties(lead)
            lead.years_in_business = estimate_years(lead)
            
            time.sleep(0.1)
    
        # Save checkpoint after step 2
        save_checkpoint(2, leads_list_to_dicts(leads_to_detail), api.call_count, api.cost_estimate)
    
    # Early filter: remove closed businesses before spending time on web enrichment
    before_filter = len(leads_to_detail)
    leads_to_detail = [l for l in leads_to_detail if l.business_status not in ("CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY")]
    closed_count = before_filter - len(leads_to_detail)
    if closed_count:
        print(f"   🚫 Filtered {closed_count} closed businesses")

    # Dedup: same mobile phone = same business (clinic chains with multiple listings)
    seen_mobiles = {}
    seen_addresses = {}
    deduped = []
    dup_count = 0
    for lead in leads_to_detail:
        is_dup = False
        if lead.mobile and lead.mobile in seen_mobiles:
            is_dup = True
            # Keep the one with more reviews
            existing = seen_mobiles[lead.mobile]
            if lead.review_count > existing.review_count:
                deduped.remove(existing)
                deduped.append(lead)
                seen_mobiles[lead.mobile] = lead
            dup_count += 1
            continue
        # Address dedup (normalize: strip spaces, lowercase)
        addr_key = re.sub(r'\s+', ' ', lead.address.lower().strip()) if lead.address else ""
        if addr_key and len(addr_key) > 10 and addr_key in seen_addresses:
            is_dup = True
            existing = seen_addresses[addr_key]
            if lead.review_count > existing.review_count:
                deduped.remove(existing)
                deduped.append(lead)
                seen_addresses[addr_key] = lead
            dup_count += 1
            continue
        deduped.append(lead)
        if lead.mobile:
            seen_mobiles[lead.mobile] = lead
        if addr_key and len(addr_key) > 10:
            seen_addresses[addr_key] = lead
    leads_to_detail = deduped
    if dup_count:
        print(f"   🔄 Deduplicated {dup_count} duplicate leads (same phone/address)")
    print(f"   API calls: {api.call_count} | Est. cost: ${api.cost_estimate:.2f}")

    # ================================================================
    # STEP 3: WEB ENRICHMENT + SITE QUALITY
    # ================================================================
    step3_ck = load_checkpoint(3) if resume else None
    if step3_ck:
        leads_to_detail = dicts_to_leads_list(step3_ck["data"])
        # Reclassify phone/mobile using current playbook (fixes stale checkpoint data)
        for lead in leads_to_detail:
            if lead.phone and not lead.mobile and is_mobile(lead.phone):
                lead.mobile = lead.phone
                lead.contact_sources.setdefault("mobile", []).extend(lead.contact_sources.pop("phone", []))
                lead.phone = ""
        print(f"\n[3/6] ⏩ Loaded from checkpoint\n")
    else:
        print(f"\n[3/6] Web Enrichment + Site Quality Check...\n")
        
        web_session = requests.Session()
        web_session.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"})
    
        for lead in tqdm(leads_to_detail, desc="  Websites"):
            if lead.website:
                # Enrich from website
                data = web.enrich(lead.website)
                
                if data['mobile'] and not lead.mobile:
                    lead.mobile = data['mobile']
                    lead.contact_sources.setdefault("mobile", []).append("website")
                elif data['mobile'] and lead.mobile and data['mobile'] == lead.mobile:
                    lead.contact_sources.setdefault("mobile", []).append("website")
                
                if data['emails']:
                    lead.email = data['emails'][0]
                    lead.contact_sources.setdefault("email", []).append("website")
                
                if data['facebook']:
                    lead.facebook = data['facebook']
                if data['instagram']:
                    lead.instagram = data['instagram']
                
                for p in data['phones']:
                    np_val = normalize_phone(p)
                    if np_val and is_mobile(np_val) and not lead.mobile:
                        lead.mobile = np_val
                        lead.contact_sources.setdefault("mobile", []).append("website")
                
                # FIX: Site quality check - preskoči ako je SSL već failovao
                if data.get("ssl_failed"):
                    lead.site_quality = {"quality_score": 20, "is_bad": True, "issues": ["ssl_error"], "checked": True}
                else:
                    lead.site_quality = check_site_quality(lead.website, web_session)
                    lead.site_content = lead.site_quality.pop("site_content", "")
        
        # FIX: Log blocked domains summary
        if web.failed_domains:
            print(f"\n   ⚠️ SSL/Connection blocked domains ({len(web.failed_domains)}): {', '.join(list(web.failed_domains)[:10])}")
        
        # Save checkpoint after step 3
        save_checkpoint(3, leads_list_to_dicts(leads_to_detail))
    
    # ================================================================
    # STEP 4: VERIFICATION (with checkpoint)
    # ================================================================
    step4_ck = load_checkpoint(4) if resume else None
    if step4_ck:
        leads_to_detail = dicts_to_leads_list(step4_ck["data"])
        # Reclassify phone/mobile using current playbook (fixes stale checkpoint data)
        for lead in leads_to_detail:
            if lead.phone and not lead.mobile and is_mobile(lead.phone):
                lead.mobile = lead.phone
                lead.contact_sources.setdefault("mobile", []).extend(lead.contact_sources.pop("phone", []))
                lead.phone = ""
        print(f"\n[4/6] ⏩ Loaded from checkpoint ({len(leads_to_detail)} leads)\n")
    else:
        print(f"\n[4/6] Cross-referencing & Verification...\n")

        verify_count = min(MAX_VERIFY_COUNT, len(leads_to_detail))
        for lead in tqdm(leads_to_detail[:verify_count], desc="  Verifying"):
            gdata = verifier.search_google(lead.name, lead.city)

            for p in gdata['phones']:
                np_val = normalize_phone(p)
                if np_val and is_mobile(np_val):
                    if not lead.mobile:
                        lead.mobile = np_val
                        lead.contact_sources.setdefault("mobile", []).append("google_search")
                    elif lead.mobile == np_val:
                        lead.contact_sources.setdefault("mobile", []).append("google_search")

            for e in gdata['emails']:
                if not lead.email:
                    lead.email = e
                    lead.contact_sources.setdefault("email", []).append("google_search")

            if gdata['facebook'] and not lead.facebook:
                lead.facebook = gdata['facebook']
            if gdata['instagram'] and not lead.instagram:
                lead.instagram = gdata['instagram']

            # Verify from multiple sources
            mobile_sources = lead.contact_sources.get("mobile", [])
            if len(set(mobile_sources)) >= 2:
                lead.verified = True

            time.sleep(0.5)

        # Save checkpoint after step 4
        save_checkpoint(4, leads_list_to_dicts(leads_to_detail))
    
    # ================================================================
    # STEP 5: QUALIFY, ANALYZE, SCORE
    # ================================================================
    print(f"\n[5/6] Qualification + Analysis + Scoring...\n")
    
    qualified = []
    disqualified = {"gate1": 0, "gate2": 0, "gate3": 0, "gate4": 0}
    
    for lead in leads_to_detail:
        passed, reason = qualify_lead(lead)
        if not passed:
            gate = reason.split("_")[0]
            disqualified[gate] = disqualified.get(gate, 0) + 1
            continue
        
        # Review analysis
        lead.review_analysis = analyze_reviews(lead.reviews)
        
        # Score
        lead = score_lead(lead, PREMIUM_LOCATIONS)
        qualified.append(lead)
    
    # Build competitor reports (needs all qualified leads)
    build_competitor_reports(qualified)
    
    # Re-sort by score
    qualified.sort(key=lambda x: x.score, reverse=True)
    qualified = qualified[:target]

    # Stats
    hot = len([l for l in qualified if l.category == "HOT"])
    warm = len([l for l in qualified if l.category == "WARM"])
    cool = len([l for l in qualified if l.category == "COOL"])
    verified_count = len([l for l in qualified if l.verified])
    
    print(f"  Qualified: {len(qualified)} | HOT:{hot} WARM:{warm} COOL:{cool}")
    print(f"  Verified: {verified_count}")
    print(f"  Disqualified: {sum(disqualified.values())} (gate1:{disqualified['gate1']} gate2:{disqualified['gate2']} gate3:{disqualified['gate3']} gate4:{disqualified['gate4']})")
    
    # ================================================================
    # STEP 6: OUTPUT (v6.1 - schema + outreach + claude prompt)
    # ================================================================
    print(f"\n[6/6] Generating Output...\n")
    
    output_dir = str(LEADS_DIR)
    if os.path.exists(output_dir):
        if no_clean:
            log.info(f"--no-clean: preskačem brisanje {output_dir}/")
        else:
            print(f"{output_dir}/ već postoji. Brišem i kreiram ponovo.")
            shutil.rmtree(output_dir)
    
    for cat in ["HOT", "WARM", "COOL"]:
        os.makedirs(f"{output_dir}/{cat}", exist_ok=True)
    
    counts = {"HOT": 0, "WARM": 0, "COOL": 0}
    
    for lead in tqdm(qualified, desc="  Output"):
        counts[lead.category] += 1
        i = counts[lead.category]
        safe = re.sub(r'[^\w\s-]', '', lead.name)[:30].replace(' ', '_')
        folder = f"{output_dir}/{lead.category}/{i:03d}_{safe}_{lead.score}pts"
        os.makedirs(folder, exist_ok=True)
        
        # Download photos
        photos_dir = os.path.join(folder, "photos")
        os.makedirs(photos_dir, exist_ok=True)
        downloaded = []
        for j, ref in enumerate(lead.photo_urls[:10], 1):
            filepath = os.path.join(photos_dir, f"photo_{j:02d}.jpg")
            if api.download_photo(ref, filepath):
                downloaded.append(filepath)
        lead.photos = downloaded
        
        # BRIEF.md (compact, action-oriented)
        brief = generate_brief(lead)
        with open(os.path.join(folder, "BRIEF.md"), 'w', encoding='utf-8') as f:
            f.write(brief)
        
        # data.json (raw scraped data)
        with open(os.path.join(folder, "data.json"), 'w', encoding='utf-8') as f:
            json.dump(asdict(lead), f, ensure_ascii=False, indent=2)
        
        # schema_draft.json (render.py compatible, 90% pre-filled)
        schema = generate_schema_draft(lead)
        with open(os.path.join(folder, "schema_draft.json"), 'w', encoding='utf-8') as f:
            json.dump(schema, f, ensure_ascii=False, indent=2)
        
        # review_analysis.json
        with open(os.path.join(folder, "review_analysis.json"), 'w', encoding='utf-8') as f:
            json.dump(lead.review_analysis, f, ensure_ascii=False, indent=2)
        
        # competitor_report.json
        with open(os.path.join(folder, "competitor_report.json"), 'w', encoding='utf-8') as f:
            json.dump(lead.competitor_report, f, ensure_ascii=False, indent=2)
        
        # CLAUDE_PROMPT.md (paste into Claude → finish site)
        prompt = generate_claude_prompt(lead, folder)
        with open(os.path.join(folder, "CLAUDE_PROMPT.md"), 'w', encoding='utf-8') as f:
            f.write(prompt)
    
    # ================================================================
    # GLOBAL FILES
    # ================================================================
    
    # _DASHBOARD.md - stats + top 10
    write_dashboard(qualified, f"{output_dir}/_DASHBOARD.md")
    
    # HubSpot CSV
    csv_path = f"{output_dir}/hubspot_import.csv"
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(["Name", "Mobile", "Email", "Website", "City", "District", "Address",
                    "Rating", "Reviews", "Score", "Category", "Verified",
                    "Specialties", "Years_Est", "Has_Reviews", "Site_Quality",
                    "Google Maps", "Facebook", "Instagram"])
        for l in qualified:
            w.writerow([l.name, l.mobile, l.email, l.website, l.city, l.district, l.address,
                       l.rating, l.review_count, l.score, l.category, l.verified,
                       "|".join(l.specialties), l.years_in_business,
                       "YES" if l.reviews else "NO",
                       l.site_quality.get("quality_score", "N/A"),
                       l.google_maps_url, l.facebook, l.instagram])
    
    # Pipeline tracker
    pipeline = {
        "generated": datetime.now().isoformat(),
        "scraper_version": "7.0-playbook",
        "niche": _playbook.get('niche', '') if _playbook else '',
        "cities_scraped": list(active.keys()),
        "total_searched": len(all_leads),
        "total_qualified": len(qualified),
        "disqualified": disqualified,
        "by_category": {
            "HOT": {"count": hot, "leads": [l.name for l in qualified if l.category == "HOT"]},
            "WARM": {"count": warm, "leads": [l.name for l in qualified if l.category == "WARM"]},
            "COOL": {"count": cool, "leads": [l.name for l in qualified if l.category == "COOL"]},
        },
        "api_usage": {
            "total_calls": api.call_count,
            "estimated_cost": round(api.cost_estimate, 2),
        },
        "stats": {
            "with_mobile": len([l for l in qualified if l.mobile]),
            "with_email": len([l for l in qualified if l.email]),
            "with_website": len([l for l in qualified if l.website]),
            "without_website": len([l for l in qualified if not l.website]),
            "with_bad_website": len([l for l in qualified if l.site_quality.get("is_bad")]),
            "with_reviews": len([l for l in qualified if l.reviews]),
            "with_specialties": len([l for l in qualified if l.specialties]),
            "verified": verified_count,
        }
    }
    with open(f"{output_dir}/_pipeline.json", 'w', encoding='utf-8') as f:
        json.dump(pipeline, f, ensure_ascii=False, indent=2)
    
    # ================================================================
    # PRINT SUMMARY
    # ================================================================
    
    # By city
    print(f"\n📊 Po gradu:")
    cc = {}
    for l in qualified:
        cc[l.city] = cc.get(l.city, 0) + 1
    for c, n in sorted(cc.items(), key=lambda x: -x[1]):
        print(f"   {c}: {n}")
    
    # Contact stats
    print(f"\n📊 Kontakt:")
    print(f"   Sa mobilnim: {pipeline['stats']['with_mobile']}")
    print(f"   Sa emailom: {pipeline['stats']['with_email']}")
    print(f"   Bez sajta: {pipeline['stats']['without_website']}")
    print(f"   Sa lošim sajtom: {pipeline['stats']['with_bad_website']}")
    print(f"   Verifikovano: {verified_count}")
    
    # Top 10
    print(f"\n🏆 TOP 10:")
    for i, l in enumerate(qualified[:10], 1):
        gap = "🔥" if not l.website else ("⚠️" if l.site_quality.get("is_bad") else "  ")
        mob = "📱" if l.mobile else "  "
        print(f"   {i:2}. [{l.score:2}] {l.name[:35]:35} ({l.city}) {l.rating}★ {l.review_count}rec {gap}{mob}")
    
    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║                          ✅ GOTOVO!                               ║
╠══════════════════════════════════════════════════════════════════╣
║  Ukupno:      {len(qualified):4}  (🔥 HOT:{hot:3} | 🟡 WARM:{warm:3} | 🔵 COOL:{cool:3})     ║
║  Verifikovano: {verified_count:3}                                                ║
║  API pozivi:   {api.call_count:5}  (~${api.cost_estimate:.2f})                          ║
║                                                                  ║
║  Output:     {str(LEADS_DIR):54} ║
║  Dashboard:  _DASHBOARD.md                                       ║
║  Tracker:    _DASHBOARD.md                                       ║
║  CSV:        hubspot_import.csv                                  ║
║  Pipeline:   _pipeline.json                                      ║
║                                                                  ║
║  Per lead:   BRIEF.md | CLAUDE_PROMPT.md                         ║
║              schema_draft.json | data.json | photos/             ║
╚══════════════════════════════════════════════════════════════════╝
    """)

    # Cleanup checkpoints after successful fresh run
    if not resume and os.path.exists(CHECKPOINT_DIR):
        import shutil
        shutil.rmtree(CHECKPOINT_DIR)
        log.info("Checkpoints cleaned up after successful run")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Money Maker Lead Scraper")
    p.add_argument("--playbook", required=True, help="Path to playbook JSON file")
    p.add_argument("--target", type=int, default=300, help="Max number of leads (default: 300)")
    p.add_argument("--key", type=str, default="", help="Google API key (overrides config.GOOGLE_API_KEY)")
    p.add_argument("--cities", type=str, default="top8",
                   help="'top4', 'top8', 'all', or comma-separated: 'Beograd,Novi Sad'")
    p.add_argument("--resume", action="store_true", help="Resume from last checkpoint (skips completed steps)")
    p.add_argument("--no-clean", action="store_true", help="Don't delete output dir before generating (default: clean)")

    a = p.parse_args()
    pb = load_playbook_from_path(a.playbook)
    init_from_playbook(pb)
    main(target=a.target, api_key=a.key, cities_filter=a.cities, resume=a.resume, no_clean=a.no_clean)
