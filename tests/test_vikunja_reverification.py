"""Vikunja reverification task sync (mocked HTTP)."""

from housing_list_search.vikunja_reverification import (
    reverify_task_title,
    sync_reverification_tasks,
)


def test_reverify_task_title():
    assert reverify_task_title("City of Campbell") == "[Reverify] City of Campbell"


def test_sync_noop_without_env(monkeypatch):
    monkeypatch.delenv("HLS_VIKUNJA_URL", raising=False)
    monkeypatch.delenv("HLS_VIKUNJA_TOKEN", raising=False)
    sync_reverification_tasks(
        run_id="r1",
        suspicious_zero_authorities=["City A"],
        reverification_due_authorities=[],
    )


def test_sync_creates_task(monkeypatch):
    monkeypatch.setenv("HLS_VIKUNJA_URL", "https://vikunja.example")
    monkeypatch.setenv("HLS_VIKUNJA_TOKEN", "test-token")
    monkeypatch.setenv("HLS_VIKUNJA_PROJECT_ID", "9")

    calls: list[tuple[str, str]] = []

    class FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_get(url, **kwargs):
        calls.append(("GET", url))
        return FakeResp([])

    def fake_post(url, **kwargs):
        calls.append(("POST", url))
        return FakeResp({"id": 901})

    monkeypatch.setattr("housing_list_search.vikunja_reverification.polite_get", fake_get)
    monkeypatch.setattr("housing_list_search.vikunja_reverification.polite_post", fake_post)

    sync_reverification_tasks(
        run_id="run-99",
        suspicious_zero_authorities=["MidPen Housing"],
        reverification_due_authorities=[],
    )

    assert calls[0] == ("GET", "https://vikunja.example/api/v1/projects/9/tasks")
    assert calls[1][0] == "POST"
    assert "/projects/9/tasks" in calls[1][1]


def test_list_open_tasks_passes_headers_to_polite_get(monkeypatch):
    """#1051: signature contract — polite_get must accept headers= from Vikunja."""
    import inspect

    import housing_list_search.vikunja_reverification as vr
    from housing_list_search.access import polite_get

    # Live signature must accept headers= (regression: TypeError before network)
    sig = inspect.signature(polite_get)
    assert "headers" in sig.parameters

    monkeypatch.setenv("HLS_VIKUNJA_URL", "https://vikunja.example")
    monkeypatch.setenv("HLS_VIKUNJA_TOKEN", "test-token")
    monkeypatch.setenv("HLS_VIKUNJA_PROJECT_ID", "9")

    seen: dict = {}

    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return []

    def capturing_get(url, *args, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        # Exercise real polite_get signature path by calling with headers=
        # (we do not hit the network — return fake)
        return FakeResp()

    monkeypatch.setattr(vr, "polite_get", capturing_get)
    monkeypatch.setattr(vr, "polite_post", lambda *a, **k: FakeResp())

    sync_reverification_tasks(
        run_id="run-sig",
        suspicious_zero_authorities=["City A"],
        reverification_due_authorities=[],
    )
    assert "headers" in seen["kwargs"]
    assert seen["kwargs"]["headers"]["Authorization"] == "Bearer test-token"


def test_sync_updates_existing_task(monkeypatch):
    monkeypatch.setenv("HLS_VIKUNJA_URL", "https://vikunja.example")
    monkeypatch.setenv("HLS_VIKUNJA_TOKEN", "test-token")

    class FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_get(url, **kwargs):
        return FakeResp([{"id": 42, "title": "[Reverify] City of Campbell", "done": False}])

    posts: list[str] = []

    def fake_post(url, **kwargs):
        posts.append(url)
        return FakeResp({"id": 42})

    monkeypatch.setattr("housing_list_search.vikunja_reverification.polite_get", fake_get)
    monkeypatch.setattr("housing_list_search.vikunja_reverification.polite_post", fake_post)

    sync_reverification_tasks(
        run_id="run-100",
        suspicious_zero_authorities=[],
        reverification_due_authorities=["City of Campbell"],
    )

    assert posts == ["https://vikunja.example/api/v1/tasks/42"]


def test_sync_creates_task_for_low_yield(monkeypatch):
    """#242: low_yield must open Vikunja reverify tasks, not only logs."""
    monkeypatch.setenv("HLS_VIKUNJA_URL", "https://vikunja.example")
    monkeypatch.setenv("HLS_VIKUNJA_TOKEN", "test-token")
    monkeypatch.setenv("HLS_VIKUNJA_PROJECT_ID", "9")

    bodies: list[dict] = []

    class FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_get(url, **kwargs):
        return FakeResp([])

    def fake_post(url, **kwargs):
        bodies.append(kwargs.get("json") or {})
        return FakeResp({"id": 902})

    monkeypatch.setattr("housing_list_search.vikunja_reverification.polite_get", fake_get)
    monkeypatch.setattr("housing_list_search.vikunja_reverification.polite_post", fake_post)

    sync_reverification_tasks(
        run_id="run-thin",
        suspicious_zero_authorities=[],
        reverification_due_authorities=[],
        low_yield=[("MidPen Housing", 5)],
    )

    assert len(bodies) == 1
    assert bodies[0].get("title") == "[Reverify] MidPen Housing"
    assert "Low-yield" in (bodies[0].get("description") or "")
    assert "5 property" in (bodies[0].get("description") or "")
