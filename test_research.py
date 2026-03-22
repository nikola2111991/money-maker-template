"""Smoke tests for research.py validation and merge logic.

Pokreni: pytest test_research.py -v
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from research import (
    _count_filled_fields,
    _is_bad_owner,
    _is_bad_url,
    _validate_claude_fields,
    load_checkpoint,
    move_lead_folder,
    research_lead,
    save_checkpoint,
)


# ============================================================
# _is_bad_owner
# ============================================================


class TestIsBadOwner:
    def test_good_owner(self):
        assert _is_bad_owner("John Smith", "ABC Tiling") is False

    def test_empty(self):
        assert _is_bad_owner("", "ABC Tiling") is True

    def test_popuni(self):
        assert _is_bad_owner("_POPUNI", "ABC Tiling") is True

    def test_business_name_word(self):
        assert _is_bad_owner("Tiling", "ABC Tiling Services") is True

    def test_garbage_word(self):
        assert _is_bad_owner("services", "Quick Fix") is True
        assert _is_bad_owner("Renovations", "Home Renovations") is True
        assert _is_bad_owner("pty", "Foo Pty Ltd") is True

    def test_too_short(self):
        assert _is_bad_owner("AB", "Some Business") is True
        assert _is_bad_owner("Jo", "Some Business") is True

    def test_three_chars_valid(self):
        assert _is_bad_owner("Ana", "Some Business") is False


# ============================================================
# _is_bad_url
# ============================================================


class TestIsBadUrl:
    def test_good_facebook(self):
        assert _is_bad_url("https://www.facebook.com/somepage") is False

    def test_empty(self):
        assert _is_bad_url("") is True

    def test_domain_only(self):
        assert _is_bad_url("https://www.facebook.com/") is True
        assert _is_bad_url("https://www.instagram.com") is True

    def test_profile_php_no_id(self):
        assert _is_bad_url("https://www.facebook.com/profile.php") is True

    def test_profile_php_with_id(self):
        assert _is_bad_url("https://www.facebook.com/profile.php?id=123") is False


# ============================================================
# _count_filled_fields
# ============================================================


class TestCountFilledFields:
    def test_empty(self):
        assert _count_filled_fields({}) == 0

    def test_all_filled(self):
        data = {
            "owner": "John",
            "years_in_business": 10,
            "facebook": "https://fb.com/x",
            "instagram": "https://ig.com/x",
            "email": "a@b.com",
            "services": ["plumbing"],
            "specialties": ["bathroom"],
        }
        assert _count_filled_fields(data) == 7

    def test_popuni_not_counted(self):
        assert _count_filled_fields({"owner": "_POPUNI_VLASNIK"}) == 0

    def test_empty_list_not_counted(self):
        assert _count_filled_fields({"services": []}) == 0


# ============================================================
# _validate_claude_fields
# ============================================================


class TestValidateClaudeFields:
    def test_good_data(self):
        data = {
            "owner": "John Smith",
            "years_in_business": 15,
            "facebook": "https://www.facebook.com/smithplumbing",
            "instagram": "https://www.instagram.com/smithplumbing",
            "email": "john@smith.com",
            "services": ["bathroom", "kitchen"],
            "licensed": True,
        }
        result = _validate_claude_fields(data, "Smith Plumbing")
        assert result["owner"] == "John Smith"
        assert result["years_in_business"] == 15
        assert result["facebook"] == "https://www.facebook.com/smithplumbing"
        assert result["instagram"] == "https://www.instagram.com/smithplumbing"
        assert result["email"] == "john@smith.com"
        assert result["extra_services"] == ["bathroom", "kitchen"]
        assert result["licensed"] is True

    def test_owner_is_business_name(self):
        result = _validate_claude_fields({"owner": "Plumbing"}, "ABC Plumbing Services")
        assert "owner" not in result

    def test_bad_facebook_post_url(self):
        result = _validate_claude_fields(
            {"facebook": "https://www.facebook.com/page/posts/123"}, "Biz"
        )
        assert "facebook" not in result

    def test_bad_instagram_reel(self):
        result = _validate_claude_fields(
            {"instagram": "https://www.instagram.com/reel/ABC123"}, "Biz"
        )
        assert "instagram" not in result

    def test_noreply_email_rejected(self):
        result = _validate_claude_fields({"email": "noreply@biz.com"}, "Biz")
        assert "email" not in result

    def test_years_out_of_range(self):
        result = _validate_claude_fields({"years_in_business": 100}, "Biz")
        assert "years_in_business" not in result
        result2 = _validate_claude_fields({"years_in_business": 0}, "Biz")
        assert "years_in_business" not in result2

    def test_founded_year_calculates_years(self):
        result = _validate_claude_fields({"founded_year": 2010}, "Biz")
        assert result["founded_year"] == 2010
        assert result["years_in_business"] > 0

    def test_empty_data(self):
        assert _validate_claude_fields({}, "Biz") == {}

    def test_services_max_5(self):
        data = {"services": ["a", "b", "c", "d", "e", "f", "g"]}
        result = _validate_claude_fields(data, "Biz")
        assert len(result["extra_services"]) == 5

    def test_facebook_domain_only_rejected(self):
        result = _validate_claude_fields(
            {"facebook": "https://www.facebook.com/"}, "Biz"
        )
        assert "facebook" not in result

    def test_instagram_good_profile(self):
        result = _validate_claude_fields(
            {"instagram": "https://www.instagram.com/handle"}, "Biz"
        )
        assert result["instagram"] == "https://www.instagram.com/handle"


# ============================================================
# research_lead (with mocked claude findings)
# ============================================================


class TestResearchLead:
    def _make_lead(self, tmp_path, schema_data, data_json=None):
        folder = str(tmp_path / "HOT" / "001_Test_Lead_50pts")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "schema_draft.json"), "w") as f:
            json.dump(schema_data, f)
        if data_json:
            with open(os.path.join(folder, "data.json"), "w") as f:
                json.dump(data_json, f)
        return folder

    def test_applies_claude_findings(self, tmp_path):
        folder = self._make_lead(
            tmp_path,
            {"name": "Test Biz", "city": "Belgrade", "owner": "", "facebook": ""},
            {"name": "Test Biz", "score": 40},
        )
        result = research_lead(
            folder=folder,
            category="HOT",
            playbook={},
            claude_findings={
                "owner": "Marko Petrovic",
                "facebook": "https://www.facebook.com/testbiz",
            },
        )
        assert "owner" in result.fields_added
        assert "facebook" in result.fields_added
        assert result.sources_used == ["claude_cli"]

        with open(os.path.join(folder, "schema_draft.json")) as f:
            updated = json.load(f)
        assert updated["owner"] == "Marko Petrovic"
        assert updated["owner_short"] == "Marko"
        assert updated["facebook"] == "https://www.facebook.com/testbiz"
        assert updated["_researched"] is True

    def test_does_not_overwrite_good_data(self, tmp_path):
        folder = self._make_lead(
            tmp_path,
            {
                "name": "Test Biz",
                "owner": "Existing Owner",
                "facebook": "https://www.facebook.com/existing",
            },
            {"name": "Test Biz"},
        )
        result = research_lead(
            folder=folder,
            category="HOT",
            playbook={},
            claude_findings={
                "owner": "New Owner",
                "facebook": "https://www.facebook.com/new",
            },
        )
        assert "owner" not in result.fields_added
        assert "facebook" not in result.fields_added

        with open(os.path.join(folder, "schema_draft.json")) as f:
            updated = json.load(f)
        assert updated["owner"] == "Existing Owner"

    def test_overwrites_bad_owner(self, tmp_path):
        folder = self._make_lead(
            tmp_path,
            {"name": "ABC Services", "owner": "Services"},
            {"name": "ABC Services"},
        )
        result = research_lead(
            folder=folder,
            category="HOT",
            playbook={},
            claude_findings={"owner": "Milan Jovic"},
        )
        assert "owner" in result.fields_added

    def test_no_findings_no_crash(self, tmp_path):
        folder = self._make_lead(
            tmp_path,
            {"name": "Empty Biz"},
            {"name": "Empty Biz"},
        )
        result = research_lead(
            folder=folder, category="WARM", playbook={}, claude_findings=None
        )
        assert result.fields_added == []
        assert result.lead_key == "WARM/001_Test_Lead_50pts"

    def test_rescores_after_research(self, tmp_path):
        folder = self._make_lead(
            tmp_path,
            {
                "name": "Good Biz",
                "rating": 4.7,
                "review_count": 50,
                "website": "",
                "mobile": "0641234567",
            },
            {"name": "Good Biz", "rating": 4.7, "review_count": 50, "score": 0},
        )
        result = research_lead(
            folder=folder, category="WARM", playbook={}, claude_findings={}
        )
        assert result.new_score > 0

        with open(os.path.join(folder, "schema_draft.json")) as f:
            updated = json.load(f)
        assert "_score" in updated
        assert "_category" in updated
        assert "_score_breakdown" in updated


# ============================================================
# Checkpoint
# ============================================================


class TestCheckpoint:
    def test_save_and_load(self, tmp_path):
        leads_dir = str(tmp_path)
        keys = {"HOT/001", "HOT/002", "WARM/003"}
        save_checkpoint(leads_dir, keys)
        loaded = load_checkpoint(leads_dir)
        assert loaded == keys

    def test_empty_checkpoint(self, tmp_path):
        loaded = load_checkpoint(str(tmp_path))
        assert loaded == set()


# ============================================================
# move_lead_folder
# ============================================================


class TestMoveLeadFolder:
    def test_moves_folder(self, tmp_path):
        leads_dir = str(tmp_path)
        warm_dir = os.path.join(leads_dir, "WARM")
        os.makedirs(warm_dir)
        folder = os.path.join(warm_dir, "001_Test_50pts")
        os.makedirs(folder)
        with open(os.path.join(folder, "schema_draft.json"), "w") as f:
            json.dump({"name": "Test"}, f)

        new_folder = move_lead_folder(folder, "WARM", "HOT", leads_dir)
        assert os.path.exists(new_folder)
        assert "HOT" in new_folder
        assert not os.path.exists(folder)
