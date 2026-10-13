"""Détail d'une activité : courbes seconde par seconde (relues dans le FIT d'origine), découpage par km,
longueurs de bassin, temps passé par zone de FC.

On ne stocke pas les streams en base : le FIT brut est conservé, on le relit à la demande (quelques
centaines de ms) et on n'en renvoie qu'un échantillon réduit, suffisant pour un graphe sur téléphone.
"""

import io
import zipfile
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

import fitdecode

from coach.sports import BIKE, RUN, SWIM

MAX_POINTS = 500
MAX_GAP_S = 10            # au-delà, l'écart entre deux points est une pause : il ne compte pas dans les zones
SPLIT_M = {RUN: 1000, BIKE: 5000, SWIM: 100}  # natation : eau libre (GPS) ; en bassin, voir pool_splits
# Zones de FC en % de FC de réserve (Karvonen), Z1 à Z5.
HR_ZONE_BOUNDS = (0.0, 0.6, 0.7, 0.8, 0.9)


class FitUnavailable(Exception):
    pass


def read_location(location: str) -> bytes:
    """Contenu d'un FIT à partir de son emplacement, y compris dans des zip imbriqués ("a.zip!b.zip!c.fit")."""
    path, *inner = location.split("!")
    try:
        data = Path(path).read_bytes()
        for name in inner:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                data = archive.read(name)
    except (OSError, KeyError, zipfile.BadZipFile) as e:
        raise FitUnavailable(f"Fichier FIT introuvable ({path})") from e
    return data


def _value(frame, *names):
    for name in names:
        if frame.has_field(name):
            value = frame.get_value(name, fallback=None)
            if value is not None:
                return value
    return None


@lru_cache(maxsize=16)
def parse_streams(location: str) -> dict:
    """Points `record`, longueurs de bassin et, par session, sa fenêtre horaire et la longueur du bassin."""
    return parse_fit_streams(read_location(location))


def parse_fit_streams(data: bytes) -> dict:
    records, lengths, sessions = [], [], []
    with fitdecode.FitReader(io.BytesIO(data), check_crc=fitdecode.CrcCheck.DISABLED) as fit:
        for frame in fit:
            if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                continue
            if frame.name == "record":
                timestamp = _value(frame, "timestamp")
                if timestamp is None:
                    continue
                cadence = _value(frame, "cadence")
                if cadence is not None:
                    cadence += _value(frame, "fractional_cadence") or 0
                records.append({
                    "time": timestamp,
                    "distance": _value(frame, "distance"),
                    "speed": _value(frame, "enhanced_speed", "speed"),
                    "hr": _value(frame, "heart_rate"),
                    "altitude": _value(frame, "enhanced_altitude", "altitude"),
                    "power": _value(frame, "power"),
                    "cadence": cadence,
                })
            elif frame.name == "length" and _value(frame, "length_type") in ("active", 1):
                lengths.append((_value(frame, "start_time"), _value(frame, "total_timer_time", "total_elapsed_time")))
            elif frame.name == "session":
                sessions.append({"start": _value(frame, "start_time"), "elapsed": _value(frame, "total_elapsed_time"),
                                 "pool_length": _value(frame, "pool_length")})
    return {"records": records, "lengths": [(s, t) for s, t in lengths if t], "sessions": sessions}


def _session_window(parsed: dict, start_time: str) -> tuple[dict, list[dict], list]:
    """Session de l'activité (même heure de départ) et ses seuls points / longueurs : un fichier multisport
    contient tout l'enchaînement (natation, vélo, course...)."""
    start = datetime.fromisoformat(start_time)
    session = next((s for s in parsed["sessions"] if s["start"] and s["start"] == start), None)
    if session is None or len(parsed["sessions"]) == 1 or not session["elapsed"]:
        return session or {"pool_length": None}, parsed["records"], parsed["lengths"]
    end = start + timedelta(seconds=session["elapsed"])
    inside = lambda t: t is not None and start <= t <= end
    records = [r for r in parsed["records"] if inside(r["time"])]
    # La distance est cumulée sur tout l'enchaînement : on la remet à zéro au début de la session.
    offset = next((r["distance"] for r in records if r["distance"] is not None), 0)
    if offset:
        records = [{**r, "distance": None if r["distance"] is None else r["distance"] - offset} for r in records]
    return session, records, [(s, t) for s, t in parsed["lengths"] if inside(s)]


def _downsample(records: list[dict], start, fields: list[str]) -> dict[str, list]:
    """Moyenne par paquets : au plus MAX_POINTS points (lisse aussi le bruit du GPS)."""
    size = max(1, -(-len(records) // MAX_POINTS))
    out: dict[str, list] = {"t": [], **{f: [] for f in fields}}
    for i in range(0, len(records), size):
        chunk = records[i:i + size]
        out["t"].append(round((chunk[0]["time"] - start).total_seconds()))
        for f in fields:
            values = [r[f] for r in chunk if r[f] is not None]
            out[f].append(round(sum(values) / len(values), 2) if values else None)
    return out


def _splits(records: list[dict], step_m: int, start) -> list[dict]:
    """Temps (et FC moyenne) de chaque tranche de `step_m` mètres ; la dernière tranche peut être partielle."""
    splits, mark, t0, hr = [], step_m, start, []
    last = None
    for r in records:
        if r["distance"] is None:
            continue
        if r["hr"] is not None:
            hr.append(r["hr"])
        while r["distance"] >= mark:
            if last is None:
                break
            # Interpolation entre les deux points qui encadrent la borne.
            d0, d1 = last["distance"], r["distance"]
            ratio = (mark - d0) / (d1 - d0) if d1 > d0 else 1
            t = last["time"] + (r["time"] - last["time"]) * ratio
            splits.append({"distance_m": step_m, "duration_s": round((t - t0).total_seconds(), 1),
                           "avg_hr": round(sum(hr) / len(hr)) if hr else None})
            mark, t0, hr = mark + step_m, t, []
        last = r
    if last is not None:
        rest = last["distance"] - (mark - step_m)
        if rest >= step_m * 0.1:
            splits.append({"distance_m": round(rest), "duration_s": round((last["time"] - t0).total_seconds(), 1),
                           "avg_hr": round(sum(hr) / len(hr)) if hr else None})
    return splits


def pool_splits(lengths: list, pool_length: float, records: list[dict], step_m: int = 100) -> list[dict]:
    """Tranches de `step_m` mètres en bassin, à partir des longueurs (4 de 25 m, 2 de 50 m...).
    FC moyenne de la tranche d'après les points enregistrés pendant ses longueurs."""
    splits, distance, seconds, windows = [], 0.0, 0.0, []
    for i, (start, duration) in enumerate(lengths):
        distance += pool_length
        seconds += duration
        if start is not None:
            windows.append((start, start + timedelta(seconds=duration)))
        if distance >= step_m - 1e-6 or i == len(lengths) - 1:
            hr = [r["hr"] for r in records if r["hr"] is not None and any(a <= r["time"] <= b for a, b in windows)]
            splits.append({"distance_m": round(distance), "duration_s": round(seconds, 1),
                           "avg_hr": round(sum(hr) / len(hr)) if hr else None})
            distance, seconds, windows = 0.0, 0.0, []
    return splits


def hr_zone_times(records: list[dict], hr_rest: int, hr_max: int) -> list[dict]:
    """Secondes passées dans chaque zone Z1..Z5, avec leurs bornes en bpm."""
    bpm = [round(hr_rest + b * (hr_max - hr_rest)) for b in HR_ZONE_BOUNDS] + [None]
    zones = [{"zone": i + 1, "low": bpm[i] if i else None, "high": bpm[i + 1], "seconds": 0} for i in range(5)]
    for prev, cur in zip(records, records[1:], strict=False):
        if prev["hr"] is None:
            continue
        dt = (cur["time"] - prev["time"]).total_seconds()
        if 0 < dt <= MAX_GAP_S:
            hrr = (prev["hr"] - hr_rest) / (hr_max - hr_rest) if hr_max > hr_rest else 0
            zone = max(i for i, b in enumerate(HR_ZONE_BOUNDS) if hrr >= b or i == 0)
            zones[zone]["seconds"] += dt
    for z in zones:
        z["seconds"] = round(z["seconds"])
    return zones


def detail(activity: dict, category: str, hr_rest: int, hr_max: int) -> dict:
    """Courbes et découpages d'une activité. `streams` vaut None (avec `unavailable`) si le FIT est illisible."""
    if not activity.get("file_path"):
        return {"streams": None, "unavailable": "Pas de fichier FIT pour cette activité"}
    try:
        parsed = parse_streams(activity["file_path"])
    except (FitUnavailable, fitdecode.FitError) as e:
        return {"streams": None, "unavailable": str(e)}
    session, records, lengths = _session_window(parsed, activity["start_time"])
    if not records:
        return {"streams": None, "unavailable": "Aucun point enregistré dans le fichier"}

    start = records[0]["time"]
    fields = [f for f in ("distance", "speed", "hr", "altitude", "power", "cadence")
              if any(r[f] is not None for r in records)]
    streams = _downsample(records, start, fields)
    if "cadence" in streams and category == RUN:
        streams["cadence"] = [round(c * 2) if c is not None else None for c in streams["cadence"]]  # pas/min
    result = {
        "streams": streams,
        "splits": _splits(records, SPLIT_M[category], start) if category in SPLIT_M and "distance" in fields else [],
        "hr_zones": hr_zone_times(records, hr_rest, hr_max) if "hr" in fields else [],
        "lengths": [],
    }
    pool = session["pool_length"]
    if category == SWIM and pool and lengths:
        # En bassin (25 m, 50 m...) : allure par longueur et tranches de 100 m. Sans bassin : eau libre (GPS).
        result["pool_length_m"] = pool
        result["lengths"] = [round(t, 1) for _, t in lengths]
        result["splits"] = pool_splits(lengths, pool, records)
    return result
