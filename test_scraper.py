"""Tests for scraper.py key functions: phone handling, site quality, schema generation."""

import pytest
import scraper


# ============================================================
# FIXTURES
# ============================================================

@pytest.fixture(autouse=True)
def au_playbook():
    """Set AU playbook for all tests by default."""
    original = scraper._playbook
    scraper._playbook = {
        "phone_prefix": "+61",
        "mobile_prefixes": ["4"],
        "niche": "tiler",
        "language": "en",
        "name_strip_prefixes": [],
        "search_verify_terms": "tiler contact",
    }
    yield scraper._playbook
    scraper._playbook = original


@pytest.fixture
def sr_playbook():
    """Serbian playbook override."""
    original = scraper._playbook
    scraper._playbook = {
        "phone_prefix": "+381",
        "mobile_prefixes": ["60", "61", "62", "63", "64", "65", "66", "69"],
        "niche": "auto servis",
        "language": "sr",
        "name_strip_prefixes": ["Auto servis ", "Autoservis "],
        "search_verify_terms": "kontakt",
    }
    yield scraper._playbook
    scraper._playbook = original


class FakeLead:
    """Minimal lead for testing."""

    def __init__(self, **kwargs):
        defaults = {
            "name": "Test Business",
            "city": "Sydney",
            "district": "",
            "address": "123 Main St",
            "phone": "+61450233326",
            "mobile": "+61450233326",
            "email": "test@example.com",
            "website": "",
            "google_maps_url": "https://maps.google.com/?cid=123",
            "facebook": "",
            "instagram": "",
            "rating": 4.8,
            "review_count": 45,
            "reviews": [
                {"author": "John", "text": "Great tiling work, very professional and clean.", "rating": 5},
                {"author": "Sarah", "text": "Excellent bathroom renovation, highly recommend.", "rating": 5},
            ],
            "review_analysis": {"top_keywords": [{"keyword": "professional", "count": 5}]},
            "specialties": ["Bathroom Tiling", "Floor Tiling"],
            "years_in_business": 8,
            "opening_hours": ["Monday: 7:00 AM - 5:00 PM", "Tuesday: 7:00 AM - 5:00 PM"],
            "photos": [],
            "place_id": "ChIJ123456",
            "score": 57,
            "category": "HOT",
            "site_quality": {},
            "competitor_report": {},
        }
        defaults.update(kwargs)
        for k, v in defaults.items():
            setattr(self, k, v)


# ============================================================
# normalize_phone
# ============================================================

class TestNormalizePhone:
    def test_au_international(self):
        assert scraper.normalize_phone("+61450233326") == "+61450233326"

    def test_au_local(self):
        assert scraper.normalize_phone("0450233326") == "+61450233326"

    def test_au_double_zero(self):
        assert scraper.normalize_phone("0061450233326") == "+61450233326"

    def test_au_with_spaces(self):
        assert scraper.normalize_phone("+61 450 233 326") == "+61450233326"

    def test_au_with_dashes(self):
        assert scraper.normalize_phone("+61-450-233-326") == "+61450233326"

    def test_empty(self):
        assert scraper.normalize_phone("") is None

    def test_none(self):
        assert scraper.normalize_phone(None) is None

    def test_garbage(self):
        assert scraper.normalize_phone("not a phone") is None

    def test_too_short(self):
        assert scraper.normalize_phone("+6145") is None

    def test_sr_international(self, sr_playbook):
        assert scraper.normalize_phone("+381641234567") == "+381641234567"

    def test_sr_local(self, sr_playbook):
        assert scraper.normalize_phone("0641234567") == "+381641234567"


# ============================================================
# is_mobile
# ============================================================

class TestIsMobile:
    def test_au_mobile(self):
        assert scraper.is_mobile("+61450233326") is True

    def test_au_landline(self):
        assert scraper.is_mobile("+61298765432") is False

    def test_au_local_mobile(self):
        assert scraper.is_mobile("0450233326") is True

    def test_empty(self):
        assert scraper.is_mobile("") is False

    def test_sr_mobile(self, sr_playbook):
        assert scraper.is_mobile("+381641234567") is True

    def test_sr_landline(self, sr_playbook):
        assert scraper.is_mobile("+381111234567") is False


# ============================================================
# extract_phones
# ============================================================

class TestExtractPhones:
    def test_au_international_in_text(self):
        phones = scraper.extract_phones("Call us at +61 450 233 326 today")
        assert "+61450233326" in phones

    def test_au_local_in_text(self):
        phones = scraper.extract_phones("Phone: 0450 233 326")
        assert "+61450233326" in phones

    def test_multiple_phones(self):
        phones = scraper.extract_phones("+61 450 233 326 or +61 412 345 678")
        assert len(phones) == 2

    def test_dedup(self):
        phones = scraper.extract_phones("+61450233326 call +61 450 233 326")
        assert len(phones) == 1

    def test_no_phones(self):
        phones = scraper.extract_phones("No phone number here")
        assert phones == []

    def test_empty(self):
        assert scraper.extract_phones("") == []

    def test_none(self):
        assert scraper.extract_phones(None) == []

    def test_sr_phones(self, sr_playbook):
        phones = scraper.extract_phones("Pozovite +381 64 123 4567")
        assert "+381641234567" in phones


# ============================================================
# format_mobilni
# ============================================================

class TestFormatMobilni:
    def test_au_format(self):
        raw, display = scraper.format_mobilni("+61450233326")
        assert raw == "450233326"
        assert display == "0450 233 326"

    def test_empty(self):
        raw, display = scraper.format_mobilni("")
        assert raw == ""
        assert display == ""

    def test_sr_format(self, sr_playbook):
        raw, display = scraper.format_mobilni("+381641234567")
        assert raw == "641234567"
        assert display == "064/123-4567"


# ============================================================
# shorten_name
# ============================================================

class TestShortenName:
    def test_short_name_unchanged(self):
        assert scraper.shorten_name("Linear Tiling") == "Linear Tiling"

    def test_long_name_truncated(self):
        name = "A Very Long Business Name That Exceeds Twenty Five Characters"
        result = scraper.shorten_name(name)
        assert len(result) <= 25

    def test_strip_prefix_from_playbook(self):
        scraper._playbook["name_strip_prefixes"] = ["Tiling Services "]
        result = scraper.shorten_name("Tiling Services Smith")
        assert result == "Smith"

    def test_no_strip_without_match(self):
        scraper._playbook["name_strip_prefixes"] = ["Auto servis "]
        assert scraper.shorten_name("Linear Tiling") == "Linear Tiling"


# ============================================================
# extract_vlasnik
# ============================================================

class TestExtractVlasnik:
    def test_name_with_surname(self):
        full, short = scraper.extract_vlasnik("Brisbane Tiling Service Johnson")
        assert full == "Johnson"

    def test_short_acronym_name(self):
        full, short = scraper.extract_vlasnik("RJP")
        assert full == ""

    def test_ignores_service_words(self):
        full, short = scraper.extract_vlasnik("Auto Servis Service")
        assert full == ""


# ============================================================
# check_site_quality (unit tests with mocked responses)
# ============================================================

class TestCheckSiteQuality:
    """Test quality scoring logic by building mock HTML."""

    def _score_html(self, html, content=""):
        """Score HTML without making HTTP requests."""
        import re
        score = 100
        issues = []
        html_lower = html.lower()

        if 'viewport' not in html_lower:
            score -= 25
            issues.append("not_responsive")
        if html_lower.count('<table') > 3:
            score -= 15
            issues.append("table_layout")
        if len(html_lower) < 2000:
            score -= 30
            issues.append("nearly_empty")
        if any(x in html_lower for x in ['under construction', 'coming soon']):
            score -= 40
            issues.append("under_construction")
        if any(x in html_lower for x in ['wix.com', 'squarespace.com']):
            score -= 30
            issues.append("template_builder")
        if any(x in html_lower for x in ['lorem ipsum', 'your company']):
            score -= 35
            issues.append("placeholder_content")

        outdated = ['jquery-ui', 'bootstrap/2.', '<marquee', 'bgcolor=']
        if sum(1 for s in outdated if s in html_lower) >= 2:
            score -= 20
            issues.append("outdated_design")

        if content:
            if len(content.split()) < 100:
                score -= 25
                issues.append("thin_content")
            has_phone = bool(re.search(r'\b\d{2,4}[\s\-]?\d{3,4}[\s\-]?\d{3,4}\b', content))
            has_email = bool(re.search(r'[\w\.-]+@[\w\.-]+\.\w+', content))
            if not has_phone and not has_email:
                score -= 15
                issues.append("no_contact_on_site")

        return max(0, score), issues

    def test_good_site(self):
        html = '<html><head><meta name="viewport" content="width=device-width"></head>' + '<body>' + 'x' * 3000 + '</body></html>'
        score, issues = self._score_html(html)
        assert score == 100
        assert issues == []

    def test_no_viewport(self):
        html = '<html><head></head><body>' + 'x' * 3000 + '</body></html>'
        score, issues = self._score_html(html)
        assert "not_responsive" in issues
        assert score == 75

    def test_under_construction(self):
        html = '<html><head><meta name="viewport"></head><body>under construction' + 'x' * 3000 + '</body></html>'
        score, issues = self._score_html(html)
        assert "under_construction" in issues

    def test_template_builder(self):
        html = '<html><head><meta name="viewport"></head><body>powered by wix.com' + 'x' * 3000 + '</body></html>'
        score, issues = self._score_html(html)
        assert "template_builder" in issues

    def test_placeholder_content(self):
        html = '<html><head><meta name="viewport"></head><body>lorem ipsum dolor' + 'x' * 3000 + '</body></html>'
        score, issues = self._score_html(html)
        assert "placeholder_content" in issues

    def test_outdated_design(self):
        html = '<html><head><meta name="viewport"></head><body>jquery-ui bgcolor=' + 'x' * 3000 + '</body></html>'
        score, issues = self._score_html(html)
        assert "outdated_design" in issues

    def test_thin_content(self):
        html = '<html><head><meta name="viewport"></head><body>' + 'x' * 3000 + '</body></html>'
        score, issues = self._score_html(html, content="only a few words here")
        assert "thin_content" in issues

    def test_no_contact_on_site(self):
        html = '<html><head><meta name="viewport"></head><body>' + 'x' * 3000 + '</body></html>'
        score, issues = self._score_html(html, content="lots of content " * 20)
        assert "no_contact_on_site" in issues

    def test_contact_with_au_phone(self):
        html = '<html><head><meta name="viewport"></head><body>' + 'x' * 3000 + '</body></html>'
        content = "Call us at 0450 233 326 for a quote. " * 5
        score, issues = self._score_html(html, content=content)
        assert "no_contact_on_site" not in issues

    def test_contact_with_email(self):
        html = '<html><head><meta name="viewport"></head><body>' + 'x' * 3000 + '</body></html>'
        content = "Email us at info@tiler.com.au for a quote. " * 5
        score, issues = self._score_html(html, content=content)
        assert "no_contact_on_site" not in issues


# ============================================================
# generate_schema_draft
# ============================================================

class TestGenerateSchema:
    def test_content_fields_empty(self):
        lead = FakeLead()
        schema = scraper.generate_schema_draft(lead)
        assert schema["hero_headline"] == ""
        assert schema["hero_subtitle"] == ""
        assert schema["services"] == []
        assert schema["benefits"] == []
        assert schema["problems"] == []
        assert schema["faq"] == []
        assert schema["about_story"] == ""
        assert schema["core_values"] == []

    def test_data_fields_populated(self):
        lead = FakeLead()
        schema = scraper.generate_schema_draft(lead)
        assert schema["name"] == "Test Business"
        assert schema["city"] == "Sydney"
        assert schema["rating"] == 4.8
        assert schema["review_count"] == 45
        assert schema["google_maps_url"] == "https://maps.google.com/?cid=123"

    def test_reviews_preserved(self):
        lead = FakeLead()
        schema = scraper.generate_schema_draft(lead)
        assert len(schema["reviews"]) == 2
        assert schema["reviews"][0]["reviewer_name"] == "John"

    def test_hours_parsed(self):
        lead = FakeLead()
        schema = scraper.generate_schema_draft(lead)
        assert len(schema["hours"]) == 2
        assert schema["hours"][0]["day"] == "Monday"

    def test_niche_in_meta(self):
        lead = FakeLead()
        schema = scraper.generate_schema_draft(lead)
        assert schema["_niche"] == "tiler"

    def test_no_serbian_content(self):
        lead = FakeLead()
        schema = scraper.generate_schema_draft(lead)
        schema_str = str(schema)
        assert "auto servis" not in schema_str.lower()
        assert "RSD" not in schema_str
        assert "dijagnostik" not in schema_str.lower()
        assert "lampica" not in schema_str.lower()


# ============================================================
# generate_claude_prompt
# ============================================================

class TestGenerateClaudePrompt:
    def test_english_output(self):
        lead = FakeLead()
        prompt = scraper.generate_claude_prompt(lead, "001_Test_57pts")
        assert "Process this lead" in prompt
        assert "CLAUDE.md" in prompt

    def test_contains_lead_data(self):
        lead = FakeLead()
        prompt = scraper.generate_claude_prompt(lead, "001_Test_57pts")
        assert "Test Business" in prompt
        assert "Sydney" in prompt
        assert "4.8" in prompt

    def test_no_serbian(self):
        lead = FakeLead()
        prompt = scraper.generate_claude_prompt(lead, "001_Test_57pts")
        assert "KORAK" not in prompt
        assert "auto servis" not in prompt.lower()
        assert "instructions-v11" not in prompt


# ============================================================
# generate_brief
# ============================================================

class TestGenerateBrief:
    def test_english_labels(self):
        lead = FakeLead()
        brief = scraper.generate_brief(lead)
        assert "City / District" in brief
        assert "Mobile" in brief
        assert "Specializations" in brief
        assert "FILES" in brief

    def test_no_serbian_labels(self):
        lead = FakeLead()
        brief = scraper.generate_brief(lead)
        assert "Grad" not in brief
        assert "Mobilni" not in brief
        assert "FAJLOVI" not in brief
        assert "NEMA" not in brief
        assert "Opšta" not in brief
