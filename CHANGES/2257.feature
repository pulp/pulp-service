Added ``EnvVarHeaderContentGuard`` content guard type that validates a header value
against a server-side environment variable at request time, so secrets can be rotated by
updating the environment without changing guard records in the database.
