import sqlite3
import threading
from pathlib import Path

from yoyo import get_backend, read_migrations

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_migration_lock = threading.Lock()
_migrated_paths: set[str] = set()


def _apply_migrations(db_path: Path) -> None:
    key = str(db_path.resolve())
    with _migration_lock:
        if key in _migrated_paths:
            return

        migrations = read_migrations(str(MIGRATIONS_DIR))
        legacy_schema = False
        existing_columns: dict[str, set[str]] = {}
        if db_path.exists():
            with sqlite3.connect(db_path) as check:
                has_users = check.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'"
                ).fetchone()
                has_yoyo_history = check.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = '_yoyo_migration'"
                ).fetchone()
                if has_users:
                    existing_columns["users"] = {
                        row[1] for row in check.execute("PRAGMA table_info(users)")
                    }
                has_activities = check.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'activities'"
                ).fetchone()
                if has_activities:
                    existing_columns["activities"] = {
                        row[1] for row in check.execute("PRAGMA table_info(activities)")
                    }
                legacy_schema = bool(
                    has_users
                    and not has_yoyo_history
                    and check.execute("PRAGMA user_version").fetchone()[0] >= 5
                )

        backend = get_backend(f"sqlite:///{db_path.resolve()}")
        with backend.lock():
            if legacy_schema:
                backend.mark_migrations(migrations[:5])
            else:
                # Some older databases have schema changes that were committed before their
                # migration history was recorded. Mark only changes already reflected in SQLite.
                reflected_columns = (
                    (1, "users", "is_admin"),
                    (2, "activities", "sub_sport"),
                    (3, "activities", "hidden"),
                    (4, "activities", "efforts_version"),
                )
                pending = list(backend.to_apply(migrations))
                for index, table, column in reflected_columns:
                    if (
                        index < len(migrations)
                        and column in existing_columns.get(table, set())
                        and migrations[index] in pending
                    ):
                        backend.mark_migrations([migrations[index]])
            backend.apply_migrations(backend.to_apply(migrations))
        _migrated_paths.add(key)


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _apply_migrations(db_path)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


ACTIVITY_COLUMNS = (
    "user_id", "id", "source", "sport", "sub_sport", "start_time", "duration_s", "distance_m",
    "elevation_gain_m", "avg_hr", "max_hr", "avg_power", "file_path",
)


def upsert_activity(conn: sqlite3.Connection, activity: dict) -> None:
    cols = ", ".join(ACTIVITY_COLUMNS)
    params = ", ".join(f":{c}" for c in ACTIVITY_COLUMNS)
    updates = ", ".join(f"{c} = excluded.{c}" for c in ACTIVITY_COLUMNS if c not in ("user_id", "id"))
    row = {c: activity.get(c) for c in ACTIVITY_COLUMNS}
    conn.execute(
        f"INSERT INTO activities ({cols}) VALUES ({params}) ON CONFLICT (user_id, id) DO UPDATE SET {updates}", row
    )
