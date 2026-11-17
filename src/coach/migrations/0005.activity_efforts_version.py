from yoyo import step

__depends__ = {"0004.activity_hidden"}

steps = [
    step(
        "ALTER TABLE activities ADD COLUMN efforts_version INTEGER",
        "ALTER TABLE activities DROP COLUMN efforts_version",
    )
]
