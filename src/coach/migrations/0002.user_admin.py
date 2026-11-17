from yoyo import step

__depends__ = {"0001.initial_schema"}

steps = [
    step(
        "ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users DROP COLUMN is_admin",
    ),
    step("UPDATE users SET is_admin = 1 WHERE id = (SELECT MIN(id) FROM users)"),
]
