"""Connexion Garmin Connect par utilisateur (bibliothèque non officielle `garminconnect` ≥ 0.3.5).

Le mot de passe Garmin ne sert qu'une fois, à la connexion : il est échangé contre des jetons, qui sont
chiffrés en base. Seuls les jetons servent ensuite aux synchros. Les FIT originaux sont rangés dans
`<fit_dir>/<user_id>/garmin/` puis importés comme n'importe quel FIT.
"""

import base64
import contextlib
import hashlib
import logging
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet
from garminconnect import Garmin
from garminconnect.exceptions import GarminConnectTooManyRequestsError
from opentelemetry import trace

from coach.ingest.fit_files import import_directory
from coach.telemetry import tracer

MFA_TTL_S = 10 * 60
PAGE_SIZE = 100
FULL_SYNC_DELAY_S = 1.5  # pause entre deux téléchargements lors d'une synchro complète
# Connexions en attente du code MFA : l'état vit dans l'instance Garmin, donc en mémoire (un seul process).
_pending_mfa: dict[int, tuple[Garmin, float]] = {}


class GarminError(Exception):
    pass


@dataclass
class SyncProgress:
    """Avancement d'une synchro (lu par l'interface pendant une synchro complète en tâche de fond)."""

    phase: str = "connexion"  # connexion | liste | telechargement | import | termine | erreur
    found: int = 0            # activités listées chez Garmin
    to_download: int = 0      # dont pas encore téléchargées
    downloaded: int = 0
    imported: int = 0
    error: str | None = None
    started_at: str = ""
    finished_at: str | None = None

    def __post_init__(self):
        self.started_at = self.started_at or datetime.now(UTC).isoformat()

    @property
    def running(self) -> bool:
        return self.phase not in ("termine", "erreur")

    def finish(self, error: str | None = None) -> None:
        self.phase, self.error = ("erreur", error) if error else ("termine", None)
        self.finished_at = datetime.now(UTC).isoformat()

    def to_dict(self) -> dict:
        return {**asdict(self), "running": self.running}


# Serveurs contactés pendant la connexion et la synchro (vérifiés par `coach garmin-check`).
GARMIN_HOSTS = ["sso.garmin.com", "diauth.garmin.com", "services.garmin.com", "connect.garmin.com",
                "connectapi.garmin.com"]


class _WarningCollector(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@contextlib.contextmanager
def _diagnostics():
    """Collecte les avertissements de garminconnect (une ligne par méthode de connexion tentée).

    La bibliothèque n'expose que la dernière erreur ; la vraie cause est souvent dans ces avertissements.
    Elle nettoie elle-même ces messages (pas de jeton ni de mot de passe).
    """
    collector = _WarningCollector()
    logger = logging.getLogger("garminconnect")
    logger.addHandler(collector)
    try:
        yield collector.messages
    finally:
        logger.removeHandler(collector)


def _error(prefix: str, e: Exception, warnings: list[str]) -> GarminError:
    details = " | ".join(dict.fromkeys(warnings))  # dédoublonné, ordre conservé
    return GarminError(f"{prefix} : {e}" + (f" (détails : {details})" if details else ""))


def _fernet(secret_key: str | None) -> Fernet:
    if not secret_key:
        raise GarminError("COACH_SECRET_KEY n'est pas configurée : impossible de stocker les jetons Garmin")
    # N'importe quelle phrase secrète fait l'affaire : on en dérive une clé Fernet.
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret_key.encode()).digest()))


def _save_tokens(conn, user_id: int, client: Garmin, secret_key: str) -> None:
    encrypted = _fernet(secret_key).encrypt(client.client.dumps().encode())
    conn.execute(
        "INSERT INTO garmin_accounts (user_id, tokens_encrypted) VALUES (?, ?) "
        "ON CONFLICT (user_id) DO UPDATE SET tokens_encrypted = excluded.tokens_encrypted, last_error = NULL",
        (user_id, encrypted),
    )
    conn.commit()


def start_login(conn, user_id: int, email: str, password: str, secret_key: str | None) -> str:
    """Lance la connexion. Renvoie "connected" ou "needs_mfa" (il faut alors appeler `complete_mfa`)."""
    _fernet(secret_key)  # échoue tôt si la clé manque, avant d'envoyer quoi que ce soit à Garmin
    client = Garmin(email, password, return_on_mfa=True)
    with _diagnostics() as warnings:
        try:
            status, _ = client.login()
        except Exception as e:
            raise _error("Connexion Garmin refusée", e, warnings) from e
    if status == "needs_mfa":
        _pending_mfa[user_id] = (client, time.monotonic())
        return "needs_mfa"
    _save_tokens(conn, user_id, client, secret_key)
    return "connected"


def complete_mfa(conn, user_id: int, code: str, secret_key: str | None) -> None:
    client, started = _pending_mfa.get(user_id, (None, 0.0))
    if client is None or time.monotonic() - started > MFA_TTL_S:
        _pending_mfa.pop(user_id, None)
        raise GarminError("Aucune connexion Garmin en attente (ou délai dépassé) : recommencez")
    with _diagnostics() as warnings:
        try:
            client.resume_login(None, code)
        except Exception as e:
            # Un code faux laisse la connexion en attente : on peut réessayer.
            raise _error("Code MFA refusé", e, warnings) from e
    _pending_mfa.pop(user_id, None)
    _save_tokens(conn, user_id, client, secret_key)


def disconnect(conn, user_id: int) -> None:
    conn.execute("DELETE FROM garmin_accounts WHERE user_id = ?", (user_id,))
    conn.commit()


def status(conn, user_id: int) -> dict:
    row = conn.execute(
        "SELECT connected_at, last_sync_at, last_error FROM garmin_accounts WHERE user_id = ?", (user_id,)
    ).fetchone()
    return {"connected": row is not None, **(dict(row) if row else {})}


@tracer.start_as_current_span("garmin.sync")
def sync(conn, user_id: int, fit_dir: Path, secret_key: str | None, limit: int = 50, full: bool = False,
         progress: SyncProgress | None = None, delay_s: float | None = None) -> tuple[int, int]:
    """Télécharge les `limit` dernières activités (ou tout l'historique si `full`) et importe les nouvelles.
    Renvoie (importées, ignorées). `progress` est mis à jour au fil de l'eau (synchro complète en tâche de fond)."""
    span = trace.get_current_span()
    span.set_attributes({"user.id": user_id, "garmin.full": full, "garmin.limit": limit})
    progress = progress or SyncProgress()
    row = conn.execute("SELECT tokens_encrypted FROM garmin_accounts WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        raise GarminError("Compte Garmin non connecté")
    fernet = _fernet(secret_key)
    with _diagnostics() as warnings:
        try:
            result = _sync(conn, user_id, fit_dir, secret_key, limit, fernet, row, full, progress,
                           (FULL_SYNC_DELAY_S if delay_s is None else delay_s) if full else 0)
            progress.finish()
            span.set_attributes({"garmin.found": progress.found, "garmin.downloaded": progress.downloaded,
                                 "activities.imported": result[0], "activities.skipped": result[1]})
            return result
        except GarminError as e:
            progress.finish(str(e))
            conn.execute("UPDATE garmin_accounts SET last_error = ? WHERE user_id = ?", (str(e), user_id))
            conn.commit()
            raise
        except Exception as e:
            error = _error("Synchro Garmin échouée", e, warnings)
            progress.finish(str(error))
            conn.execute("UPDATE garmin_accounts SET last_error = ? WHERE user_id = ?", (str(error), user_id))
            conn.commit()
            raise error from e


def _list_all(client, progress: SyncProgress) -> list[dict]:
    """Tout l'historique Garmin, page par page (la bibliothèque plafonne à 1 000 par appel)."""
    activities: list[dict] = []
    while True:
        page = client.get_activities(len(activities), PAGE_SIZE)
        if not page:
            return activities
        activities += page
        progress.found = len(activities)
        if len(page) < PAGE_SIZE:
            return activities


def _download(client, target: Path, activities: list[dict], progress: SyncProgress, delay_s: float) -> None:
    missing = [a for a in activities if not (target / f"{a['activityId']}.zip").exists()]
    progress.to_download = len(missing)
    progress.phase = "telechargement"
    for i, activity in enumerate(missing):
        if i and delay_s:
            time.sleep(delay_s)  # rythme modéré : Garmin bloque les rafales de téléchargements
        data = client.download_activity(activity["activityId"], dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL)
        # Écriture atomique : un téléchargement interrompu ne laisse pas de fichier tronqué (pris pour fait).
        path = target / f"{activity['activityId']}.zip"
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(path)
        progress.downloaded += 1


def _sync(conn, user_id, fit_dir, secret_key, limit, fernet, row, full, progress, delay_s) -> tuple[int, int]:
    client = Garmin()
    client.login(tokenstore=fernet.decrypt(row["tokens_encrypted"]).decode())
    target = fit_dir / str(user_id) / "garmin"
    target.mkdir(parents=True, exist_ok=True)
    failure: Exception | None = None
    try:
        progress.phase = "liste"
        activities = _list_all(client, progress) if full else client.get_activities(0, limit)
        progress.found = len(activities)
        _download(client, target, activities, progress, delay_s)
    except Exception as e:  # on importe quand même ce qui a été téléchargé avant l'erreur
        failure = e
    # Les jetons ont pu être rafraîchis pendant la synchro : on garde la dernière version.
    _save_tokens(conn, user_id, client, secret_key)
    progress.phase = "import"
    imported, skipped = import_directory(conn, user_id, target, only_new=True)
    progress.imported = imported
    if isinstance(failure, GarminConnectTooManyRequestsError):
        raise GarminError(
            f"Garmin limite les requêtes : {progress.downloaded} activité(s) téléchargée(s) sur "
            f"{progress.to_download}. Relancez dans quelques heures, la synchro reprendra où elle s'est arrêtée."
        ) from failure
    if failure is not None:
        raise failure
    conn.execute(
        "UPDATE garmin_accounts SET last_sync_at = ?, last_error = NULL WHERE user_id = ?",
        (datetime.now(UTC).isoformat(), user_id),
    )
    conn.commit()
    return imported, skipped


def check_connectivity(timeout_s: float = 10) -> list[tuple[str, str]]:
    """Teste l'accès HTTPS aux serveurs Garmin depuis cette machine (ou ce conteneur)."""
    import httpx

    results = []
    for host in GARMIN_HOSTS:
        try:
            r = httpx.get(f"https://{host}/", timeout=timeout_s, follow_redirects=False)
            results.append((host, f"OK (HTTP {r.status_code})"))
        except Exception as e:
            results.append((host, f"ÉCHEC : {type(e).__name__}: {e}"))
    return results
