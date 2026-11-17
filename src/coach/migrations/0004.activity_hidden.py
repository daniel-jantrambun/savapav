from yoyo import step

__depends__ = {"0003.activity_sub_sport"}

steps = [
    step(
        "ALTER TABLE activities ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE activities DROP COLUMN hidden",
    )
]
