-- add api key
-- depends: 0005.activity_efforts_version

ALTER TABLE users ADD COLUMN api_key TEXT;
