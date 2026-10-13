import random
from datetime import UTC, date, datetime, timedelta

import pytest

from coach import performance
from coach.efforts import best_efforts, is_scannable
from coach.performance import Effort, Predictor, baseline, check_goal, equivalent, hard_efforts, records

START = datetime(2026, 10, 1, 7, tzinfo=UTC)


def record(s: float, distance: float, hr: float | None = None) -> dict:
    return {"time": START + timedelta(seconds=s), "distance": distance, "hr": hr}


def test_best_efforts_find_fastest_segment_with_interpolation():
    # 2 km à 4 m/s, puis 1 km à 5 m/s, puis 2 km à 4 m/s.
    records_, t, d = [], 0.0, 0.0
    for speed, meters in ((4.0, 2000), (5.0, 1000), (4.0, 2000)):
        for _ in range(int(meters / speed)):
            records_.append(record(t, d, 150))
            t, d = t + 1, d + speed
    records_.append(record(t, d, 150))
    efforts = best_efforts(records_, (1000.0, 5000.0, 10000.0))
    assert efforts[1000.0]["duration_s"] == 200.0
    assert efforts[5000.0]["duration_s"] == 1200.0  # 500 + 200 + 500
    assert efforts[5000.0]["avg_hr"] == 150
    assert 10000.0 not in efforts  # course trop courte


def test_best_efforts_tolerate_sparse_recording_noise():
    # Enregistrement « intelligent » : un point toutes les 6 s à 3,5 m/s, avec un point bruité (+30 m) toutes les
    # minutes, compensé au suivant. Ce n'est pas un saut du GPS : le marathon reste mesuré.
    records_, d = [], 0.0
    for k in range(2100):
        noise = 30 if k % 10 == 5 else -30 if k % 10 == 6 else 0
        records_.append(record(k * 6, d + max(noise, 0)))
        d += 21.0
    efforts = best_efforts(records_, (42195.0,))
    assert efforts[42195.0]["duration_s"] == pytest.approx(42195 / 3.5, rel=0.01)


def test_best_efforts_skip_gps_jumps():
    # 3 km à 4 m/s, mais un saut de 300 m en 2 s au milieu (tunnel) : le km qui le contient est ignoré.
    records_, d = [], 0.0
    for t in range(0, 760):
        if t == 400:
            d += 300
        records_.append(record(t, d))
        d += 4.0
    efforts = best_efforts(records_, (1000.0,))
    assert efforts[1000.0]["duration_s"] == 250.0


def test_indoor_runs_are_not_scanned():
    run = {"sport": "running", "sub_sport": None, "file_path": "a.fit", "distance_m": 8000, "duration_s": 2400}
    assert is_scannable(run)
    assert not is_scannable({**run, "sub_sport": "treadmill"})
    assert not is_scannable({**run, "sport": "cycling"})
    assert not is_scannable({**run, "file_path": None})
    assert not is_scannable({**run, "duration_s": 1000})  # 8 m/s de moyenne : vélo enregistré comme course
    assert not is_scannable({**run, "distance_m": 4000})  # moins que le plus court effort (5 km)


def effort(day: date, distance_m: float, duration_s: float, avg_hr: int | None = None, id_: str = "a") -> Effort:
    return Effort(id_, day, distance_m, duration_s, avg_hr)


def test_baseline_uses_best_recent_effort_with_age_penalty():
    today = date(2026, 10, 13)
    efforts = [
        effort(date(2026, 3, 1), 10000, 2400),   # plus vieux que 180 jours : ignoré
        effort(date(2026, 9, 1), 10000, 2700),
        effort(date(2026, 10, 10), 5000, 1310),  # ≈ 2732 s sur 10 km : moins bon malgré la pénalité du 1er
    ]
    value, source = baseline(efforts, today)
    assert source.day == date(2026, 9, 1)
    assert value == pytest.approx(2700 * (1 + performance.AGE_PENALTY_PER_DAY * 42))
    assert baseline(efforts, date(2026, 8, 1)) is not None
    assert baseline([], today) is None


def test_hard_efforts_skip_easy_runs():
    efforts = [
        effort(date(2026, 9, 1), 10000, 2500, 170),
        effort(date(2026, 9, 3), 10000, 3200, 140),   # footing : lent et FC basse
        effort(date(2026, 9, 5), 10000, 2520, None),  # à 1 % du meilleur, FC inconnue
        effort(date(2026, 9, 7), 10000, 2540, 120),   # rapide mais FC basse : capteur douteux
    ]
    hard = hard_efforts(efforts, hr_rest=50, hr_max=190)
    assert [e.duration_s for e in hard] == [2500, 2520]


def test_check_goal_accounts_for_preparation_time():
    today = date(2026, 10, 13)
    in_4_months = today + timedelta(days=122)  # ≈ 4 % de progression atteignable
    assert check_goal(3000, 2900, in_4_months, today)["verdict"] == "realistic"
    assert check_goal(3000, 2800, in_4_months, today)["verdict"] == "ambitious"
    assert check_goal(3000, 3200, in_4_months, today)["verdict"] == "conservative"
    assert check_goal(3000, 2900, today + timedelta(days=7), today)["verdict"] == "ambitious"


def history(n_weeks: int, today: date, seed: int = 0) -> tuple[list[dict], list[dict]]:
    """Coureur qui progresse : 3 sorties par semaine, un 10 km à fond toutes les 2 semaines."""
    rng = random.Random(seed)
    activities, rows = [], []
    for w in range(n_weeks):
        week_start = today - timedelta(weeks=n_weeks - w)
        fitness = 1 - 0.1 * w / n_weeks  # de 50 min à 45 min sur 10 km
        for k, offset in enumerate((1, 3, 6)):
            day = week_start + timedelta(days=offset)
            hard = k == 2 and w % 2 == 0
            duration = 3000 * fitness * (1 + rng.uniform(-0.01, 0.01)) if hard else 3600
            id_ = f"fit:{w}-{k}"
            start = f"{day}T07:00:00+00:00"
            activities.append({"id": id_, "sport": "running", "sub_sport": None, "start_time": start,
                               "duration_s": duration, "distance_m": 10000, "avg_hr": 172 if hard else 140})
            rows.append({"activity_id": id_, "start_time": start, "distance_m": 10000.0, "duration_s": duration,
                         "avg_hr": 172 if hard else 140})
    return activities, rows


def test_predictor_keeps_baseline_with_little_data():
    today = date(2026, 10, 13)
    activities, rows = history(20, today)
    model = Predictor(activities, rows, 50, 190, today)
    assert model.method == "baseline"
    assert model.evaluation["targets"] < performance.MIN_TARGETS
    prediction = model.predict(today, 21097.5)
    best_recent = min(r["duration_s"] for r in rows if r["start_time"] >= str(today - timedelta(days=180)))
    assert prediction["time_s"] > equivalent(best_recent, 10000, 21097.5)  # pénalité d'ancienneté
    curve = model.curve(today)
    assert curve[-1]["day"] == str(today) and curve[-1]["time_s"] is not None
    assert curve[0]["time_s"] is None  # rien avant le premier effort


def test_predictor_evaluates_xgboost_on_unseen_efforts():
    if performance._xgboost() is None:
        pytest.skip("XGBoost indisponible (macOS : brew install libomp)")
    today = date(2026, 10, 13)
    activities, rows = history(200, today)
    model = Predictor(activities, rows, 50, 190, today)
    evaluation = model.evaluation
    assert evaluation["targets"] >= performance.MIN_TARGETS
    assert evaluation["tested"] > 0 and evaluation["xgboost_error"] is not None
    assert model.method in ("baseline", "xgboost")
    assert model.predict(today, 10000)["time_s"] > 0


def test_records_all_time_and_last_year():
    rows = [
        {"activity_id": "old", "start_time": "2018-05-01T07:00:00+00:00", "distance_m": 5000.0, "duration_s": 1200,
         "avg_hr": None},
        {"activity_id": "new", "start_time": "2026-09-01T07:00:00+00:00", "distance_m": 5000.0, "duration_s": 1300,
         "avg_hr": None},
    ]
    (five,) = records(rows, date(2026, 10, 13))
    assert five["all_time"]["activity_id"] == "old" and five["last_year"]["activity_id"] == "new"
