"""Prédiction de performance en course à pied : quel temps sur 5 km, 10 km, semi, marathon aujourd'hui ?

Deux méthodes, comparées sur l'historique de la personne :

- **simple** : le meilleur effort récent (5 km ou plus) extrapolé par Riegel, un peu pénalisé avec son ancienneté
  (la forme se perd sans entraînement). Explicable : « d'après votre 10 km du 3 octobre ».
- **XGBoost** : corrige la méthode simple (rapport prédit / simple) à partir de la charge (CTL/ATL/TSB), du volume,
  de la sortie longue et de l'efficacité cardiaque. Gardé seulement s'il fait mieux que la méthode simple sur des
  efforts qu'il n'a pas vus (validation chronologique : entraîné sur le passé, testé sur les mois suivants).

Cible : les efforts « à fond » de l'historique, repérés parmi les meilleurs efforts (`coach.efforts`) — il y a
peu de courses officielles. Tout est ramené en équivalent 10 km (Riegel) pour comparer des distances différentes.
"""

import bisect
import math
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import cache

from coach.load import daily_loads, fitness_series
from coach.sports import RUN, category

RIEGEL = 1.06
REFERENCE_M = 10000.0
PREDICTED_M = (5000.0, 10000.0, 21097.5, 42195.0)
MIN_EFFORT_M = 5000.0         # en dessous, l'extrapolation par Riegel est trop optimiste
BASELINE_DAYS = 180           # efforts pris en compte par la méthode simple
AGE_PENALTY_PER_DAY = 0.0002  # +0,6 % par mois d'ancienneté de l'effort de référence
HARD_WINDOW_DAYS = 45         # un effort « à fond » : parmi les meilleurs de ± 45 jours...
HARD_MARGIN = 0.02            # ... à 2 % près du meilleur...
HARD_MIN_HRR = 0.75           # ... et, si la FC est connue, à au moins 75 % de la FC de réserve
MIN_TARGETS = 60              # en dessous, pas assez d'exemples pour XGBoost
MIN_GAIN = 0.03               # XGBoost doit réduire l'erreur d'au moins 3 % pour être retenu
FOLDS = (0.6, 0.7, 0.8, 0.9)  # validation : entraîné avant chaque seuil, testé sur les 10 % suivants

# Objectif d'un plan : progression jugée atteignable d'ici la course (1 % par mois, au plus 8 %).
GAIN_PER_MONTH = 0.01
MAX_GAIN = 0.08
CONSERVATIVE_MARGIN = 0.03


def equivalent(duration_s: float, distance_m: float, target_m: float = REFERENCE_M) -> float:
    """Temps équivalent sur `target_m` (formule de Riegel)."""
    return duration_s * (target_m / distance_m) ** RIEGEL


def _day(value: str) -> date:
    return datetime.fromisoformat(value).date()


@dataclass
class Effort:
    activity_id: str
    day: date
    distance_m: float
    duration_s: float
    avg_hr: int | None

    @property
    def eq10(self) -> float:
        return equivalent(self.duration_s, self.distance_m)


def to_efforts(rows: list[dict]) -> list[Effort]:
    return [Effort(r["activity_id"], _day(r["start_time"]), r["distance_m"], r["duration_s"], r["avg_hr"])
            for r in rows]


def baseline(efforts: list[Effort], when: date) -> tuple[float, Effort] | None:
    """Équivalent 10 km prédit à la date `when` (efforts strictement antérieurs) et l'effort qui le fonde."""
    best: tuple[float, Effort] | None = None
    for e in efforts:
        age = (when - e.day).days
        if e.distance_m < MIN_EFFORT_M or not 0 < age <= BASELINE_DAYS:
            continue
        value = e.eq10 * (1 + AGE_PENALTY_PER_DAY * age)
        if best is None or value < best[0]:
            best = (value, e)
    return best


def hard_efforts(efforts: list[Effort], hr_rest: int, hr_max: int) -> list[Effort]:
    """Efforts « à fond » : la cible du modèle. Un footing tranquille ne dit rien de ce qu'on vaut."""
    candidates = sorted((e for e in efforts if e.distance_m >= MIN_EFFORT_M), key=lambda e: e.day)
    days = [e.day.toordinal() for e in candidates]
    hard = []
    for e in candidates:
        if e.avg_hr and hr_max > hr_rest and (e.avg_hr - hr_rest) / (hr_max - hr_rest) < HARD_MIN_HRR:
            continue
        lo = bisect.bisect_left(days, e.day.toordinal() - HARD_WINDOW_DAYS)
        hi = bisect.bisect_right(days, e.day.toordinal() + HARD_WINDOW_DAYS)
        if e.eq10 <= min(o.eq10 for o in candidates[lo:hi]) * (1 + HARD_MARGIN):
            hard.append(e)
    return hard


class History:
    """Activités et charge, pour calculer les variables du modèle à une date donnée (passé strict)."""

    def __init__(self, activities: list[dict], hr_rest: int, hr_max: int, today: date):
        self.hr_rest = hr_rest
        self.runs = sorted(
            ((_day(a["start_time"]), a) for a in activities if category(a["sport"], a.get("sub_sport")) == RUN),
            key=lambda x: x[0],
        )
        self.load = {p.day: p for p in fitness_series(daily_loads(activities, hr_rest, hr_max),
                                                      until=today + timedelta(days=1))}

    def _runs_between(self, start: date, end: date) -> list[dict]:
        return [a for d, a in self.runs if start <= d < end]

    def features(self, when: date, efforts: list[Effort], distance_m: float) -> list[float] | None:
        base = baseline(efforts, when)
        if base is None:
            return None
        value, source = base
        recent = [e.eq10 for e in efforts if e.distance_m >= MIN_EFFORT_M and 0 < (when - e.day).days <= 365]
        point = self.load.get(when - timedelta(days=1))
        weeks4 = self._runs_between(when - timedelta(weeks=4), when)
        weeks12 = self._runs_between(when - timedelta(weeks=12), when)
        weeks8 = self._runs_between(when - timedelta(weeks=8), when)
        # Efficacité : vitesse par battement au-dessus du repos, sur les sorties du dernier mois (médiane).
        efficiency = [a["distance_m"] / a["duration_s"] / (a["avg_hr"] - self.hr_rest)
                      for a in weeks4 if a["distance_m"] and a["avg_hr"] and a["avg_hr"] > self.hr_rest + 10]
        return [
            math.log(value),
            (when - source.day).days,
            math.log(min(recent)) if recent else math.log(value),
            len(recent),
            point.ctl if point else 0.0,
            point.atl if point else 0.0,
            point.tsb if point else 0.0,
            sum(a["distance_m"] or 0 for a in weeks4) / 4000,
            sum(a["distance_m"] or 0 for a in weeks12) / 12000,
            len(weeks4),
            max((a["distance_m"] or 0 for a in weeks8), default=0) / 1000,
            statistics.median(efficiency) * 1000 if efficiency else math.nan,
            distance_m / 1000,
        ]


@cache
def _xgboost():
    """XGBoost s'il est installé et chargeable (sur macOS, il faut libomp : `brew install libomp`)."""
    try:
        import xgboost
    except Exception:  # ImportError, ou XGBoostError si la bibliothèque native ne se charge pas
        return None
    return xgboost


XGB_PARAMS = {
    "objective": "reg:squarederror", "max_depth": 3, "eta": 0.05, "subsample": 0.8, "colsample_bytree": 0.8,
    "min_child_weight": 3, "lambda": 1.0, "nthread": 1, "seed": 0,
}
XGB_ROUNDS = 200


def _fit(xgboost, rows: list[list[float]], targets: list[float]):
    # API native : l'interface « scikit-learn » d'XGBoost exigerait scikit-learn en plus.
    import numpy as np

    data = xgboost.DMatrix(np.array(rows, dtype=float), label=np.array(targets, dtype=float))
    return xgboost.train(XGB_PARAMS, data, num_boost_round=XGB_ROUNDS)


def _predict(model, row: list[float]) -> float:
    import numpy as np

    return float(model.predict(_xgboost().DMatrix(np.array([row], dtype=float)))[0])


@dataclass
class Sample:
    effort: Effort
    features: list[float]

    @property
    def base(self) -> float:
        return math.exp(self.features[0])

    @property
    def residual(self) -> float:
        """Ce que XGBoost apprend : l'écart (log) entre l'effort réel et la méthode simple."""
        return math.log(self.effort.eq10 / self.base)


def _mean_error(pairs: list[tuple[float, float]]) -> float:
    return sum(abs(p - a) / a for p, a in pairs) / len(pairs)


class Predictor:
    """Choisit la méthode (simple ou XGBoost) d'après l'historique, puis prédit à n'importe quelle date."""

    def __init__(self, activities: list[dict], effort_rows: list[dict], hr_rest: int, hr_max: int, today: date):
        self.efforts = to_efforts(effort_rows)
        self.history = History(activities, hr_rest, hr_max, today)
        targets = hard_efforts(self.efforts, hr_rest, hr_max)
        self.samples = [Sample(e, f) for e in targets
                        if (f := self.history.features(e.day, self.efforts, e.distance_m)) is not None]
        self.xgboost_available = _xgboost() is not None
        self.evaluation = self._evaluate()
        self.method = "xgboost" if self.evaluation.get("xgboost_error") is not None and (
            self.evaluation["xgboost_error"] < self.evaluation["baseline_error"] * (1 - MIN_GAIN)) else "baseline"
        self.model = None
        if self.method == "xgboost":
            self.model = _fit(_xgboost(), [s.features for s in self.samples], [s.residual for s in self.samples])

    def _evaluate(self) -> dict:
        n = len(self.samples)
        result: dict = {"targets": n, "tested": 0, "baseline_error": None, "xgboost_error": None}
        if n == 0:
            return result
        # Méthode simple : rien à apprendre, on la mesure sur tous les efforts.
        result["baseline_error"] = round(_mean_error([(s.base, s.effort.eq10) for s in self.samples]), 4)
        xgboost = _xgboost()
        if xgboost is None or n < MIN_TARGETS:
            return result
        ordered = sorted(self.samples, key=lambda s: s.effort.day)
        base_pairs, model_pairs = [], []
        for start, end in zip(FOLDS, (*FOLDS[1:], 1.0), strict=True):
            train, test = ordered[:int(n * start)], ordered[int(n * start):int(n * end)]
            # Pas d'effort de test le même jour qu'un effort d'entraînement (même sortie, autre distance).
            cutoff = train[-1].effort.day
            test = [s for s in test if s.effort.day > cutoff]
            if len(train) < MIN_TARGETS // 2 or not test:
                continue
            model = _fit(xgboost, [s.features for s in train], [s.residual for s in train])
            for s in test:
                base_pairs.append((s.base, s.effort.eq10))
                model_pairs.append((s.base * math.exp(_predict(model, s.features)), s.effort.eq10))
        if model_pairs:
            # Comparaison sur les mêmes efforts de test.
            result["tested"] = len(model_pairs)
            result["baseline_error"] = round(_mean_error(base_pairs), 4)
            result["xgboost_error"] = round(_mean_error(model_pairs), 4)
        return result

    @property
    def error(self) -> float | None:
        key = "xgboost_error" if self.method == "xgboost" else "baseline_error"
        return self.evaluation[key]

    def predict(self, when: date, distance_m: float) -> dict | None:
        """Temps prédit sur `distance_m` le jour `when` (activités de ce jour exclues), avec l'effort de référence."""
        base = baseline(self.efforts, when)
        if base is None:
            return None
        value, source = base
        if self.model is not None:
            features = self.history.features(when, self.efforts, distance_m)
            value *= math.exp(_predict(self.model, features))
        return {
            "distance_m": distance_m,
            "time_s": round(equivalent(value, REFERENCE_M, distance_m)),
            "source": {"activity_id": source.activity_id, "date": str(source.day), "distance_m": source.distance_m,
                       "duration_s": source.duration_s},
        }

    def curve(self, today: date) -> list[dict]:
        """Équivalent 10 km estimé chaque mois (au 1er du mois), du premier effort à aujourd'hui."""
        if not self.efforts:
            return []
        first = self.efforts[0].day
        day, points = date(first.year, first.month, 1), []
        while day <= today:
            prediction = self.predict(day, REFERENCE_M)
            points.append({"day": str(day), "time_s": prediction["time_s"] if prediction else None})
            day = date(day.year + day.month // 12, day.month % 12 + 1, 1)
        tomorrow = today + timedelta(days=1)
        prediction = self.predict(tomorrow, REFERENCE_M)
        points.append({"day": str(today), "time_s": prediction["time_s"] if prediction else None})
        return points


def records(effort_rows: list[dict], today: date) -> list[dict]:
    """Par distance, le record absolu et le meilleur temps des 12 derniers mois."""
    result = []
    efforts = to_efforts(effort_rows)
    for distance in PREDICTED_M:
        at = [e for e in efforts if e.distance_m == distance]
        if not at:
            continue
        year = [e for e in at if (today - e.day).days <= 365]

        def best(es: list[Effort]) -> dict | None:
            if not es:
                return None
            e = min(es, key=lambda x: x.duration_s)
            return {"activity_id": e.activity_id, "date": str(e.day), "duration_s": e.duration_s}

        result.append({"distance_m": distance, "all_time": best(at), "last_year": best(year)})
    return result


def check_goal(predicted_s: float, target_s: float, race_date: date, today: date) -> dict:
    """Objectif d'un plan comparé au temps estimé du jour, en tenant compte du temps de préparation."""
    months = max(0.0, (race_date - today).days / 30.4)
    gain = min(MAX_GAIN, GAIN_PER_MONTH * months)
    reachable_s = predicted_s * (1 - gain)
    if target_s < reachable_s:
        verdict = "ambitious"
    elif target_s > predicted_s * (1 + CONSERVATIVE_MARGIN):
        verdict = "conservative"
    else:
        verdict = "realistic"
    return {"verdict": verdict, "predicted_s": round(predicted_s), "reachable_s": round(reachable_s),
            "gain": round(gain, 3)}
