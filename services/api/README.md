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

Database read/write failures return a generic `503` response; unexpected API
failures return a generic `500`. The application error handlers log the error
type without including passwords, tokens, database contents, or exception
messages. Configure server and proxy logging separately to protect sensitive
data. The password recovery request remains neutral even when its lookup or
email delivery fails; check server logs for those failures.

## Password recovery

Configure `RESEND_API_KEY`, `RESEND_FROM_EMAIL` (a verified Resend sender) and
`FRONTEND_BASE_URL` (the backoffice origin; HTTPS except for localhost).
`PASSWORD_RESET_EXPIRE_MINUTES` is optional and defaults to 30. The
administrator can assign a unique `email` when creating or updating users;
existing users without one cannot recover their passwords until it is assigned.
The bootstrap command also accepts an optional administrator email.

`POST /auth/forgot-password` accepts `{"email":"..."}` and returns a neutral
message whether or not the account exists. Delivery failures are logged without
the address or token. `POST /auth/reset-password` accepts `token` and
`new_password`; links expire and are single-use. `POST /auth/change-password`
requires a bearer token, `current_password` and `new_password`. A password or
email change invalidates existing access and reset tokens, so users must sign
in again. Apply rate limits to login and password recovery at the deployment
gateway, and avoid logging query strings containing reset tokens.
