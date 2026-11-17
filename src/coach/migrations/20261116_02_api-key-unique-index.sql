-- unique index on users.api_key (authentification par API key)
-- depends: 20261116_01_V7jiI-add-api-key

CREATE UNIQUE INDEX IF NOT EXISTS users_api_key ON users (api_key);
