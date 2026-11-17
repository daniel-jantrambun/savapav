from yoyo import step

steps = [
    step(
        """CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            display_name TEXT NOT NULL,
            hr_rest INTEGER NOT NULL DEFAULT 50,
            hr_max INTEGER NOT NULL DEFAULT 185,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
        "DROP TABLE users",
    ),
    step(
        """CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            expires_at TEXT NOT NULL
        )""",
        "DROP TABLE sessions",
    ),
    step(
        """CREATE TABLE IF NOT EXISTS garmin_accounts (
            user_id INTEGER PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
            tokens_encrypted BLOB NOT NULL,
            connected_at TEXT NOT NULL DEFAULT (datetime('now')),
            last_sync_at TEXT,
            last_error TEXT
        )""",
        "DROP TABLE garmin_accounts",
    ),
    step(
        """CREATE TABLE IF NOT EXISTS activities (
            user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            id TEXT NOT NULL,
            source TEXT NOT NULL,
            sport TEXT NOT NULL,
            start_time TEXT NOT NULL,
            duration_s REAL NOT NULL,
            distance_m REAL,
            elevation_gain_m REAL,
            avg_hr REAL,
            max_hr REAL,
            avg_power REAL,
            file_path TEXT,
            PRIMARY KEY (user_id, id)
        )""",
        "DROP TABLE activities",
    ),
    step(
        "CREATE INDEX IF NOT EXISTS activities_user_start ON activities (user_id, start_time)",
        "DROP INDEX activities_user_start",
    ),
    step(
        """CREATE TABLE IF NOT EXISTS best_efforts (
            user_id INTEGER NOT NULL,
            activity_id TEXT NOT NULL,
            distance_m REAL NOT NULL,
            duration_s REAL NOT NULL,
            avg_hr INTEGER,
            PRIMARY KEY (user_id, activity_id, distance_m),
            FOREIGN KEY (user_id, activity_id) REFERENCES activities (user_id, id) ON DELETE CASCADE
        )""",
        "DROP TABLE best_efforts",
    ),
    step(
        """CREATE TABLE IF NOT EXISTS plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            goal_json TEXT NOT NULL,
            plan_json TEXT NOT NULL
        )""",
        "DROP TABLE plans",
    ),
    step(
        "CREATE INDEX IF NOT EXISTS plans_user ON plans (user_id)",
        "DROP INDEX plans_user",
    ),
    step(
        """CREATE TABLE IF NOT EXISTS plan_texts (
            plan_id INTEGER NOT NULL REFERENCES plans (id) ON DELETE CASCADE,
            week INTEGER NOT NULL,
            text TEXT NOT NULL,
            model TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (plan_id, week)
        )""",
        "DROP TABLE plan_texts",
    ),
]
