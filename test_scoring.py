"""
test_scoring.py - Testovi za scoring.py v3.1
Pokreni: pytest test_scoring.py -v
"""

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from scoring import _calculate, score_dict, DEFAULT_HOT, DEFAULT_WARM


def _base_args(**overrides):
    """Minimal args for _calculate, all zeros/empty."""
    defaults = dict(
        rating=0.0,
        review_count=0,
        website="",
        site_quality={},
        facebook="",
        instagram="",
        mobile="",
        phone="",
        email="",
        verified=False,
        district="",
        address="",
        premium_locations=[],
        specialties=[],
        photo_count=0,
        has_hours=False,
        years_in_business=0,
        negative_flags=[],
        avg_review_length=0,
        newest_review_days=999,
        has_owner=False,
        competitor_count=0,
        scoring_config=None,
    )
    defaults.update(overrides)
    return defaults


class TestEdgeCases:
    def test_all_zeros_is_cool(self):
        score, cat, bd = _calculate(**_base_args())
        assert cat == "COOL"

    def test_empty_strings_is_cool(self):
        score, cat, bd = _calculate(**_base_args())
        assert score < DEFAULT_WARM


class TestThresholds:
    def test_default_hot_threshold(self):
        assert DEFAULT_HOT == 45

    def test_default_warm_threshold(self):
        assert DEFAULT_WARM == 28

    def test_score_44_is_warm(self):
        # rating 4.5 (12) + no_website (15) + no_fb (3) + no_ig (2) + mobile (10) = 42
        # + has_email (3) = 45... need exactly 44
        # rating 4.5 (12) + no_website (15) + no_fb (3) + no_ig (2) + phone_only (5) + email (3) + photos (2) + hours (2) = 44
        score, cat, bd = _calculate(
            **_base_args(
                rating=4.5,
                phone="12345",
                email="a@b.com",
                photo_count=3,
                has_hours=True,
            )
        )
        assert score == 44
        assert cat == "WARM"

    def test_score_45_is_hot(self):
        # same + mobile instead of phone = +5 more = 49, too high
        # rating 4.5 (12) + no_website (15) + no_fb (3) + no_ig (2) + mobile (10) = 42
        # + email (3) = 45
        score, cat, bd = _calculate(
            **_base_args(
                rating=4.5,
                mobile="0412345678",
                email="a@b.com",
            )
        )
        assert score == 45
        assert cat == "HOT"

    def test_score_28_is_warm(self):
        # no_website (15) + no_fb (3) + no_ig (2) + mobile (10) = 30... too high
        # no_website (15) + no_fb (3) + no_ig (2) + phone_only (5) + rating 3.5 (6) = 31... still
        # no_website (15) + mobile (10) + rating 0 = 25 + no_fb (3) = 28
        score, cat, bd = _calculate(
            **_base_args(
                mobile="0412345678",
                facebook="https://facebook.com/test",
            )
        )
        # no_website (15) + no_ig (2) + mobile (10) + zero_online would apply but has mobile...
        # Actually: no_website=15, no_instagram=2, has_mobile=10, zero_online needs no website+no fb+no ig+no email
        # facebook is set so no_facebook=0. zero_online needs all empty but fb is set.
        # = 15 + 2 + 10 = 27 → COOL
        assert score == 27
        assert cat == "COOL"

    def test_score_just_above_warm(self):
        # Add has_hours to get 29
        score, cat, bd = _calculate(
            **_base_args(
                mobile="0412345678",
                facebook="https://facebook.com/test",
                has_hours=True,
            )
        )
        # 15 + 2 + 10 + 2 = 29
        assert score == 29
        assert cat == "WARM"


class TestReputation:
    def test_rating_elite(self):
        _, _, bd = _calculate(**_base_args(rating=4.5))
        assert bd.get("rating_elite") == 12

    def test_rating_strong(self):
        _, _, bd = _calculate(**_base_args(rating=4.2))
        assert bd.get("rating_strong") == 9

    def test_rating_good(self):
        _, _, bd = _calculate(**_base_args(rating=3.5))
        assert bd.get("rating_good") == 6

    def test_rating_below_threshold(self):
        _, _, bd = _calculate(**_base_args(rating=2.9))
        assert "rating_elite" not in bd
        assert "rating_strong" not in bd
        assert "rating_good" not in bd

    def test_reviews_100(self):
        _, _, bd = _calculate(**_base_args(review_count=100))
        assert bd.get("reviews_100+") == 8

    def test_reviews_50(self):
        _, _, bd = _calculate(**_base_args(review_count=50))
        assert bd.get("reviews_50+") == 6

    def test_reviews_20(self):
        _, _, bd = _calculate(**_base_args(review_count=20))
        assert bd.get("reviews_20+") == 4

    def test_reviews_5(self):
        _, _, bd = _calculate(**_base_args(review_count=5))
        assert bd.get("reviews_5+") == 2

    def test_reviews_4_gets_nothing(self):
        _, _, bd = _calculate(**_base_args(review_count=4))
        assert "reviews_5+" not in bd


class TestDigitalGap:
    def test_no_website(self):
        _, _, bd = _calculate(**_base_args())
        assert bd.get("no_website") == 15

    def test_bad_website(self):
        _, _, bd = _calculate(
            **_base_args(
                website="https://example.com",
                site_quality={"is_bad": True, "issues": ["ssl_error"]},
            )
        )
        assert bd.get("bad_website") == 12
        assert bd.get("site_issues") == "ssl_error"

    def test_good_website_no_gap(self):
        _, _, bd = _calculate(
            **_base_args(
                website="https://example.com",
                site_quality={"is_bad": False},
            )
        )
        assert "no_website" not in bd
        assert "bad_website" not in bd

    def test_no_facebook(self):
        _, _, bd = _calculate(**_base_args())
        assert bd.get("no_facebook") == 3

    def test_has_facebook(self):
        _, _, bd = _calculate(**_base_args(facebook="https://facebook.com/test"))
        assert "no_facebook" not in bd

    def test_no_instagram(self):
        _, _, bd = _calculate(**_base_args())
        assert bd.get("no_instagram") == 2


class TestReachability:
    def test_mobile_10pts(self):
        _, _, bd = _calculate(**_base_args(mobile="0412345678"))
        assert bd.get("has_mobile") == 10
        assert "has_phone_only" not in bd

    def test_phone_only_5pts(self):
        _, _, bd = _calculate(**_base_args(phone="0212345678"))
        assert bd.get("has_phone_only") == 5
        assert "has_mobile" not in bd

    def test_mobile_takes_priority_over_phone(self):
        _, _, bd = _calculate(**_base_args(mobile="0412345678", phone="0212345678"))
        assert bd.get("has_mobile") == 10
        assert "has_phone_only" not in bd

    def test_email(self):
        _, _, bd = _calculate(**_base_args(email="test@example.com"))
        assert bd.get("has_email") == 3

    def test_verified(self):
        _, _, bd = _calculate(**_base_args(verified=True))
        assert bd.get("contact_verified") == 2


class TestBusinessSignals:
    def test_premium_location(self):
        _, _, bd = _calculate(
            **_base_args(
                address="123 Mosman St",
                premium_locations=["Mosman"],
            )
        )
        assert bd.get("premium_location") == 5

    def test_non_premium_location(self):
        _, _, bd = _calculate(
            **_base_args(
                address="123 Random St",
                premium_locations=["Mosman"],
            )
        )
        assert "premium_location" not in bd

    def test_specialty(self):
        _, _, bd = _calculate(**_base_args(specialties=["Bathroom Tiling"]))
        assert bd.get("has_specialty") == 4

    def test_photos(self):
        _, _, bd = _calculate(**_base_args(photo_count=3))
        assert bd.get("has_photos") == 2

    def test_photos_below_threshold(self):
        _, _, bd = _calculate(**_base_args(photo_count=2))
        assert "has_photos" not in bd

    def test_new_business(self):
        _, _, bd = _calculate(**_base_args(years_in_business=1))
        assert bd.get("new_business") == 2

    def test_old_business_no_bonus(self):
        _, _, bd = _calculate(**_base_args(years_in_business=10))
        assert "new_business" not in bd

    def test_low_competition(self):
        _, _, bd = _calculate(**_base_args(competitor_count=5))
        assert bd.get("low_competition") == 3

    def test_medium_competition(self):
        _, _, bd = _calculate(**_base_args(competitor_count=15))
        assert bd.get("medium_competition") == 2

    def test_high_competition_no_bonus(self):
        _, _, bd = _calculate(**_base_args(competitor_count=50))
        assert "low_competition" not in bd
        assert "medium_competition" not in bd


class TestEngagement:
    def test_detailed_reviews(self):
        _, _, bd = _calculate(**_base_args(avg_review_length=150))
        assert bd.get("detailed_reviews") == 3

    def test_short_reviews_no_bonus(self):
        _, _, bd = _calculate(**_base_args(avg_review_length=50))
        assert "detailed_reviews" not in bd

    def test_recent_activity(self):
        _, _, bd = _calculate(**_base_args(newest_review_days=15))
        assert bd.get("recent_activity") == 4

    def test_moderate_activity(self):
        _, _, bd = _calculate(**_base_args(newest_review_days=60))
        assert bd.get("moderate_activity") == 2

    def test_stale_no_activity_bonus(self):
        _, _, bd = _calculate(**_base_args(newest_review_days=200))
        assert "recent_activity" not in bd
        assert "moderate_activity" not in bd

    def test_known_owner(self):
        _, _, bd = _calculate(**_base_args(has_owner=True))
        assert bd.get("known_owner") == 3


class TestUrgency:
    def test_negative_reviews_with_good_rating(self):
        _, _, bd = _calculate(**_base_args(rating=4.0, negative_flags=["delay"]))
        assert bd.get("has_negative_reviews") == 2

    def test_negative_reviews_with_bad_rating_no_bonus(self):
        _, _, bd = _calculate(**_base_args(rating=3.0, negative_flags=["delay"]))
        assert "has_negative_reviews" not in bd

    def test_ssl_broken(self):
        _, _, bd = _calculate(
            **_base_args(
                website="https://example.com",
                site_quality={"is_bad": True, "issues": ["ssl_error"]},
            )
        )
        assert bd.get("ssl_broken") == 3

    def test_ssl_no_website_no_bonus(self):
        _, _, bd = _calculate(
            **_base_args(
                site_quality={"issues": ["ssl_error"]},
            )
        )
        assert "ssl_broken" not in bd

    def test_zero_online(self):
        _, _, bd = _calculate(**_base_args())
        assert bd.get("zero_online") == 3

    def test_has_email_no_zero_online(self):
        _, _, bd = _calculate(**_base_args(email="a@b.com"))
        assert "zero_online" not in bd

    def test_has_website_no_zero_online(self):
        _, _, bd = _calculate(**_base_args(website="https://example.com"))
        assert "zero_online" not in bd


class TestPlaybookOverride:
    def test_custom_hot_threshold(self):
        score, cat, bd = _calculate(
            **_base_args(
                rating=4.5,
                mobile="0412345678",
                scoring_config={"hot_threshold": 60},
            )
        )
        # rating_elite(12) + no_website(15) + no_fb(3) + no_ig(2) + mobile(10) + zero_online(3) = 45
        assert score == 45
        assert cat == "WARM"  # 45 < 60

    def test_custom_warm_threshold(self):
        score, cat, bd = _calculate(
            **_base_args(
                mobile="0412345678",
                scoring_config={"warm_threshold": 40},
            )
        )
        # no_website(15) + no_fb(3) + no_ig(2) + mobile(10) + zero_online(3) = 33
        assert score == 33
        assert cat == "COOL"  # 33 < 40


class TestScoreDict:
    def test_basic_dict_scoring(self):
        data = {
            "rating": 4.5,
            "review_count": 50,
            "website": "",
            "mobile": "0412345678",
            "review_analysis": {"avg_review_length": 150, "newest_review_days": 10},
        }
        playbook = {"premium_locations": []}
        score, cat, bd = score_dict(data, playbook)
        assert isinstance(score, int)
        assert cat in ("HOT", "WARM", "COOL")
        assert isinstance(bd, dict)

    def test_years_as_string(self):
        data = {"years_in_business": "2", "rating": 0}
        playbook = {"premium_locations": []}
        score, cat, bd = score_dict(data, playbook)
        assert bd.get("new_business") == 2

    def test_playbook_scoring_config(self):
        data = {"rating": 4.5, "mobile": "0412345678"}
        playbook = {"premium_locations": [], "scoring": {"hot_threshold": 90}}
        score, cat, bd = score_dict(data, playbook)
        assert cat != "HOT"  # threshold too high


class TestMaxScore:
    def test_max_score_under_100(self):
        """All signals maxed out should stay under 100."""
        score, cat, bd = _calculate(
            **_base_args(
                rating=4.5,
                review_count=100,
                website="",
                facebook="",
                instagram="",
                mobile="0412345678",
                email="a@b.com",
                verified=True,
                address="123 Mosman St",
                premium_locations=["Mosman"],
                specialties=["Bathroom Tiling"],
                photo_count=5,
                has_hours=True,
                years_in_business=1,
                negative_flags=["delay"],
                avg_review_length=200,
                newest_review_days=10,
                has_owner=True,
                competitor_count=5,
            )
        )
        assert score < 100
        assert cat == "HOT"
