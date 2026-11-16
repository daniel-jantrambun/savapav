"""Import de fichiers FIT : export complet Garmin ("Exporter vos données"), garminexport ou sync Garmin.

On ne garde que le résumé de chaque activité (message `session`) ; les streams seconde par seconde
restent dans les fichiers bruts et seront exploités plus tard (dérive FC/allure, détection de fatigue).
"""

import hashlib
import io
import zipfile
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import fitdecode
from opentelemetry import trace

from coach.telemetry import tracer

COMMIT_EVERY = 100  # fichiers : un gros export ne verrouille pas la base pendant tout l'import


@dataclass
class ImportProgress:
    """Avancement d'un import de fichier (lu par l'interface pendant l'import en tâche de fond)."""

    files: int = 0     # fichiers FIT lus
    imported: int = 0  # activités importées
    skipped: int = 0   # fichiers sans activité exploitable
    running: bool = True
    error: str | None = None
    started_at: str = ""
    finished_at: str | None = None

    def __post_init__(self):
        self.started_at = self.started_at or datetime.now(UTC).isoformat()

    def finish(self, error: str | None = None) -> None:
        self.running, self.error = False, error
        self.finished_at = datetime.now(UTC).isoformat()

    def to_dict(self) -> dict:
        return asdict(self)


def parse_fit(data: bytes) -> list[tuple[int, dict]]:
    """Résumé de chaque session d'un fichier FIT, avec son rang dans le fichier. Un fichier multisport
    (triathlon...) en contient plusieurs ; les transitions sont ignorées."""
    sessions = []
    with fitdecode.FitReader(io.BytesIO(data), check_crc=fitdecode.CrcCheck.DISABLED) as fit:
        index = 0
        for frame in fit:
            if frame.frame_type == fitdecode.FIT_FRAME_DATA and frame.name == "session":
                activity = _session_to_activity(frame)
                if activity is not None and activity["sport"] != "transition":
                    sessions.append((index, activity))
                index += 1
    return sessions


def _field(frame, name):
    return frame.get_value(name, fallback=None) if frame.has_field(name) else None


def _session_to_activity(frame) -> dict | None:
    start = _field(frame, "start_time")
    duration = _field(frame, "total_timer_time") or _field(frame, "total_elapsed_time")
    if not isinstance(start, datetime) or not duration:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    sport = _field(frame, "sport")
    sub_sport = _field(frame, "sub_sport")
    return {
        "sport": str(sport).lower() if sport is not None else "generic",
        "sub_sport": str(sub_sport).lower() if sub_sport not in (None, "generic") else None,
        "start_time": start.astimezone(UTC).isoformat(),
        "duration_s": float(duration),
        "distance_m": _field(frame, "total_distance"),
        "elevation_gain_m": _field(frame, "total_ascent"),
        "avg_hr": _field(frame, "avg_heart_rate"),
        "max_hr": _field(frame, "max_heart_rate"),
        "avg_power": _field(frame, "avg_power"),
    }


def _iter_zip(archive: zipfile.ZipFile, location: str) -> Iterator[tuple[str, bytes]]:
    # L'export complet Garmin contient des zip dans le zip (DI-Connect-Uploaded-Files/UploadedFiles_*.zip).
    for name in archive.namelist():
        lower = name.lower()
        if lower.endswith(".fit"):
            yield f"{location}!{name}", archive.read(name)
        elif lower.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(archive.read(name))) as inner:
                yield from _iter_zip(inner, f"{location}!{name}")


def iter_fit_payloads(root: Path, skip: frozenset[str] = frozenset()) -> Iterator[tuple[str, bytes]]:
    """Parcourt un dossier et renvoie (chemin, contenu) pour chaque .fit, y compris dans les .zip.
    Les fichiers dont le chemin est dans `skip` ne sont pas lus."""
    for path in sorted(root.rglob("*")):
        if str(path) in skip:
            continue
        suffix = path.suffix.lower()
        if suffix == ".fit":
            yield str(path), path.read_bytes()
        elif suffix == ".zip":
            with zipfile.ZipFile(path) as archive:
                yield from _iter_zip(archive, str(path))


@tracer.start_as_current_span("fit.import")
def import_directory(conn, user_id: int, root: Path, only_new: bool = False,
                     progress: ImportProgress | None = None) -> tuple[int, int]:
    """Importe tous les FIT d'un dossier pour un utilisateur. Renvoie (importés, ignorés).
    `progress` est mis à jour au fil de l'eau (import en tâche de fond).

    `only_new` : ne relit pas les fichiers dont une activité est déjà en base (synchro Garmin : sans ça,
    chaque synchro relirait tout l'historique)."""
    from coach.db import upsert_activity

    known: frozenset[str] = frozenset()
    if only_new:
        known = frozenset(
            row[0].split("!")[0]
            for row in conn.execute(
                "SELECT file_path FROM activities WHERE user_id = ? AND file_path IS NOT NULL", (user_id,)
            )
        )
    progress = progress or ImportProgress()
    imported = skipped = 0
    for location, payload in iter_fit_payloads(root, known):
        progress.files += 1
        if progress.files % COMMIT_EVERY == 0:
            conn.commit()
        try:
            sessions = parse_fit(payload)
        except fitdecode.FitError:
            sessions = []
        if not sessions:
            skipped += 1
            progress.skipped = skipped
            continue
        # Identifiant stable basé sur le contenu : réimporter le même fichier ne crée pas de doublon.
        # La 1re session garde l'identifiant historique, les suivantes (multisport) prennent leur rang.
        base_id = "fit:" + hashlib.sha1(payload).hexdigest()[:16]
        for index, activity in sessions:
            activity["id"] = base_id if index == 0 else f"{base_id}:{index}"
            activity["user_id"] = user_id
            activity["source"] = "fit"
            activity["file_path"] = location
            upsert_activity(conn, activity)
            imported += 1
        progress.imported = imported
    conn.commit()
    trace.get_current_span().set_attributes({"user.id": user_id, "fit.only_new": only_new, "fit.files": progress.files,
                                             "activities.imported": imported, "activities.skipped": skipped})
    return imported, skipped
