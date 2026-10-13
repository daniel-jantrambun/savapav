from datetime import date, timedelta

import pytest

from coach.planner import Goal, build_plan, training_paces, validate_plan

START = date(2026, 10, 12)  # un lundi


@pytest.mark.parametrize(
    "distance, target, weeks, current, runs",
    [
        (21.1, 6300, 16, 25.0, 4),   # semi en 1h45
        (42.195, 13500, 18, 40.0, 5),  # marathon en 3h45
        (10, 2700, 10, 15.0, 3),     # 10 km en 45 min
        (5, 1320, 6, 0.0, 3),        # 5 km depuis zéro
        (21.1, 6300, 16, 80.0, 6),   # déjà au-dessus du volume recommandé
    ],
)
def test_plans_respect_guardrails(distance, target, weeks, current, runs):
    race = date.fromordinal(START.toordinal() + 7 * (weeks - 1) + 6)
    plan = build_plan(Goal(distance, target, race, runs), current, START)

    assert validate_plan(plan) == []
    assert len(plan.weeks) == weeks
    assert plan.weeks[-1].phase == "race"
    assert plan.weeks[-1].sessions[-1].kind == "race"
    assert plan.weeks[-1].sessions[-1].date == race
    for week in plan.weeks[:-1]:
        assert len(week.sessions) == runs
        assert all(week.start <= s.date < week.start + timedelta(days=7) for s in week.sessions)


def test_deload_every_fourth_week():
    plan = build_plan(Goal(21.1, 6300, date(2027, 1, 31), 4), 25.0, START)
    build = [w for w in plan.weeks if w.phase in ("base", "build")]
    assert [w.index for w in build if w.deload] == [3, 7, 11]


def test_paces_are_ordered():
    paces = training_paces(Goal(21.1, 6300, date(2027, 1, 31)))
    assert paces["intervals"] < paces["tempo"] < paces["race"] < paces["long"] < paces["easy"]
    assert paces["race"] == round(6300 / 21.1)


def test_too_short_raises():
    with pytest.raises(ValueError):
        build_plan(Goal(10, 2700, date(2026, 10, 18)), 20.0, START)


def test_validate_detects_jump():
    plan = build_plan(Goal(21.1, 6300, date(2027, 1, 31), 4), 25.0, START)
    plan.weeks[2].volume_km = 80
    assert any("dépasse" in e for e in validate_plan(plan))


def test_narrate_week_logs_prompt_and_answer(monkeypatch, caplog):
    import logging

    from coach import llm

    sent = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": " **Séance** facile. "}}]}

    def fake_post(url, json, timeout):
        sent.update(json)
        return Response()

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    week = {"index": 0, "phase": "base", "deload": False, "volume_km": 10, "sessions": []}
    with caplog.at_level(logging.INFO, logger="coach.llm"):
        assert llm.narrate_week(week, "http://llm", "m", 5) == "**Séance** facile."
    logged = caplog.text
    assert llm.SYSTEM_PROMPT in logged
    assert sent["messages"][1]["content"] in logged
    assert "réponse" in logged and "**Séance** facile." in logged
