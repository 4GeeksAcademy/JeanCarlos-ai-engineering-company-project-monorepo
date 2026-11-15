# Authentication

Install `requirements.txt` and configure these environment variables, either
directly or in a `.env` file in this directory:

- `JWT_SECRET_KEY`: a random secret of at least 32 UTF-8 bytes. Generate one
  with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
- `JWT_ALGORITHM`: `HS256`, `HS384`, or `HS512`.
- `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`: a positive integer.

Existing environment variables take precedence over `.env`; restart the API
after changing the configuration. Never commit the secret.

With the API stopped, create the initial administrator from the repository root:

```bash
python -m services.api.bootstrap_admin
```

The command prompts for credentials without displaying the password and refuses
to run if any users already exist. Users and profiles are stored in named TinyDB
tables in the same file selected by `SUPPLIERS_DB_PATH`. As with the supplier
seed, run this command with the API stopped and use only one API worker: TinyDB
does not provide cross-process locking. The database file is restricted to its
owner (mode `0600`) because it contains password hashes.

`POST /login` accepts OAuth2 form fields `username` and `password`; send the
returned access token as `Authorization: Bearer <token>` to protected routes.
`GET /me` returns the current user without the password hash. All six supplier
routes require authentication. Administrators manage users; administrators and
managers manage profiles. Users can read and update only their own profile.
A profile contains only its ID and owner ID; a user cannot change its owner.
Administrators and managers may reassign profiles to users without a profile.
Deleting a user also deletes their profile. The last administrator cannot be
removed or demoted.

Tokens expire according to `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`. Password changes
invalidate existing tokens; user deletion also invalidates them. Role checks use
the current database value on every request.
