-- unique index on users.api_key (authentification par API key)
-- depends: 20261116_01_V7jiI-add-api-key

DROP INDEX IF EXISTS users_api_key;
