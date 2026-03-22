"""Tests for pipeline.py outreach tracker."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline import (
    FOLLOWUP_SCHEDULE,
    VALID_CHANNELS,
    VALID_HOOKS,
    VALID_OUTCOMES,
    _empty_status,
    _pct,
    _short_key,
    _trunc,
    cmd_contact,
    cmd_convert,
    cmd_due,
    cmd_followup,
    cmd_list,
    cmd_next,
    cmd_respond,
    cmd_stats,
    find_leads_dir,
    folder_key,
    get_lead_status,
    load_status,
    read_schema,
    resolve_lead,
    save_status,
    scan_all_leads,
    suggest_similar,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_lead_folder(
    base: Path,
    category: str,
    number: int,
    name: str,
    score: int,
    mobilni: str = "641234567",
    email: str = "",
    grad: str = "Beograd",
    rating: float = 4.5,
    reviews: int = 50,
    slug: str = "",
    keywords: list[str] | None = None,
) -> Path:
    """Create a mock lead folder with schema_draft.json."""
    folder_name = f"{number:03d}_{name}_{score}pts"
    folder = base / category / folder_name
    folder.mkdir(parents=True, exist_ok=True)

    schema = {
        "slug": slug or f"test-{name.lower()}-abcd",
        "name": name.replace("_", " "),
        "owner_short": name.split("_")[0],
        "city": grad,
        "phone": mobilni,
        "phone_display": "",
        "email": email,
        "rating": rating,
        "review_count": reviews,
        "_score": score,
        "_category": category,
        "_review_keywords": keywords or ["brzo", "profesionalno"],
    }
    with open(folder / "schema_draft.json", "w", encoding="utf-8") as f:
        json.dump(schema, f, ensure_ascii=False)

    return folder


@pytest.fixture
def leads_dir(tmp_path: Path) -> Path:
    """Create a mock auto-leads directory with sample leads."""
    base = tmp_path / "auto-leads"
    (base / "HOT").mkdir(parents=True)
    (base / "WARM").mkdir(parents=True)
    (base / "COOL").mkdir(parents=True)

    _make_lead_folder(base, "HOT", 1, "Vulkanizer_Sasa", 81, grad="Subotica", reviews=104)
    _make_lead_folder(base, "HOT", 2, "Elektricar_Milan", 79, grad="Novi Sad", reviews=87)
    _make_lead_folder(base, "HOT", 3, "Auto_Servis_MB", 79, grad="Beograd", reviews=60)
    _make_lead_folder(base, "WARM", 1, "Automehaničar", 59, grad="Novi Sad", reviews=20)
    _make_lead_folder(base, "WARM", 2, "Farbara_Riva", 55, email="riva@test.com", grad="Kragujevac")

    return base


@pytest.fixture
def status_with_contacts(leads_dir: Path) -> dict:
    """Create a status with some contacted leads."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

    status = _empty_status()
    status["leads"] = {
        "HOT/001_Vulkanizer_Sasa_81pts": {
            "contacted_date": yesterday,
            "channel": "whatsapp",
            "hook": "quote",
            "followups": [],
            "response_date": None,
            "outcome": None,
            "deal_value": None,
            "notes": "",
        },
        "HOT/002_Elektricar_Milan_79pts": {
            "contacted_date": today,
            "channel": "email",
            "hook": "competitor",
            "followups": [],
            "response_date": None,
            "outcome": None,
            "deal_value": None,
            "notes": "",
        },
    }
    save_status(leads_dir, status)
    return status


# ---------------------------------------------------------------------------
# TestLeadResolution
# ---------------------------------------------------------------------------


class TestLeadResolution:
    def test_resolve_category_number(self, leads_dir: Path) -> None:
        result = resolve_lead("HOT/001", leads_dir)
        assert result is not None
        assert result.name.startswith("001_")
        assert "Vulkanizer" in result.name

    def test_resolve_number_only(self, leads_dir: Path) -> None:
        result = resolve_lead("003", leads_dir)
        assert result is not None
        assert "Auto_Servis_MB" in result.name

    def test_resolve_full_name(self, leads_dir: Path) -> None:
        result = resolve_lead("WARM/001_Automehaničar_59pts", leads_dir)
        assert result is not None

    def test_resolve_not_found(self, leads_dir: Path) -> None:
        result = resolve_lead("HOT/999", leads_dir)
        assert result is None

    def test_resolve_warm_number(self, leads_dir: Path) -> None:
        result = resolve_lead("WARM/002", leads_dir)
        assert result is not None
        assert "Farbara" in result.name

    def test_suggest_similar(self, leads_dir: Path) -> None:
        suggestions = suggest_similar("vulk", leads_dir)
        assert len(suggestions) >= 1
        assert any("Vulkanizer" in s for s in suggestions)


# ---------------------------------------------------------------------------
# TestStatusStorage
# ---------------------------------------------------------------------------


class TestStatusStorage:
    def test_load_empty(self, leads_dir: Path) -> None:
        status = load_status(leads_dir)
        assert "leads" in status
        assert "meta" in status
        assert len(status["leads"]) == 0

    def test_save_and_load(self, leads_dir: Path) -> None:
        status = _empty_status()
        status["leads"]["HOT/001_Test_80pts"] = {
            "contacted_date": "2026-03-17",
            "channel": "whatsapp",
            "hook": "quote",
            "followups": [],
            "response_date": None,
            "outcome": None,
            "deal_value": None,
            "notes": "",
        }
        save_status(leads_dir, status)
        loaded = load_status(leads_dir)
        assert "HOT/001_Test_80pts" in loaded["leads"]

    def test_atomic_write_no_partial(self, leads_dir: Path) -> None:
        status = _empty_status()
        save_status(leads_dir, status)
        # .tmp should not exist after successful write
        tmp = leads_dir / "_pipeline_status.json.tmp"
        assert not tmp.exists()

    def test_corrupt_json_recovery(self, leads_dir: Path) -> None:
        path = leads_dir / "_pipeline_status.json"
        path.write_text("{invalid json", encoding="utf-8")
        status = load_status(leads_dir)
        assert "leads" in status
        assert len(status["leads"]) == 0
        # Backup should exist
        assert (leads_dir / "_pipeline_status.json.bak").exists()


# ---------------------------------------------------------------------------
# TestSchemaReader
# ---------------------------------------------------------------------------


class TestSchemaReader:
    def test_read_schema_extracts_fields(self, leads_dir: Path) -> None:
        schema_path = leads_dir / "HOT" / "001_Vulkanizer_Sasa_81pts" / "schema_draft.json"
        result = read_schema(schema_path)
        assert result["_score"] == 81
        assert result["city"] == "Subotica"
        assert result["rating"] == 4.5
        assert result["review_count"] == 104

    def test_read_schema_missing_file(self, tmp_path: Path) -> None:
        result = read_schema(tmp_path / "nonexistent.json")
        assert result["_score"] == 0


# ---------------------------------------------------------------------------
# TestGetLeadStatus
# ---------------------------------------------------------------------------


class TestGetLeadStatus:
    def test_pending(self) -> None:
        status = _empty_status()
        assert get_lead_status(status, "HOT/001_Test_80pts") == "pending"

    def test_contacted(self) -> None:
        status = _empty_status()
        status["leads"]["HOT/001"] = {
            "contacted_date": "2026-03-17",
            "outcome": None,
            "deal_value": None,
        }
        assert get_lead_status(status, "HOT/001") == "contacted"

    def test_responded(self) -> None:
        status = _empty_status()
        status["leads"]["HOT/001"] = {
            "contacted_date": "2026-03-17",
            "outcome": "positive",
            "deal_value": None,
        }
        assert get_lead_status(status, "HOT/001") == "responded"

    def test_converted(self) -> None:
        status = _empty_status()
        status["leads"]["HOT/001"] = {
            "contacted_date": "2026-03-17",
            "outcome": "positive",
            "deal_value": 300,
        }
        assert get_lead_status(status, "HOT/001") == "converted"

    def test_ghosted(self) -> None:
        status = _empty_status()
        status["leads"]["HOT/001"] = {
            "contacted_date": "2026-03-17",
            "outcome": "ghosted",
            "deal_value": None,
        }
        assert get_lead_status(status, "HOT/001") == "ghosted"


# ---------------------------------------------------------------------------
# TestCmdContact
# ---------------------------------------------------------------------------


class TestCmdContact:
    def test_contact_new_lead(self, leads_dir: Path, capsys: pytest.CaptureFixture) -> None:
        cmd_contact("HOT/001", "whatsapp", "quote", leads_dir)
        captured = capsys.readouterr()
        assert "OK" in captured.out
        status = load_status(leads_dir)
        key = [k for k in status["leads"] if k.startswith("HOT/001_")][0]
        assert status["leads"][key]["channel"] == "whatsapp"
        assert status["leads"][key]["hook"] == "quote"

    def test_contact_already_contacted(self, leads_dir: Path, capsys: pytest.CaptureFixture) -> None:
        cmd_contact("HOT/001", "whatsapp", "quote", leads_dir)
        cmd_contact("HOT/001", "email", "competitor", leads_dir)
        captured = capsys.readouterr()
        assert "WARNING" in captured.out
        assert "already contacted" in captured.out

    def test_contact_force_override(self, leads_dir: Path, capsys: pytest.CaptureFixture) -> None:
        cmd_contact("HOT/001", "whatsapp", "quote", leads_dir)
        cmd_contact("HOT/001", "email", "competitor", leads_dir, force=True)
        status = load_status(leads_dir)
        key = [k for k in status["leads"] if k.startswith("HOT/001_")][0]
        assert status["leads"][key]["channel"] == "email"

    def test_contact_email_no_email_warning(self, leads_dir: Path, capsys: pytest.CaptureFixture) -> None:
        cmd_contact("HOT/001", "email", "quote", leads_dir)
        captured = capsys.readouterr()
        assert "no email" in captured.out

    def test_contact_not_found(self, leads_dir: Path, capsys: pytest.CaptureFixture) -> None:
        cmd_contact("HOT/999", "whatsapp", "quote", leads_dir)
        captured = capsys.readouterr()
        assert "ERROR" in captured.out


# ---------------------------------------------------------------------------
# TestCmdFollowup
# ---------------------------------------------------------------------------


class TestCmdFollowup:
    def test_followup_valid(self, leads_dir: Path, capsys: pytest.CaptureFixture) -> None:
        cmd_contact("HOT/001", "whatsapp", "quote", leads_dir)
        cmd_followup("HOT/001", "followup_1", "whatsapp", leads_dir)
        captured = capsys.readouterr()
        assert "OK" in captured.out
        status = load_status(leads_dir)
        key = [k for k in status["leads"] if k.startswith("HOT/001_")][0]
        assert len(status["leads"][key]["followups"]) == 1

    def test_followup_uncontacted(self, leads_dir: Path, capsys: pytest.CaptureFixture) -> None:
        cmd_followup("HOT/001", "followup_1", "whatsapp", leads_dir)
        captured = capsys.readouterr()
        assert "ERROR" in captured.out
        assert "not contacted" in captured.out

    def test_followup_duplicate(self, leads_dir: Path, capsys: pytest.CaptureFixture) -> None:
        cmd_contact("HOT/001", "whatsapp", "quote", leads_dir)
        cmd_followup("HOT/001", "followup_1", "whatsapp", leads_dir)
        cmd_followup("HOT/001", "followup_1", "whatsapp", leads_dir)
        captured = capsys.readouterr()
        assert "WARNING" in captured.out
        assert "already sent" in captured.out


# ---------------------------------------------------------------------------
# TestCmdRespond
# ---------------------------------------------------------------------------


class TestCmdRespond:
    def test_respond_contacted(self, leads_dir: Path, capsys: pytest.CaptureFixture) -> None:
        cmd_contact("HOT/001", "whatsapp", "quote", leads_dir)
        cmd_respond("HOT/001", "positive", leads_dir)
        captured = capsys.readouterr()
        assert "OK" in captured.out
        status = load_status(leads_dir)
        key = [k for k in status["leads"] if k.startswith("HOT/001_")][0]
        assert status["leads"][key]["outcome"] == "positive"

    def test_respond_uncontacted(self, leads_dir: Path, capsys: pytest.CaptureFixture) -> None:
        cmd_respond("HOT/001", "positive", leads_dir)
        captured = capsys.readouterr()
        assert "ERROR" in captured.out


# ---------------------------------------------------------------------------
# TestCmdConvert
# ---------------------------------------------------------------------------


class TestCmdConvert:
    def test_convert_with_response(self, leads_dir: Path, capsys: pytest.CaptureFixture) -> None:
        cmd_contact("HOT/001", "whatsapp", "quote", leads_dir)
        cmd_respond("HOT/001", "positive", leads_dir)
        cmd_convert("HOT/001", 300, leads_dir)
        captured = capsys.readouterr()
        assert "CONVERTED" in captured.out
        status = load_status(leads_dir)
        key = [k for k in status["leads"] if k.startswith("HOT/001_")][0]
        assert status["leads"][key]["deal_value"] == 300

    def test_convert_uncontacted(self, leads_dir: Path, capsys: pytest.CaptureFixture) -> None:
        cmd_convert("HOT/001", 300, leads_dir)
        captured = capsys.readouterr()
        assert "ERROR" in captured.out


# ---------------------------------------------------------------------------
# TestCmdDue
# ---------------------------------------------------------------------------


class TestCmdDue:
    def test_no_followups_due(self, leads_dir: Path, capsys: pytest.CaptureFixture) -> None:
        cmd_due(leads_dir)
        captured = capsys.readouterr()
        assert "No follow-ups due" in captured.out

    def test_day2_due(self, leads_dir: Path, capsys: pytest.CaptureFixture) -> None:
        two_days_ago = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
        status = _empty_status()
        status["leads"]["HOT/001_Vulkanizer_Sasa_81pts"] = {
            "contacted_date": two_days_ago,
            "channel": "whatsapp",
            "hook": "quote",
            "followups": [],
            "response_date": None,
            "outcome": None,
            "deal_value": None,
            "notes": "",
        }
        save_status(leads_dir, status)
        cmd_due(leads_dir)
        captured = capsys.readouterr()
        assert "followup_1" in captured.out

    def test_already_sent_excluded(self, leads_dir: Path, capsys: pytest.CaptureFixture) -> None:
        two_days_ago = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        status = _empty_status()
        status["leads"]["HOT/001_Vulkanizer_Sasa_81pts"] = {
            "contacted_date": two_days_ago,
            "channel": "whatsapp",
            "hook": "quote",
            "followups": [{"date": today, "type": "followup_1", "channel": "whatsapp"}],
            "response_date": None,
            "outcome": None,
            "deal_value": None,
            "notes": "",
        }
        save_status(leads_dir, status)
        cmd_due(leads_dir)
        captured = capsys.readouterr()
        # followup_1 already sent, no more follow-ups in schedule
        assert "followup_1" not in captured.out

    def test_overdue_shown(self, leads_dir: Path, capsys: pytest.CaptureFixture) -> None:
        five_days_ago = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d")
        status = _empty_status()
        status["leads"]["HOT/001_Vulkanizer_Sasa_81pts"] = {
            "contacted_date": five_days_ago,
            "channel": "whatsapp",
            "hook": "quote",
            "followups": [],
            "response_date": None,
            "outcome": None,
            "deal_value": None,
            "notes": "",
        }
        save_status(leads_dir, status)
        cmd_due(leads_dir)
        captured = capsys.readouterr()
        assert "OVERDUE" in captured.out

    def test_outcome_skipped(self, leads_dir: Path, capsys: pytest.CaptureFixture) -> None:
        """Leads with outcome should not show in due."""
        two_days_ago = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
        status = _empty_status()
        status["leads"]["HOT/001_Vulkanizer_Sasa_81pts"] = {
            "contacted_date": two_days_ago,
            "channel": "whatsapp",
            "hook": "quote",
            "followups": [],
            "response_date": two_days_ago,
            "outcome": "positive",
            "deal_value": None,
            "notes": "",
        }
        save_status(leads_dir, status)
        cmd_due(leads_dir)
        captured = capsys.readouterr()
        assert "followup_1" not in captured.out


# ---------------------------------------------------------------------------
# TestCmdStats
# ---------------------------------------------------------------------------


class TestCmdStats:
    def test_empty_stats(self, leads_dir: Path, capsys: pytest.CaptureFixture) -> None:
        cmd_stats(leads_dir)
        captured = capsys.readouterr()
        assert "Contacted:" in captured.out
        assert "0" in captured.out

    def test_funnel_calculation(self, leads_dir: Path, capsys: pytest.CaptureFixture) -> None:
        cmd_contact("HOT/001", "whatsapp", "quote", leads_dir)
        cmd_contact("HOT/002", "email", "competitor", leads_dir)
        cmd_respond("HOT/001", "positive", leads_dir)
        cmd_stats(leads_dir)
        captured = capsys.readouterr()
        assert "Contacted:       2" in captured.out
        assert "Responded:       1" in captured.out

    def test_hook_breakdown(self, leads_dir: Path, capsys: pytest.CaptureFixture) -> None:
        cmd_contact("HOT/001", "whatsapp", "quote", leads_dir)
        cmd_contact("HOT/002", "whatsapp", "competitor", leads_dir)
        cmd_respond("HOT/001", "positive", leads_dir)
        cmd_stats(leads_dir)
        captured = capsys.readouterr()
        assert "quote" in captured.out
        assert "competitor" in captured.out


# ---------------------------------------------------------------------------
# TestCmdNext
# ---------------------------------------------------------------------------


class TestCmdNext:
    def test_sorted_by_score(self, leads_dir: Path, capsys: pytest.CaptureFixture) -> None:
        cmd_next(3, leads_dir)
        captured = capsys.readouterr()
        lines = [l for l in captured.out.split("\n") if l.strip() and l.strip()[0].isdigit()]
        # First lead should have highest score (81)
        assert "81" in lines[0]

    def test_excludes_contacted(self, leads_dir: Path, capsys: pytest.CaptureFixture) -> None:
        cmd_contact("HOT/001", "whatsapp", "quote", leads_dir)
        capsys.readouterr()  # Clear contact output
        cmd_next(10, leads_dir)
        captured = capsys.readouterr()
        # HOT/001 (score 81) should not appear in next list
        lines = captured.out.split("\n")
        data_lines = [l for l in lines if l.strip() and l.strip()[0].isdigit()]
        for line in data_lines:
            assert "81" not in line.split()[1:2]  # Score column


# ---------------------------------------------------------------------------
# TestCmdList
# ---------------------------------------------------------------------------


class TestCmdList:
    def test_filter_contacted(self, leads_dir: Path, capsys: pytest.CaptureFixture) -> None:
        cmd_contact("HOT/001", "whatsapp", "quote", leads_dir)
        cmd_list("contacted", leads_dir)
        captured = capsys.readouterr()
        assert "contacted" in captured.out
        assert "Total: 1" in captured.out

    def test_no_filter_hides_pending(self, leads_dir: Path, capsys: pytest.CaptureFixture) -> None:
        cmd_list(None, leads_dir)
        captured = capsys.readouterr()
        assert "No contacted" in captured.out


# ---------------------------------------------------------------------------
# TestHelpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_pct(self) -> None:
        assert _pct(1, 4) == "25.0%"
        assert _pct(0, 0) == "0.0%"

    def test_trunc(self) -> None:
        assert _trunc("short", 10) == "short"
        assert _trunc("very long string here", 10) == "very lon.."

    def test_short_key(self) -> None:
        result = _short_key("HOT/001_VULKANIZER_SAŠA_81pts")
        assert result == "HOT/001 VULKANIZER SAŠA"
        assert "pts" not in result
