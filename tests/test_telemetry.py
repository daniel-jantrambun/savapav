import dataclasses

import httpx
import pytest
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from coach import auth, telemetry
from coach.api import create_app
from coach.config import load_settings
from coach.db import connect

PASSWORD = "correct horse battery"
GOAL = {"distance_km": 10, "target_time_s": 2700, "race_date": "2027-03-07", "runs_per_week": 3}

# Le fournisseur global ne se pose qu'une fois par process : un exporteur en mémoire partagé par les tests.
_exporter = InMemorySpanExporter()
_provider = TracerProvider()
_provider.add_span_processor(SimpleSpanProcessor(_exporter))
trace.set_tracer_provider(_provider)


@pytest.fixture
def settings(tmp_path):
    return dataclasses.replace(load_settings(), data_dir=tmp_path, db_path=tmp_path / "coach.sqlite",
                               fit_dir=tmp_path / "fit", secret_key="phrase secrète de test")


@pytest.fixture
def spans(monkeypatch):
    """Télémétrie active, exportée en mémoire au lieu du Collector."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4318")
    monkeypatch.setattr(telemetry, "_configure_providers", lambda: None)
    _exporter.clear()
    yield _exporter
    _exporter.clear()


def logged_in(settings) -> TestClient:
    conn = connect(settings.db_path)
    auth.create_user(conn, "alice", PASSWORD)
    conn.close()
    auth._failures.clear()
    client = TestClient(create_app(settings))
    assert client.post("/api/login", json={"username": "alice", "password": PASSWORD}).status_code == 200
    return client


def test_disabled_without_endpoint(settings, monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    called = []
    monkeypatch.setattr(telemetry, "_configure_providers", lambda: called.append(True))
    app = create_app(settings)
    assert not called
    assert not getattr(app, "_is_instrumented_by_opentelemetry", False)


def test_requests_are_traced_except_health(settings, spans):
    client = logged_in(settings)
    spans.clear()
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/plans").status_code == 200
    names = [s.name for s in spans.get_finished_spans()]
    assert names == ["GET /api/plans"]


def test_narration_trace(settings, spans, monkeypatch):
    def fake_post(url, json, timeout):
        return httpx.Response(200, json={"choices": [{"message": {"content": " Footing facile. "}}],
                                         "usage": {"prompt_tokens": 120, "completion_tokens": 30}},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr("coach.llm.httpx.post", fake_post)
    client = logged_in(settings)
    plan_id = client.post("/api/plans", json=GOAL).json()["id"]
    spans.clear()
    assert client.post(f"/api/plans/{plan_id}/narrate", json=[0]).status_code == 202

    by_name = {s.name: s for s in spans.get_finished_spans()}
    request, narrate, week = by_name["POST /api/plans/{plan_id}/narrate"], by_name["plan.narrate"], by_name["llm.week"]
    # Une seule trace, du clic à la fin de la rédaction (tâche de fond comprise).
    assert narrate.context.trace_id == request.context.trace_id
    assert week.parent.span_id == narrate.context.span_id
    assert narrate.attributes["plan.id"] == plan_id
    assert narrate.attributes["plan.weeks_narrated"] == 1
    assert week.attributes["plan.week"] == 1
    assert week.attributes["llm.usage.prompt_tokens"] == 120
    # Le span de la requête se ferme avant la tâche de fond : les latences des spanmetrics restent justes.
    assert request.end_time <= narrate.start_time
