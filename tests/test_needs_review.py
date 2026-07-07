"""Needs Review notification hook."""

import logging

from housing_list_search.needs_review import notify_needs_review, should_notify_needs_review


def test_notify_noop_when_no_signals():
    notify_needs_review(
        run_id="test",
        suspicious_zero_authorities=[],
        reverification_due_authorities=[],
    )


def test_should_notify_on_stale_threshold():
    assert should_notify_needs_review(
        suspicious_zero_authorities=[],
        reverification_due_authorities=[],
        stale_n=5,
        scrape_failed_n=0,
    )


def test_should_notify_on_scrape_failed():
    assert should_notify_needs_review(
        suspicious_zero_authorities=[],
        reverification_due_authorities=[],
        stale_n=0,
        scrape_failed_n=2,
    )


def test_webhook_blocked_by_url_policy(monkeypatch, caplog):
    monkeypatch.setenv("HLS_NEEDS_REVIEW_WEBHOOK", "http://169.254.169.254/hook")
    synced: list[str] = []

    def fake_sync(**kwargs):
        synced.append("ok")

    monkeypatch.setattr(
        "housing_list_search.vikunja_reverification.sync_reverification_tasks",
        fake_sync,
    )

    with caplog.at_level(logging.WARNING):
        notify_needs_review(
            run_id="run-x",
            suspicious_zero_authorities=["City"],
            reverification_due_authorities=[],
        )

    assert "NEEDS_REVIEW" in caplog.text
    assert "blocked by policy" in caplog.text.lower()


def test_notify_logs_warning(caplog):
    with caplog.at_level(logging.WARNING):
        notify_needs_review(
            run_id="run-1",
            suspicious_zero_authorities=["City A"],
            reverification_due_authorities=[],
        )
    assert "NEEDS_REVIEW" in caplog.text
    assert "City A" in caplog.text


def test_notify_webhook_post(monkeypatch):
    posted: list[dict] = []

    class FakeResp:
        def raise_for_status(self):
            return None

    def fake_post(url, **kwargs):
        posted.append({"url": url, "json": kwargs.get("json")})
        return FakeResp()

    monkeypatch.setenv("HLS_NEEDS_REVIEW_WEBHOOK", "https://example.com/hook")
    monkeypatch.setattr("housing_list_search.needs_review.polite_post", fake_post)

    notify_needs_review(
        run_id="run-2",
        suspicious_zero_authorities=["City B"],
        reverification_due_authorities=["City C"],
        stale_n=3,
        scrape_failed_n=1,
    )

    assert len(posted) == 1
    assert posted[0]["url"] == "https://example.com/hook"
    assert "City B" in str(posted[0]["json"])
    assert "City C" in str(posted[0]["json"])


def test_vikunja_sync_without_webhook(monkeypatch):
    """Vikunja reverification must run when only HLS_VIKUNJA_* is set (#756)."""
    synced: list[str] = []

    def fake_sync(**kwargs):
        synced.append(kwargs.get("run_id", ""))

    monkeypatch.delenv("HLS_NEEDS_REVIEW_WEBHOOK", raising=False)
    monkeypatch.setattr(
        "housing_list_search.vikunja_reverification.sync_reverification_tasks",
        fake_sync,
    )

    notify_needs_review(
        run_id="run-vikunja",
        suspicious_zero_authorities=["City X"],
        reverification_due_authorities=[],
    )

    assert synced == ["run-vikunja"]
