"""Types de séance (course, vélo, natation, salle, autre) et analyse de l'historique par sport.

Point de départ du planificateur multisport : pas d'objectif chiffré à fixer, la fréquence, le volume et les
allures de référence viennent des activités récentes.
"""

import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta

RUN, BIKE, SWIM, GYM, OTHER = "run", "bike", "swim", "gym", "other"
PLANNED = (RUN, BIKE, SWIM, GYM)  # sports pour lesquels on planifie des séances ; « autre » compte seulement en charge

# Champ `sport` des FIT (en minuscules) → catégorie. Le sous-sport, quand il est connu, est prioritaire.
SPORT_CATEGORY = {
    "running": RUN, "cycling": BIKE, "swimming": SWIM, "training": GYM, "fitness_equipment": GYM, "hiit": GYM,
}
SUB_SPORT_CATEGORY = {
    "treadmill": RUN, "indoor_running": RUN, "indoor_cycling": BIKE,
    "yoga": OTHER, "pilates": OTHER, "breathing": OTHER, "flexibility_training": OTHER,
}

ANALYSIS_WEEKS = 8
TREND_THRESHOLD = 0.15     # ±15 % de volume entre les 4 dernières semaines et les 4 précédentes
THRESHOLD_HRR = 0.85       # seuil ≈ 85 % de la FC de réserve
DEFAULT_THRESHOLD_PACE = 330  # 5:30/km, sans aucune course récente
DEFAULT_SWIM_PACE = 150       # 2:30/100 m, sans aucune séance de natation
HR_ZONES = {"endurance": (0.60, 0.72), "tempo": (0.75, 0.84), "seuil": (0.85, 0.92)}  # en % de FC de réserve


def category(sport: str, sub_sport: str | None = None) -> str:
    by_sub = SUB_SPORT_CATEGORY.get(sub_sport or "")
    return by_sub or SPORT_CATEGORY.get(sport, OTHER)


def _day(activity) -> date:
    return datetime.fromisoformat(activity["start_time"]).date()


def _recent(activities, today: date, weeks: int, cat: str | None = None) -> list:
    start = today - timedelta(weeks=weeks)
    return [
        a for a in activities
        if start <= _day(a) < today and (cat is None or category(a["sport"], a.get("sub_sport")) == cat)
    ]


def _weekly(activities, today: date, weeks: int) -> list[tuple[int, float, float]]:
    """(séances, minutes, km) par semaine glissante, de la plus ancienne à la plus récente."""
    start = today - timedelta(weeks=weeks)
    out = [[0, 0.0, 0.0] for _ in range(weeks)]
    for a in activities:
        w = out[(_day(a) - start).days // 7]
        w[0] += 1
        w[1] += a["duration_s"] / 60
        w[2] += (a["distance_m"] or 0) / 1000
    return [tuple(w) for w in out]


def baseline_minutes(weekly: list[tuple[int, float, float]]) -> int:
    """Volume de référence : moyenne des 3 plus grosses des 4 dernières semaines (ignore une semaine de décharge)."""
    best = sorted((w[1] for w in weekly[-4:]), reverse=True)[:3]
    return round(sum(best) / 3)


def hr_zones(hr_rest: int, hr_max: int) -> dict[str, list[int]]:
    """Zones de FC (méthode de Karvonen), en bpm."""
    bpm = lambda pct: round(hr_rest + pct * (hr_max - hr_rest))
    return {name: [bpm(lo), bpm(hi)] for name, (lo, hi) in HR_ZONES.items()}


def run_paces(runs, hr_rest: int, hr_max: int) -> tuple[dict[str, int], str]:
    """Allures (s/km) dérivées des sorties récentes, et leur source : fc | allure_moyenne | defaut.

    Avec la FC : régression vitesse ~ % de FC de réserve, lue au seuil. Sinon : la sortie médiane est
    considérée comme un footing, le seuil est ~18 % plus rapide.
    """
    samples = []
    for a in runs:
        if not a["distance_m"] or a["distance_m"] < 2000 or a["duration_s"] < 600:
            continue
        speed = a["distance_m"] / a["duration_s"]
        if 1000 / (12 * 60) <= speed <= 1000 / (2.5 * 60):  # écarte la marche et les erreurs GPS
            samples.append((speed, a["avg_hr"]))

    threshold, source = 1000 / DEFAULT_THRESHOLD_PACE, "defaut"
    if samples:
        median = statistics.median(s for s, _ in samples)
        threshold, source = median * 1.18, "allure_moyenne"
        with_hr = [(s, (hr - hr_rest) / (hr_max - hr_rest)) for s, hr in samples if hr and hr_max > hr_rest]
        if len(with_hr) >= 4 and statistics.pvariance([x for _, x in with_hr]) > 1e-4:
            slope, intercept = statistics.linear_regression([x for _, x in with_hr], [s for s, _ in with_hr])
            estimate = intercept + slope * THRESHOLD_HRR
            if slope > 0 and median * 1.02 <= estimate <= median * 1.35:
                threshold, source = estimate, "fc"

    t = 1000 / threshold
    return {"easy": round(t * 1.22), "long": round(t * 1.17), "tempo": round(t), "intervals": round(t * 0.95)}, source


def swim_pace(swims) -> tuple[int, str]:
    """Allure habituelle en natation (s/100 m, repos compris), et sa source : allure_moyenne | defaut."""
    paces = [a["duration_s"] / (a["distance_m"] / 100) for a in swims if a["distance_m"] and a["distance_m"] >= 200]
    if not paces:
        return DEFAULT_SWIM_PACE, "defaut"
    return round(min(max(statistics.median(paces), 60), 240)), "allure_moyenne"


def analyze(activities, today: date, hr_rest: int, hr_max: int, weeks: int = ANALYSIS_WEEKS) -> dict:
    """Profil des `weeks` dernières semaines : par sport planifiable, les autres activités, les références."""
    sports = {}
    for cat in PLANNED:
        recent = _recent(activities, today, weeks, cat)
        if not recent:
            continue
        weekly = _weekly(recent, today, weeks)
        last4, before = weekly[-4:], weekly[:-4]
        minutes_last4 = sum(w[1] for w in last4) / 4
        minutes_before = sum(w[1] for w in before) / len(before) if before else 0
        if minutes_before == 0:
            trend = "nouveau" if minutes_last4 else "stable"
        elif minutes_last4 > minutes_before * (1 + TREND_THRESHOLD):
            trend = "hausse"
        elif minutes_last4 < minutes_before * (1 - TREND_THRESHOLD):
            trend = "baisse"
        else:
            trend = "stable"
        sports[cat] = {
            "sessions": len(recent),
            "sessions_per_week": round(len(recent) / weeks, 1),
            "recent_sessions_per_week": round(sum(w[0] for w in last4) / 4, 1),
            "minutes_per_week": round(sum(w[1] for w in weekly) / weeks),
            "km_per_week": round(sum(w[2] for w in weekly) / weeks, 1) if cat != GYM else None,
            "baseline_minutes": baseline_minutes(weekly),
            "longest_minutes": round(max(a["duration_s"] for a in recent) / 60),
            "trend": trend,
            "last_date": str(max(_day(a) for a in recent)),
        }

    others: dict[str, list[float]] = defaultdict(lambda: [0, 0.0])
    for a in _recent(activities, today, weeks, OTHER):
        others[a["sport"]][0] += 1
        others[a["sport"]][1] += a["duration_s"] / 60
    paces, pace_source = run_paces(_recent(activities, today, weeks, RUN), hr_rest, hr_max)
    swim, swim_source = swim_pace(_recent(activities, today, weeks, SWIM))
    return {
        "weeks": weeks,
        "sports": sports,
        "others": sorted(
            ({"sport": s, "sessions": int(n), "minutes": round(m)} for s, (n, m) in others.items()),
            key=lambda o: -o["minutes"],
        ),
        "references": {
            "run_paces": paces,
            "run_paces_source": pace_source,
            "swim_pace_s_per_100m": swim,
            "swim_pace_source": swim_source,
            "hr_zones": hr_zones(hr_rest, hr_max),
        },
    }


# Au-delà, les statistiques sont regroupées par mois : une barre par semaine ne serait plus lisible.
MONTHLY_ABOVE_WEEKS = 260


def stats_start(today: date, weeks: int) -> date:
    """Lundi de la première des `weeks` semaines calendaires couvertes par les statistiques ; 1er du mois de ce
    lundi au-delà de `MONTHLY_ABOVE_WEEKS`, pour que le premier mois soit complet."""
    monday = today - timedelta(days=today.weekday(), weeks=weeks - 1)
    return monday.replace(day=1) if weeks > MONTHLY_ABOVE_WEEKS else monday


def _months_between(start: date, day: date) -> int:
    return (day.year - start.year) * 12 + day.month - start.month


def _add_months(start: date, months: int) -> date:
    year, month = divmod(start.month - 1 + months, 12)
    return start.replace(year=start.year + year, month=month + 1)


def weeks_since(first: date, today: date) -> int:
    """Semaines calendaires de celle de `first` à celle de `today`, toutes deux comprises."""
    return max(1, ((today - first).days + first.weekday() - today.weekday()) // 7 + 1)


def category_stats(activities, today: date, weeks: int, first_days: dict[str, date] | None = None) -> dict[str, dict]:
    """Par type d'activité, semaines calendaires (lundi) des `weeks` dernières semaines, semaine en cours comprise —
    ou mois calendaires au-delà de `MONTHLY_ABOVE_WEEKS` (`bucket`) : séances, minutes, km, allure/vitesse moyenne
    (temps total / distance totale), FC moyenne pondérée par la durée.

    `first_days` : date de la toute première activité de chaque type. Ses graphes démarrent alors à cette date
    plutôt que sur des semaines vides, et le passage aux mois se décide sur cette durée raccourcie."""
    first_days = first_days or {}
    by_cat: dict[str, list] = {}
    for a in activities:
        by_cat.setdefault(category(a["sport"], a.get("sub_sport")), []).append(a)

    def summary(w: dict) -> dict:
        return {
            "sessions": w["sessions"],
            "minutes": round(w["seconds"] / 60),
            "km": round(w["meters"] / 1000, 1),
            "speed_m_s": round(w["meters"] / w["timed_meters"], 3) if w["meters"] else None,
            "avg_hr": round(w["hr_sum"] / w["hr_seconds"]) if w["hr_seconds"] else None,
        }

    result = {}
    for cat, acts in by_cat.items():
        span = min(weeks, weeks_since(first_days[cat], today)) if cat in first_days else weeks
        monthly = span > MONTHLY_ABOVE_WEEKS
        start = stats_start(today, span)
        weekly = [
            {"sessions": 0, "seconds": 0.0, "meters": 0.0, "timed_meters": 0.0, "hr_seconds": 0.0, "hr_sum": 0.0}
            for _ in range(_months_between(start, today) + 1 if monthly else span)
        ]
        for a in acts:
            day = _day(a)
            if not start <= day <= today:
                continue
            w = weekly[_months_between(start, day) if monthly else (day - start).days // 7]
            w["sessions"] += 1
            w["seconds"] += a["duration_s"]
            if a["distance_m"]:
                w["meters"] += a["distance_m"]
                w["timed_meters"] += a["duration_s"]  # temps des seules séances avec distance, pour l'allure
            if a["avg_hr"]:
                w["hr_seconds"] += a["duration_s"]
                w["hr_sum"] += a["avg_hr"] * a["duration_s"]
        total = {k: sum(w[k] for w in weekly) for k in weekly[0]}
        if not total["sessions"]:
            continue
        result[cat] = {
            "bucket": "month" if monthly else "week",
            # Clé « weeks » conservée en mode mensuel : chaque entrée est alors un mois.
            "weeks": [
                {"start": str(_add_months(start, i) if monthly else start + timedelta(weeks=i)), **summary(w)}
                for i, w in enumerate(weekly)
            ],
            "total": summary(total),
        }
    return result
