-- The official PostgreSQL image applies this only to an empty database.
-- psql reads the password from the assigned secret; it is not a command argument.
\set ON_ERROR_STOP on
\set app_password `cat /run/secrets/db_app_password`
CREATE ROLE cctv_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD :'app_password';
\unset app_password
REVOKE CONNECT ON DATABASE cctv FROM PUBLIC;
GRANT CONNECT ON DATABASE cctv TO cctv_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE EXTENSION IF NOT EXISTS vector;
