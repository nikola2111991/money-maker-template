"""
test_render.py - Testovi za render.py i utils.py
Pokreni: pytest test_render.py -v
"""
import pytest
import sys
import os

# Dodaj projekat u path
sys.path.insert(0, os.path.dirname(__file__))

from utils import cyr_to_lat, has_cyrillic, strip_diacritics
from render import (
    validate, sanitize_data, sanitize_tema, escape_html_chars,
    is_placeholder, is_valid_url, enrich_schema, _json_val,
    ascii_safe_html,
)


# ═══════════════════════════════════════════
# utils.py
# ═══════════════════════════════════════════

class TestCyrToLat:
    def test_basic(self):
        assert cyr_to_lat("Београд") == "Beograd"

    def test_mixed(self):
        assert cyr_to_lat("Др Марко") == "Dr Marko"

    def test_latin_passthrough(self):
        assert cyr_to_lat("Dr Marko") == "Dr Marko"

    def test_empty(self):
        assert cyr_to_lat("") == ""

    def test_non_string(self):
        assert cyr_to_lat(123) == 123

    def test_digraphs(self):
        assert cyr_to_lat("Љубав Њега Џеп") == "Ljubav Njega Džep"


class TestHasCyrillic:
    def test_cyrillic(self):
        assert has_cyrillic("Београд") is True

    def test_latin(self):
        assert has_cyrillic("Beograd") is False

    def test_empty(self):
        assert has_cyrillic("") is False

    def test_non_string(self):
        assert has_cyrillic(None) is False


class TestStripDiacritics:
    def test_basic(self):
        assert strip_diacritics("šćžčđ") == "sczcd"

    def test_uppercase(self):
        assert strip_diacritics("ŠĆŽČĐ") == "SCZCD"

    def test_mixed(self):
        assert strip_diacritics("Mehaničarska") == "Mehanicarska"


# ═══════════════════════════════════════════
# render.py - Validacija
# ═══════════════════════════════════════════

def _minimal_data() -> dict:
    """Vraća minimalan validan data dict."""
    return {
        "slug": "test-servis",
        "name": "Test Auto Servis",
        "name_short": "Test",
        "owner": "Petrovic",
        "owner_short": "Petrovic",
        "city": "Beograd",
        "address": "Neka ulica 1",
        "rating": 4.8,
        "review_count": 50,
        "hero_headline": "Vaš auto servis",
        "hero_subtitle": "Zakažite danas",
        "phone": "601234567",
        "phone_display": "060/123-4567",
        "benefits": [
            {"title": "B1", "description": "Opis1"},
            {"title": "B2", "description": "Opis2"},
            {"title": "B3", "description": "Opis3"},
        ],
        "services": [{"title": "Mali servis", "description": "Zamena ulja i filtera"}],
        "problems": [
            {"title": "Lampica na tabli", "treatment": "Dijagnostika", "description": "Očitavanje grešaka"},
            {"title": "Čudni zvuci", "treatment": "Pregled motora", "description": "Identifikacija izvora"},
            {"title": "Auto ne startuje", "treatment": "Provera elektrike", "description": "Test akumulatora"},
        ],
        "reviews": [{"reviewer_name": "Pera", "text": "Odlično!"}],
        "faq": [
            {"question": "P1?", "answer": "O1"},
            {"question": "P2?", "answer": "O2"},
            {"question": "P3?", "answer": "O3"},
            {"question": "P4?", "answer": "O4"},
            {"question": "P5?", "answer": "O5"},
        ],
    }


class TestValidate:
    def test_valid_data(self):
        errors = validate(_minimal_data())
        hard = [e for e in errors if e.level == "ERROR"]
        assert len(hard) == 0

    def test_missing_required_field(self):
        data = _minimal_data()
        del data["name"]
        errors = validate(data)
        hard = [e for e in errors if e.level == "ERROR"]
        assert any("name" in e.field for e in hard)

    def test_placeholder_detected(self):
        data = _minimal_data()
        data["name"] = "???"
        errors = validate(data)
        hard = [e for e in errors if e.level == "ERROR"]
        assert any("name" in e.field and "placeholder" in e.msg for e in hard)

    def test_invalid_mobilni_format(self):
        data = _minimal_data()
        data["phone"] = "abc-not-a-phone"
        errors = validate(data)
        hard = [e for e in errors if e.level == "ERROR"]
        assert any("phone" in e.field for e in hard)

    def test_valid_international_phone(self):
        data = _minimal_data()
        data["phone"] = "+61450233326"
        errors = validate(data)
        hard = [e for e in errors if e.level == "ERROR" and "phone" in e.field]
        assert not hard  # international format should be accepted

    def test_rating_out_of_range(self):
        data = _minimal_data()
        data["rating"] = 6.0
        errors = validate(data)
        hard = [e for e in errors if e.level == "ERROR"]
        assert any("rating" in e.field for e in hard)

    def test_slug_format(self):
        data = _minimal_data()
        data["slug"] = "Test Servis!"
        errors = validate(data)
        hard = [e for e in errors if e.level == "ERROR"]
        assert any("slug" in e.field for e in hard)

    def test_array_too_short(self):
        data = _minimal_data()
        data["benefits"] = [{"title": "B1", "description": "O1"}]
        errors = validate(data)
        hard = [e for e in errors if e.level == "ERROR"]
        assert any("benefits" in e.field for e in hard)


# ═══════════════════════════════════════════
# render.py - Sanitizacija
# ═══════════════════════════════════════════

class TestSanitizeData:
    def test_html_escape(self):
        data = {"name": 'Test <script>alert("xss")</script>'}
        result = sanitize_data(data)
        assert "<script>" not in result["name"]
        assert "&lt;script&gt;" in result["name"]

    def test_cyrillic_transliteration(self):
        data = {"name": "Београд"}
        result = sanitize_data(data)
        assert result["name"] == "Beograd"

    def test_url_not_escaped(self):
        data = {"google_maps_url": "https://maps.google.com?q=test&z=16"}
        result = sanitize_data(data)
        assert "&amp;" not in result["google_maps_url"]
        assert "&z=16" in result["google_maps_url"]

    def test_nested_dict(self):
        data = {"reviews": [{"reviewer_name": "Петар", "text": "Одлично"}]}
        result = sanitize_data(data)
        assert result["reviews"][0]["reviewer_name"] == "Petar"

    def test_tema_sanitized(self):
        data = {"theme": {"primary": "#1a5276", "dark": "red;background:url(evil)"}}
        result = sanitize_data(data)
        assert result["theme"]["primary"] == "#1a5276"
        assert result["theme"]["dark"] == ""


class TestSanitizeTema:
    def test_safe_values(self):
        tema = {"primary": "#1a5276", "font_body": "'DM Sans',sans-serif"}
        result = sanitize_tema(tema)
        assert result["primary"] == "#1a5276"
        assert result["font_body"] == "'DM Sans',sans-serif"

    def test_unsafe_value_stripped(self):
        tema = {"primary": "red;background:url(javascript:alert(1))"}
        result = sanitize_tema(tema)
        assert result["primary"] == ""

    def test_non_string_preserved(self):
        tema = {"some_number": 42}
        result = sanitize_tema(tema)
        assert result["some_number"] == 42


class TestEscapeHtmlChars:
    def test_special_chars(self):
        assert escape_html_chars('a<b>c"d&e') == "a&lt;b&gt;c&quot;d&amp;e"

    def test_non_string(self):
        assert escape_html_chars(42) == 42

    def test_empty(self):
        assert escape_html_chars("") == ""


class TestJsonVal:
    def test_reverses_html_escape(self):
        result = _json_val("&lt;test&gt; &amp; &quot;quoted&quot;")
        assert result == '<test> & \\"quoted\\"'

    def test_prevents_script_injection(self):
        result = _json_val("</script>")
        assert "</script>" not in result
        assert "<\\/script>" in result

    def test_none_returns_empty(self):
        assert _json_val(None) == ""


class TestIsPlaceholder:
    def test_exact_match(self):
        assert is_placeholder("???") is True

    def test_in_text(self):
        assert is_placeholder("nesto ??? ovde") is True

    def test_normal_text(self):
        assert is_placeholder("Dr Marko") is False

    def test_non_string(self):
        assert is_placeholder(42) is False


class TestIsValidUrl:
    def test_valid_https(self):
        assert is_valid_url("https://example.com") is True

    def test_valid_http(self):
        assert is_valid_url("http://example.com") is True

    def test_relative_path(self):
        assert is_valid_url("photos/photo_01.jpg") is True

    def test_path_traversal_blocked(self):
        assert is_valid_url("../../etc/passwd") is False

    def test_absolute_path_blocked(self):
        assert is_valid_url("/etc/passwd") is False

    def test_empty_ok(self):
        assert is_valid_url("") is True


class TestAsciiSafeHtml:
    def test_ascii_passthrough(self):
        assert ascii_safe_html("hello world") == "hello world"

    def test_non_ascii_passthrough_utf8(self):
        result = ascii_safe_html("Šta")
        assert result == "Šta"

    def test_json_ld_uses_unicode_escapes(self):
        html = '<script type="application/ld+json">{"name":"Šta"}</script>'
        result = ascii_safe_html(html)
        assert "\\u" in result
        assert "&#" not in result.split("</script>")[0]


# ═══════════════════════════════════════════
# render.py - Enrichment
# ═══════════════════════════════════════════

class TestEnrichSchema:
    def test_adds_tema(self):
        data = _minimal_data()
        result = enrich_schema(data)
        assert "theme" in result
        assert "primary" in result["theme"]

    def test_adds_google_maps_embed(self):
        data = _minimal_data()
        result = enrich_schema(data)
        assert "google_maps_embed_url" in result
        assert "maps.google.com" in result["google_maps_embed_url"]

    def test_no_auto_core_values(self):
        data = _minimal_data()
        result = enrich_schema(data)
        assert not result.get("core_values"), "core_values should not be auto-generated"

    def test_does_not_overwrite_existing_tema(self):
        data = _minimal_data()
        data["theme"] = {"primary": "#ff0000"}
        result = enrich_schema(data)
        assert result["theme"]["primary"] == "#ff0000"

    def test_splits_long_story_into_paragraphs(self):
        data = _minimal_data()
        data["about_story"] = "Prva rečenica. Druga rečenica. Treća rečenica. Četvrta rečenica. Peta rečenica. Šesta rečenica. Sedma rečenica. Osma rečenica. Deveta rečenica. Deseta rečenica." + " Još jedna." * 20
        result = enrich_schema(data)
        assert "about_paragraphs" in result
        assert len(result["about_paragraphs"]) >= 2
