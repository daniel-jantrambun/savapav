from yoyo import step

__depends__ = {"0002.user_admin"}

steps = [step("ALTER TABLE activities ADD COLUMN sub_sport TEXT", "ALTER TABLE activities DROP COLUMN sub_sport")]
