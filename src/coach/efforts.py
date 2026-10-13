"""Meilleurs efforts : le segment le plus rapide de 5 km, 10 km, semi et marathon dans chaque course.

Calculés à partir des points GPS du FIT d'origine (la distance cumulée), en temps écoulé : une pause au feu
rouge compte, comme sur une course officielle. Ils servent de records et de cible au modèle de prédiction
de performance (voir `coach.performance`) : il y a peu de courses officielles dans un historique.
"""

import io
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass

import fitdecode

from coach.activity import FitUnavailable, _session_window, parse_fit_streams, read_location
from coach.sports import RUN, category

# Pas de 1 km : trop court pour être fiable (une descente suffit), et Riegel en extrapole des temps trop rapides.
EFFORT_DISTANCES_M = (5000.0, 10000.0, 21097.5, 42195.0)
# À incrémenter quand le calcul change : les activités déjà analysées le sont de nouveau.
# v2 : sauts du GPS sur 30 s (et non 10), sorties trop rapides pour être à pied exclues, plus de 1 km.
EFFORTS_VERSION = 2
# Tapis et courses en salle : la distance vient de l'accéléromètre de la montre, trop imprécise.
INDOOR_SUB_SPORTS = {"treadmill", "indoor_running", "virtual_activity"}
# Sauts du GPS (tunnel, perte de signal) : vitesse moyenne sur GLITCH_WINDOW_S au-delà de GLITCH_SPEED_M_S
# (2:47/km, hors de portée d'un amateur sur 30 s). Le bruit normal fait des pics de quelques secondes (+30 m d'un
# point à l'autre en enregistrement « intelligent », un point toutes les 6 à 8 s), dilués sur 30 s.
GLITCH_WINDOW_S = 30
GLITCH_SPEED_M_S = 6.0
# Vitesse moyenne de toute la sortie au-delà : ce n'est pas de la course à pied (vélo ou voiture mal classés).
MAX_AVG_SPEED_M_S = 6.0
COMMIT_EVERY = 25


@dataclass
class ScanProgress:
    total: int = 0
    done: int = 0
    running: bool = True
    error: str | None = None

    def finish(self, error: str | None = None) -> None:
        self.running, self.error = False, error

    def to_dict(self) -> dict:
        return asdict(self)


def _glitches(points: list[tuple[float, float, float | None]]) -> list[int]:
    """Indices des points atteints à une vitesse impossible sur les GLITCH_WINDOW_S secondes précédentes."""
    flagged, i = [], 0
    for j, (t_j, d_j, _) in enumerate(points):
        while i < j and t_j - points[i][0] > GLITCH_WINDOW_S:
            i += 1
        k = max(i - 1, 0)  # point juste avant la fenêtre : au moins GLITCH_WINDOW_S secondes d'écart
        if j > k and t_j - points[k][0] > 0 and (d_j - points[k][1]) / (t_j - points[k][0]) > GLITCH_SPEED_M_S:
            flagged.append(j)
    return flagged


def best_efforts(records: list[dict], distances: Iterable[float] = EFFORT_DISTANCES_M) -> dict[float, dict]:
    """Par distance, le segment le plus court en temps (`duration_s`, `avg_hr` du segment).
    Le départ du segment est interpolé entre les deux points qui l'encadrent ; un segment ne contient aucun saut
    du GPS (voir `_glitches`)."""
    points: list[tuple[float, float, float | None]] = []
    start = None
    for r in records:
        if r["distance"] is None or r["time"] is None:
            continue
        if points and r["distance"] < points[-1][1]:
            continue  # distance qui recule : point aberrant
        start = start or r["time"]
        points.append(((r["time"] - start).total_seconds(), r["distance"], r["hr"]))
    glitches = _glitches(points)
    result = {}
    for target in distances:
        best: tuple[float, int, int] | None = None
        i, g, last_glitch = 0, 0, -1
        for j in range(len(points)):
            t_j, d_j, _ = points[j]
            while g < len(glitches) and glitches[g] <= j:
                last_glitch, g = glitches[g], g + 1
            if d_j - points[0][1] < target:
                continue
            # Dernier point i tel que le segment [i, j] couvre encore au moins `target` mètres.
            while i + 1 < j and d_j - points[i + 1][1] >= target:
                i += 1
            if i < last_glitch:
                continue  # le segment traverse un saut du GPS
            (t0, d0, _), (t1, d1, _) = points[i], points[i + 1]
            ratio = (d_j - target - d0) / (d1 - d0) if d1 > d0 else 0.0
            duration = t_j - (t0 + (t1 - t0) * ratio)
            if duration > 0 and (best is None or duration < best[0]):
                best = (duration, i, j)
        if best is not None:
            duration, i, j = best
            hr = [p[2] for p in points[i:j + 1] if p[2] is not None]
            result[target] = {"duration_s": round(duration, 1), "avg_hr": round(sum(hr) / len(hr)) if hr else None}
    return result


def is_scannable(activity: dict) -> bool:
    return (category(activity["sport"], activity.get("sub_sport")) == RUN
            and (activity.get("sub_sport") or "") not in INDOOR_SUB_SPORTS
            and bool(activity.get("file_path")) and (activity.get("distance_m") or 0) >= EFFORT_DISTANCES_M[0]
            and activity["distance_m"] / activity["duration_s"] <= MAX_AVG_SPEED_M_S)


def archive_reader() -> Callable[[str], bytes]:
    """Lecture de FIT qui garde ouverte la dernière archive : un export Garmin (zip de ~100 Mo contenant des zip)
    serait sinon relu en entier pour chacune de ses milliers d'activités."""
    opened: dict[str, zipfile.ZipFile] = {}

    def read(location: str) -> bytes:
        *container, name = location.split("!")
        if not container:
            return read_location(location)
        key = "!".join(container)
        if key not in opened:
            opened.clear()
            try:
                opened[key] = zipfile.ZipFile(io.BytesIO(read_location(key)))
            except zipfile.BadZipFile as e:
                raise FitUnavailable(f"Archive illisible ({key})") from e
        try:
            return opened[key].read(name)
        except KeyError as e:
            raise FitUnavailable(f"Fichier FIT introuvable ({name})") from e

    return read


def scan_activity(conn, user_id: int, activity: dict, read: Callable[[str], bytes] = read_location) -> int:
    """Calcule et enregistre les meilleurs efforts d'une course. Renvoie leur nombre (0 si FIT illisible, ou si ce
    n'est pas une course mesurable : ses éventuels efforts d'une version précédente sont alors effacés)."""
    efforts: dict[float, dict] = {}
    try:
        if not is_scannable(activity):
            raise FitUnavailable("pas une course mesurable")
        parsed = parse_fit_streams(read(activity["file_path"]))
        _, records, _ = _session_window(parsed, activity["start_time"])
        efforts = best_efforts(records)
    except (FitUnavailable, fitdecode.FitError):
        pass
    conn.execute("DELETE FROM best_efforts WHERE user_id = ? AND activity_id = ?", (user_id, activity["id"]))
    conn.executemany(
        "INSERT INTO best_efforts (user_id, activity_id, distance_m, duration_s, avg_hr) VALUES (?, ?, ?, ?, ?)",
        [(user_id, activity["id"], d, e["duration_s"], e["avg_hr"]) for d, e in efforts.items()],
    )
    conn.execute("UPDATE activities SET efforts_version = ? WHERE user_id = ? AND id = ?",
                 (EFFORTS_VERSION, user_id, activity["id"]))
    return len(efforts)


def pending(conn, user_id: int) -> list[dict]:
    """Activités dont les meilleurs efforts restent à calculer (nouvelles, ou calculées par une version antérieure).
    Les autres sports en font partie : vite marqués, sans lire de FIT."""
    # Triées par fichier : les activités d'une même archive se suivent (voir archive_reader).
    rows = conn.execute(
        "SELECT * FROM activities WHERE user_id = ? AND (efforts_version IS NULL OR efforts_version < ?)"
        " ORDER BY file_path",
        (user_id, EFFORTS_VERSION),
    )
    return [dict(r) for r in rows]


def reset(conn, user_id: int) -> None:
    """Toutes les activités seront analysées de nouveau (les efforts actuels restent affichés d'ici là)."""
    conn.execute("UPDATE activities SET efforts_version = NULL WHERE user_id = ?", (user_id,))
    conn.commit()


def scan_pending(conn, user_id: int, progress: ScanProgress | None = None) -> int:
    todo = pending(conn, user_id)
    progress = progress or ScanProgress()
    progress.total = len(todo)
    read = archive_reader()
    for n, activity in enumerate(todo, start=1):
        scan_activity(conn, user_id, activity, read)
        progress.done = n
        if n % COMMIT_EVERY == 0:
            conn.commit()
    conn.commit()
    return len(todo)


def efforts(conn, user_id: int, hidden: bool = False) -> list[dict]:
    """Tous les meilleurs efforts de l'utilisateur, du plus ancien au plus récent (activités cachées exclues
    sauf `hidden` : une activité cachée est souvent une erreur de GPS)."""
    rows = conn.execute(
        "SELECT e.activity_id, e.distance_m, e.duration_s, e.avg_hr, a.start_time FROM best_efforts e"
        " JOIN activities a ON a.user_id = e.user_id AND a.id = e.activity_id"
        f" WHERE e.user_id = ?{'' if hidden else ' AND a.hidden = 0'} ORDER BY a.start_time, e.distance_m",
        (user_id,),
    )
    return [dict(r) for r in rows]
