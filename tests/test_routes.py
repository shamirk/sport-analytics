"""Integration tests for API routes using FastAPI TestClient + SQLite."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

import app.services.task_manager as task_manager
from app.models import CurrentClassification, Division, Member
from app.services.cache import cache


# ---------------------------------------------------------------------------
# Helpers: seed DB with member + division data
# ---------------------------------------------------------------------------


def _create_member(db_session, member_number: str = "A12345") -> Member:
    member = Member(
        member_number=member_number,
        last_scraped_at=datetime.now(timezone.utc),
    )
    db_session.add(member)
    db_session.flush()
    return member


def _create_division(db_session, name: str = "Limited", abbr: str = "LTD") -> Division:
    div = Division(name=name, abbreviation=abbr)
    db_session.add(div)
    db_session.flush()
    return div


def _create_classification(
    db_session, member: Member, division: Division, cls: str = "B", pct: float = 72.5
) -> CurrentClassification:
    cc = CurrentClassification(
        member_id=member.id,
        division_id=division.id,
        classification_class=cls,
        percentage=pct,
    )
    db_session.add(cc)
    db_session.flush()
    return cc


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    def test_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_body_has_status(self, client):
        resp = client.get("/health")
        assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# POST /api/analyze/{member_number}
# ---------------------------------------------------------------------------


class TestAnalyzeMember:
    def test_cached_returns_200_with_data(self, client):
        cached_data = {"member_number": "A12345", "current_classifications": []}
        cache.set("analyze:A12345", cached_data)

        resp = client.post("/api/analyze/A12345")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "complete"
        assert body["data"] == cached_data

    def test_no_cache_returns_202_with_job_id(self, client):
        with patch("app.routes.members.scrape_and_store", new_callable=AsyncMock):
            resp = client.post("/api/analyze/A12345")
        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "accepted"
        assert "job_id" in body

    def test_pending_job_returns_202_with_existing_job(self, client):
        existing_job = task_manager.create_job("A12345")
        resp = client.post("/api/analyze/A12345")
        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "pending"
        assert body["job_id"] == existing_job

    def test_invalid_member_number_returns_422(self, client):
        resp = client.post("/api/analyze/AB")  # too short
        assert resp.status_code == 422

    def test_member_number_uppercased(self, client):
        cached_data = {"member_number": "A12345"}
        cache.set("analyze:A12345", cached_data)

        resp = client.post("/api/analyze/a12345")
        assert resp.status_code == 200

    def test_special_chars_in_member_number_returns_422(self, client):
        resp = client.post("/api/analyze/A-1234")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/member/{member_number}
# ---------------------------------------------------------------------------


class TestGetMember:
    def test_returns_200_for_existing_member(self, client, db_session):
        member = _create_member(db_session)
        division = _create_division(db_session)
        _create_classification(db_session, member, division)
        db_session.commit()

        resp = client.get("/api/member/A12345")
        assert resp.status_code == 200

    def test_returns_member_number(self, client, db_session):
        _create_member(db_session, "B99999")
        db_session.commit()

        resp = client.get("/api/member/B99999")
        assert resp.json()["member_number"] == "B99999"

    def test_returns_classifications(self, client, db_session):
        member = _create_member(db_session)
        division = _create_division(db_session)
        _create_classification(db_session, member, division, cls="A", pct=85.0)
        db_session.commit()

        resp = client.get("/api/member/A12345")
        body = resp.json()
        assert len(body["current_classifications"]) == 1
        cls_data = body["current_classifications"][0]
        assert cls_data["division"] == "Limited"
        assert cls_data["class"] == "A"
        assert cls_data["percentage"] == pytest.approx(85.0)

    def test_returns_404_for_unknown_member(self, client):
        resp = client.get("/api/member/Z99999")
        assert resp.status_code == 404

    def test_404_body_has_error_field(self, client):
        resp = client.get("/api/member/Z99999")
        body = resp.json()
        assert "error" in body

    def test_invalid_member_number_returns_422(self, client):
        resp = client.get("/api/member/AB")
        assert resp.status_code == 422

    def test_no_classifications_returns_empty_list(self, client, db_session):
        _create_member(db_session)
        db_session.commit()

        resp = client.get("/api/member/A12345")
        assert resp.json()["current_classifications"] == []


# ---------------------------------------------------------------------------
# GET /api/member/{member_number}/dashboard
# ---------------------------------------------------------------------------


class TestGetMemberDashboard:
    def test_returns_200_for_existing_member(self, client, db_session):
        member = _create_member(db_session)
        _create_division(db_session)
        db_session.commit()

        resp = client.get("/api/member/A12345/dashboard")
        assert resp.status_code == 200

    def test_returns_overview_section(self, client, db_session):
        _create_member(db_session)
        db_session.commit()

        resp = client.get("/api/member/A12345/dashboard")
        body = resp.json()
        assert "overview" in body
        assert body["overview"]["member_number"] == "A12345"

    def test_returns_all_sections(self, client, db_session):
        _create_member(db_session)
        db_session.commit()

        resp = client.get("/api/member/A12345/dashboard")
        body = resp.json()
        assert "time_series" in body
        assert "division_stats" in body
        assert "classifier_breakdown" in body
        assert "match_stats" in body

    def test_cache_hit_returns_cached(self, client, db_session):
        _create_member(db_session)
        db_session.commit()

        # Prime cache
        resp1 = client.get("/api/member/A12345/dashboard")
        assert resp1.status_code == 200

        # Second call should hit cache (same data)
        resp2 = client.get("/api/member/A12345/dashboard")
        assert resp2.status_code == 200
        assert resp2.json() == resp1.json()

    def test_refresh_bypasses_cache(self, client, db_session):
        _create_member(db_session)
        db_session.commit()

        # Inject stale data into cache
        cache.set("dashboard:A12345", {"stale": True})

        resp = client.get("/api/member/A12345/dashboard?refresh=true")
        assert resp.status_code == 200
        assert "stale" not in resp.json()

    def test_cached_without_refresh(self, client, db_session):
        cache.set("dashboard:A12345", {"cached": "value"})
        resp = client.get("/api/member/A12345/dashboard")
        assert resp.json() == {"cached": "value"}

    def test_returns_404_for_unknown_member(self, client):
        resp = client.get("/api/member/Z99999/dashboard")
        assert resp.status_code == 404

    def test_invalid_member_number_returns_422(self, client):
        resp = client.get("/api/member/AB/dashboard")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/member/{member_number}/status
# ---------------------------------------------------------------------------


class TestGetMemberStatus:
    def test_not_started_when_no_job_no_db_record(self, client):
        resp = client.get("/api/member/A12345/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "not_started"

    def test_complete_when_member_in_db_with_scrape_date(self, client, db_session):
        _create_member(db_session)
        db_session.commit()

        resp = client.get("/api/member/A12345/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "complete"

    def test_pending_status_from_job(self, client):
        job_id = task_manager.create_job("A12345")
        resp = client.get("/api/member/A12345/status")
        body = resp.json()
        assert body["status"] == "pending"
        assert body["job_id"] == job_id

    def test_in_progress_status_from_job(self, client):
        job_id = task_manager.create_job("A12345")
        task_manager.job_status[job_id]["status"] = "in_progress"

        resp = client.get("/api/member/A12345/status")
        assert resp.json()["status"] == "in_progress"

    def test_error_status_includes_error_message(self, client):
        job_id = task_manager.create_job("A12345")
        task_manager.job_status[job_id]["status"] = "error"
        task_manager.job_status[job_id]["error"] = "member not found"

        resp = client.get("/api/member/A12345/status")
        body = resp.json()
        assert body["status"] == "error"
        assert "member not found" in body["error"]

    def test_complete_status_from_job(self, client):
        job_id = task_manager.create_job("A12345")
        task_manager.job_status[job_id]["status"] = "complete"

        resp = client.get("/api/member/A12345/status")
        assert resp.json()["status"] == "complete"

    def test_invalid_member_number_returns_422(self, client):
        resp = client.get("/api/member/AB/status")
        assert resp.status_code == 422

    def test_member_number_returned_in_response(self, client):
        resp = client.get("/api/member/A12345/status")
        assert resp.json()["member_number"] == "A12345"
