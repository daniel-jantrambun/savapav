import io
import zipfile
from datetime import UTC, date, datetime, timedelta

import pytest

from coach.activity import FitUnavailable, _session_window, _splits, detail, hr_zone_times, pool_splits, read_location
from coach.sports import category_stats

START = datetime(2026, 10, 1, 7, tzinfo=UTC)


def record(s: float, distance: float | None = None, hr: float | None = None) -> dict:
    return {"time": START + timedelta(seconds=s), "distance": distance, "speed": None, "hr": hr,
            "altitude": None, "power": None, "cadence": None}


def test_splits_interpolate_each_km():
    # 4 m/s constants pendant 2,5 km : 250 s par km, puis un demi-km.
    records = [record(t, t * 4.0, 150) for t in range(0, 626)]
    splits = _splits(records, 1000, START)
    assert [s["distance_m"] for s in splits] == [1000, 1000, 500]
    assert [s["duration_s"] for s in splits] == [250.0, 250.0, 125.0]
    assert splits[0]["avg_hr"] == 150


def test_hr_zones_ignore_pauses():
    # FC de réserve 50..190 : 120 bpm = 50 % (Z1), 176 bpm = 90 % (Z5).
    records = [record(t, hr=120) for t in range(0, 60)] + [record(t, hr=176) for t in range(60, 120)]
    records.append(record(600, hr=176))  # 8 min de pause : pas comptée
    zones = hr_zone_times(records, 50, 190)
    assert [z["seconds"] for z in zones] == [60, 0, 0, 0, 59]
    assert zones[1]["low"] == 134 and zones[4]["high"] is None


def test_read_location_in_nested_zip(tmp_path):
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("act.fit", b"fit")
    with zipfile.ZipFile(tmp_path / "export.zip", "w") as z:
        z.writestr("part.zip", inner.getvalue())
    assert read_location(f"{tmp_path / 'export.zip'}!part.zip!act.fit") == b"fit"
    with pytest.raises(FitUnavailable):
        read_location(f"{tmp_path / 'export.zip'}!absent.fit")
    with pytest.raises(FitUnavailable):
        read_location(str(tmp_path / "disparu.fit"))


def test_detail_without_file():
    assert detail({"file_path": None}, "run", 50, 185)["streams"] is None
    assert "introuvable" in detail({"file_path": "/nulle/part.fit"}, "run", 50, 185)["unavailable"]


def test_category_stats_by_calendar_week():
    today = date(2026, 10, 8)  # jeudi
    acts = [
        {"start_time": "2026-10-06T07:00:00+00:00", "sport": "running", "duration_s": 3000, "distance_m": 10000,
         "avg_hr": 150},
        {"start_time": "2026-10-05T07:00:00+00:00", "sport": "running", "duration_s": 1000, "distance_m": None,
         "avg_hr": 120},
        {"start_time": "2026-09-30T07:00:00+00:00", "sport": "training", "duration_s": 2700, "distance_m": None,
         "avg_hr": None},
        {"start_time": "2026-08-01T07:00:00+00:00", "sport": "running", "duration_s": 3000, "distance_m": 10000,
         "avg_hr": 150},
    ]
    stats = category_stats(acts, today, 4)
    assert set(stats) == {"run", "gym"}
    run = stats["run"]["weeks"]
    assert [w["start"] for w in run] == ["2026-09-14", "2026-09-21", "2026-09-28", "2026-10-05"]
    assert run[-1] == {"start": "2026-10-05", "sessions": 2, "minutes": 67, "km": 10.0,
                       "speed_m_s": round(10000 / 3000, 3), "avg_hr": round((150 * 3000 + 120 * 1000) / 4000)}
    assert stats["gym"]["total"]["sessions"] == 1 and stats["gym"]["total"]["speed_m_s"] is None
    assert stats["run"]["bucket"] == "week"


def test_category_stats_by_month_above_five_years():
    today = date(2026, 10, 8)
    acts = [
        {"start_time": "2026-10-06T07:00:00+00:00", "sport": "running", "duration_s": 3000, "distance_m": 10000,
         "avg_hr": 150},
        {"start_time": "2026-10-01T07:00:00+00:00", "sport": "running", "duration_s": 1000, "distance_m": None,
         "avg_hr": 120},
        {"start_time": "2016-11-15T07:00:00+00:00", "sport": "running", "duration_s": 1800, "distance_m": 5000,
         "avg_hr": None},
    ]
    run = category_stats(acts, today, 520)["run"]
    assert run["bucket"] == "month"
    assert run["weeks"][0]["start"] == "2016-10-01"  # 1er du mois du lundi 17 octobre 2016
    assert run["weeks"][-1]["start"] == "2026-10-01" and len(run["weeks"]) == 121
    assert run["weeks"][1] == {"start": "2016-11-01", "sessions": 1, "minutes": 30, "km": 5.0,
                              "speed_m_s": round(5000 / 1800, 3), "avg_hr": None}
    assert run["weeks"][-1] == {"start": "2026-10-01", "sessions": 2, "minutes": 67, "km": 10.0,
                                "speed_m_s": round(10000 / 3000, 3), "avg_hr": round((150 * 3000 + 120 * 1000) / 4000)}


def test_category_stats_start_at_first_activity_of_each_type():
    today = date(2026, 10, 8)
    acts = [
        {"start_time": "2026-09-30T07:00:00+00:00", "sport": "running", "duration_s": 1800, "distance_m": 5000,
         "avg_hr": None},
        {"start_time": "2026-10-06T07:00:00+00:00", "sport": "cycling", "duration_s": 3600, "distance_m": 30000,
         "avg_hr": None},
    ]
    # Course pratiquée bien avant la période : ses 12 semaines restent ; vélo commencé le 6 octobre : 1 semaine.
    firsts = {"run": date(2020, 1, 1), "bike": date(2026, 10, 6)}
    stats = category_stats(acts, today, 12, firsts)
    assert len(stats["run"]["weeks"]) == 12
    assert [w["start"] for w in stats["bike"]["weeks"]] == ["2026-10-05"]


@pytest.mark.parametrize("pool, per_100", [(25.0, 4), (50.0, 2)])
def test_pool_splits_group_lengths_by_100m(pool, per_100):
    # 10 longueurs de 30 s (25 m) ou 60 s (50 m) : même allure 2:00/100 m quel que soit le bassin.
    seconds = 30 * pool / 25
    lengths = [(START + timedelta(seconds=i * seconds), seconds) for i in range(10)]
    records = [record(t, hr=140) for t in range(0, int(10 * seconds))]
    splits = pool_splits(lengths, pool, records)
    full = 10 // per_100
    assert [s["distance_m"] for s in splits[:full]] == [100] * full
    assert all(s["duration_s"] == 120 for s in splits[:full]) and splits[0]["avg_hr"] == 140
    assert sum(s["distance_m"] for s in splits) == 10 * pool  # reste partiel en fin de séance


def test_multisport_file_is_cut_per_session():
    # Natation 0-600 s puis vélo 600-1200 s dans le même fichier, distance cumulée.
    parsed = {
        "records": [record(t, distance=t * (1.0 if t < 600 else 8.0)) for t in range(0, 1200, 10)],
        "lengths": [],
        "sessions": [{"start": START, "elapsed": 590, "pool_length": None},
                     {"start": START + timedelta(seconds=600), "elapsed": 600, "pool_length": None}],
    }
    _, bike, _ = _session_window(parsed, (START + timedelta(seconds=600)).isoformat())
    assert bike[0]["time"] == START + timedelta(seconds=600)
    assert bike[0]["distance"] == 0 and bike[-1]["distance"] == 1190 * 8.0 - 600 * 8.0
    _, swim, _ = _session_window(parsed, START.isoformat())
    assert len(swim) == 60 and swim[0]["distance"] == 0
