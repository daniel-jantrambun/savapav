import sqlite3
from pathlib import Path

SCHEMA_VERSION = 4

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    display_name  TEXT NOT NULL,
    hr_rest       INTEGER NOT NULL DEFAULT 50,
    hr_max        INTEGER NOT NULL DEFAULT 185,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- On ne stocke que le hash du jeton de session : une fuite de la base ne permet pas d'usurper une session.
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL
);

-- Jetons Garmin Connect chiffrés (Fernet). Le mot de passe Garmin n'est jamais stocké.
CREATE TABLE IF NOT EXISTS garmin_accounts (
    user_id          INTEGER PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
    tokens_encrypted BLOB NOT NULL,
    connected_at     TEXT NOT NULL DEFAULT (datetime('now')),
    last_sync_at     TEXT,
    last_error       TEXT
);

CREATE TABLE IF NOT EXISTS activities (
    user_id          INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    id               TEXT NOT NULL,      -- "<source>:<id source>"
    source           TEXT NOT NULL,      -- fit | garmin
    sport            TEXT NOT NULL,      -- running, cycling, swimming...
    sub_sport        TEXT,               -- strength_training, indoor_cycling, treadmill...
    start_time       TEXT NOT NULL,      -- ISO 8601 UTC
    duration_s       REAL NOT NULL,
    distance_m       REAL,
    elevation_gain_m REAL,
    avg_hr           REAL,
    max_hr           REAL,
    avg_power        REAL,
    file_path        TEXT,
    hidden           INTEGER NOT NULL DEFAULT 0,  -- masquée des listes et graphes (choix de l'utilisateur)
    PRIMARY KEY (user_id, id)
);
CREATE INDEX IF NOT EXISTS activities_user_start ON activities (user_id, start_time);

CREATE TABLE IF NOT EXISTS plans (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    goal_json  TEXT NOT NULL,
    plan_json  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS plans_user ON plans (user_id);

-- Texte rédigé par le LLM, séparé des chiffres : il ne peut jamais modifier le plan.
CREATE TABLE IF NOT EXISTS plan_texts (
    plan_id    INTEGER NOT NULL REFERENCES plans (id) ON DELETE CASCADE,
    week       INTEGER NOT NULL,
    text       TEXT NOT NULL,
    model      TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (plan_id, week)
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Mises à jour des bases créées par une version précédente (idempotent)."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    if "is_admin" not in columns:
        # v2 : comptes admin. Le plus ancien compte existant devient admin, pour ne pas perdre la main.
        conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE users SET is_admin = 1 WHERE id = (SELECT MIN(id) FROM users)")
        conn.commit()
    columns = {row[1] for row in conn.execute("PRAGMA table_info(activities)")}
    if "sub_sport" not in columns:
        # v3 : sous-sport, pour classer les séances (salle, home trainer...). Rempli au prochain import.
        conn.execute("ALTER TABLE activities ADD COLUMN sub_sport TEXT")
        conn.commit()
    if "hidden" not in columns:
        # v4 : activités masquées. Hors de ACTIVITY_COLUMNS : un réimport ne les fait pas réapparaître.
        conn.execute("ALTER TABLE activities ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")
        conn.commit()


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
