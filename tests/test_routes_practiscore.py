"""Integration tests for new PractiScore API routes."""
from datetime import datetime, timezone, date

import pytest

import app.services.task_manager as task_manager
from app.models import Member
from app.models.practiscore_match import PractiscoreMatch
from app.models.practiscore_result import PractiscoreResult
from app.services.cache import cache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_member(db_session, member_number: str = "A12345") -> Member:
    member = Member(
        member_number=member_number,
        last_scraped_at=datetime.now(timezone.utc),
    )
    db_session.add(member)
    db_session.flush()
    return member


def _create_ps_match(
    db_session,
    member: Member,
    match_name: str = "Spring Steel 2024",
    match_date: date | None = date(2024, 3, 15),
    division: str = "Limited",
    match_level: int | None = 2,
    total_competitors: int | None = 30,
) -> PractiscoreMatch:
    m = PractiscoreMatch(
        member_id=member.id,
        match_name=match_name,
        match_date=match_date,
        match_level=match_level,
        division=division,
        total_competitors=total_competitors,
    )
    db_session.add(m)
    db_session.flush()
    return m


def _create_ps_result(
    db_session,
    match: PractiscoreMatch,
    shooter_name: str = "Bob Jones",
    member_number: str = "A12345",
    placement: int = 5,
    percent_of_winner: float = 84.5,
    is_queried_member: bool = True,
) -> PractiscoreResult:
    r = PractiscoreResult(
        match_id=match.id,
        shooter_name=shooter_name,
        member_number=member_number,
        division="Limited",
        classification="B",
        placement=placement,
        percent_of_winner=percent_of_winner,
        is_queried_member=is_queried_member,
    )
    db_session.add(r)
    db_session.flush()
    return r


# ---------------------------------------------------------------------------
# POST /api/analyze/{member_number}/practiscore
# ---------------------------------------------------------------------------


class TestAnalyzeMemberPractiscore:
    def test_returns_202_with_job_id_when_not_cached(self, client):
        from unittest.mock import AsyncMock, patch

        with patch("app.routes.members.scrape_practiscore_and_store", new_callable=AsyncMock):
            resp = client.post("/api/analyze/A12345/practiscore")

        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "accepted"
        assert "job_id" in body

    def test_cached_returns_200(self, client):
        cache.set("practiscore:A12345", {"matches": [], "stats": {}})
        resp = client.post("/api/analyze/A12345/practiscore")
        assert resp.status_code == 200
        assert resp.json()["status"] == "complete"

    def test_pending_job_returns_existing_job_id(self, client):
        existing = task_manager.create_job("A12345", job_type="practiscore")
        resp = client.post("/api/analyze/A12345/practiscore")
        assert resp.status_code == 202
        assert resp.json()["job_id"] == existing
        assert resp.json()["status"] == "pending"

    def test_invalid_member_number_returns_422(self, client):
        resp = client.post("/api/analyze/AB/practiscore")
        assert resp.status_code == 422

    def test_member_number_uppercased(self, client):
        cache.set("practiscore:A12345", {"matches": []})
        resp = client.post("/api/analyze/a12345/practiscore")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/member/{member_number}/practiscore
# ---------------------------------------------------------------------------


class TestGetMemberPractiscore:
    def test_returns_404_when_member_missing(self, client):
        resp = client.get("/api/member/Z99999/practiscore")
        assert resp.status_code == 404

    def test_returns_404_when_no_practiscore_data(self, client, db_session):
        _create_member(db_session)
        db_session.commit()

        resp = client.get("/api/member/A12345/practiscore")
        assert resp.status_code == 404

    def test_returns_200_with_practiscore_data(self, client, db_session):
        member = _create_member(db_session)
        match = _create_ps_match(db_session, member)
        _create_ps_result(db_session, match)
        db_session.commit()

        resp = client.get("/api/member/A12345/practiscore")
        assert resp.status_code == 200

    def test_matches_list_in_response(self, client, db_session):
        member = _create_member(db_session)
        match = _create_ps_match(db_session, member)
        _create_ps_result(db_session, match)
        db_session.commit()

        body = client.get("/api/member/A12345/practiscore").json()
        assert "matches" in body
        assert len(body["matches"]) == 1

    def test_stats_in_response(self, client, db_session):
        member = _create_member(db_session)
        match = _create_ps_match(db_session, member)
        _create_ps_result(db_session, match)
        db_session.commit()

        body = client.get("/api/member/A12345/practiscore").json()
        assert "stats" in body
        stats = body["stats"]
        assert "total_matches" in stats
        assert stats["total_matches"] == 1

    def test_match_fields_present(self, client, db_session):
        member = _create_member(db_session)
        match = _create_ps_match(db_session, member, match_name="Big Match 2024", match_level=2)
        _create_ps_result(db_session, match, placement=5, percent_of_winner=84.5)
        db_session.commit()

        body = client.get("/api/member/A12345/practiscore").json()
        m = body["matches"][0]
        assert m["match_name"] == "Big Match 2024"
        assert m["match_date"] == "2024-03-15"
        assert m["match_level"] == 2
        assert m["member_placement"] == 5
        assert m["member_percent"] == pytest.approx(84.5)
        assert m["total_competitors"] == 30

    def test_placement_percentile_calculated(self, client, db_session):
        # placement=5, total=30 → percentile = (30-5)/30 * 100 = 83.33
        member = _create_member(db_session)
        match = _create_ps_match(db_session, member, total_competitors=30)
        _create_ps_result(db_session, match, placement=5)
        db_session.commit()

        body = client.get("/api/member/A12345/practiscore").json()
        pct = body["matches"][0]["placement_percentile"]
        assert pct == pytest.approx(83.33, abs=0.1)

    def test_best_and_worst_placement_in_stats(self, client, db_session):
        member = _create_member(db_session)
        m1 = _create_ps_match(db_session, member, match_name="Match A", match_date=date(2024, 1, 1), total_competitors=20)
        _create_ps_result(db_session, m1, placement=1)
        m2 = _create_ps_match(db_session, member, match_name="Match B", match_date=date(2024, 2, 1), total_competitors=20)
        _create_ps_result(db_session, m2, placement=10)
        db_session.commit()

        body = client.get("/api/member/A12345/practiscore").json()
        stats = body["stats"]
        assert stats["best_placement"] == 1
        assert stats["worst_placement"] == 10

    def test_avg_percent_of_winner_in_stats(self, client, db_session):
        member = _create_member(db_session)
        m1 = _create_ps_match(db_session, member, match_name="Match A", match_date=date(2024, 1, 1))
        _create_ps_result(db_session, m1, percent_of_winner=80.0)
        m2 = _create_ps_match(db_session, member, match_name="Match B", match_date=date(2024, 2, 1))
        _create_ps_result(db_session, m2, percent_of_winner=90.0)
        db_session.commit()

        body = client.get("/api/member/A12345/practiscore").json()
        assert body["stats"]["avg_percent_of_winner"] == pytest.approx(85.0)

    def test_cached_response_returned(self, client, db_session):
        _create_member(db_session)
        db_session.commit()

        # Inject cached data
        cache.set("practiscore:A12345", {"matches": [{"cached": True}], "stats": {}})

        body = client.get("/api/member/A12345/practiscore").json()
        assert body["matches"][0].get("cached") is True

    def test_invalid_member_number_returns_422(self, client):
        resp = client.get("/api/member/AB/practiscore")
        assert resp.status_code == 422

    def test_improvement_trend_with_multiple_matches(self, client, db_session):
        member = _create_member(db_session)
        for i, (d, pct) in enumerate([
            (date(2024, 1, 1), 60.0),
            (date(2024, 2, 1), 70.0),
            (date(2024, 3, 1), 80.0),
        ]):
            m = _create_ps_match(
                db_session, member,
                match_name=f"Match {i}",
                match_date=d,
                total_competitors=100,
            )
            _create_ps_result(db_session, m, placement=int(100 - pct), percent_of_winner=pct)
        db_session.commit()

        body = client.get("/api/member/A12345/practiscore").json()
        # trend should be positive (improving placements)
        assert body["stats"]["improvement_trend"] is not None
        assert body["stats"]["improvement_trend"] > 0

    def test_no_member_result_row_handled_gracefully(self, client, db_session):
        """Match exists but no is_queried_member result — should return None for placement/pct."""
        member = _create_member(db_session)
        match = _create_ps_match(db_session, member)
        # Add a non-member result only
        _create_ps_result(db_session, match, shooter_name="Other Guy", member_number="Z99999", is_queried_member=False)
        db_session.commit()

        body = client.get("/api/member/A12345/practiscore").json()
        m = body["matches"][0]
        assert m["member_placement"] is None
        assert m["member_percent"] is None
