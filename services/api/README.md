# Authentication (AUTH-01)

Stateless JWT authentication (no sessions or cookies). Users and profiles are
stored **only in TinyDB** (tables `users` and `profiles` in the file selected by
`SUPPLIERS_DB_PATH`); PostgreSQL/Supabase tables store the TinyDB user id as
`user_uuid` and no SQL user table exists.

## Setup

Dependencies are managed with `uv` (`pyproject.toml` / `uv.lock`;
`requirements.txt` is kept for the Docker image):

```bash
cd services/api && uv sync && cp .env.example .env   # then fill in .env
cd ../.. && uv run --project services/api uvicorn services.api.main:app --reload
```

Environment variables (environment takes precedence over `.env`):

- `JWT_SECRET_KEY` (**required**): random secret of at least 32 UTF-8 bytes,
  e.g. `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Never
  commit it; there is no default.
- `JWT_ALGORITHM`: `HS256` (default), `HS384` or `HS512`.
- `ACCESS_TOKEN_EXPIRE_MINUTES`: token lifetime, default 30.
  (`JWT_ACCESS_TOKEN_EXPIRE_MINUTES` is still accepted as a fallback.)

Create the first administrator with the API stopped:
`uv run --project services/api python -m services.api.bootstrap_admin`.

## Data model

- **User** (credentials only): `id`, `email`, `hashed_password` (bcrypt via
  `libpass`), `is_active`, `role` (`admin` | `manager` | `user`), `created_at`,
  plus internal `email_key` and `token_version`.
- **Profile** (one per user, via `user_id`): `id`, `user_id`, `name`, `phone`,
  `address`.

## Routes

| Method | Route | Access |
| --- | --- | --- |
| POST | `/auth/login` | Public. JSON `{"email","password"}` or OAuth2 form (`username` = email) → `{"access_token","token_type":"bearer"}` |
| GET | `/auth/me` | Token. Email, role and linked profile |
| POST | `/auth/forgot-password`, `/auth/reset-password` | Public |
| POST | `/auth/change-password` | Token |
| POST | `/users` | Public registration (role `user` by default; only an admin token may assign `admin`/`manager`). Optional `name`, `phone`, `address` create the linked profile |
| GET | `/users` | Token, admin or manager (403 otherwise) |
| GET | `/users/{id}` | Token, the user themselves, admin or manager (403 otherwise) |
| PUT | `/users/{id}` | Token, the user themselves or admin (403 otherwise); `role`/`is_active` admin only. Users changing their own `email` or `password` must also send `current_password` |
| DELETE | `/users/{id}` | Token, the user themselves or admin (403 otherwise); also deletes the profile |
| GET/PUT | `/profiles/me` | Token, the authenticated user's profile |
| GET | `/profiles`, `/profiles/{id}` | Token; list is admin/manager, detail is owner/admin/manager |
| PUT | `/profiles/{id}` | Token, profile owner only (403 otherwise) |
| * | `/suppliers/**`, `/inventory/**`, `GET /telemetry/session` | Token |

`POST /telemetry/events` stays public (anonymous product analytics).

Missing, malformed, expired or revoked tokens return `401` with
`WWW-Authenticate: Bearer`. Accessing another user's account or profile returns
`403`. Changing email or password, deactivating or deleting a user invalidates
their existing tokens; roles are read from the database on every request. The
last active administrator cannot be demoted, deactivated or deleted.

### Manual check in `/docs`

1. `POST /users` with `{"email": "...", "password": "..."}`.
2. `POST /auth/login` (or the **Authorize** button, using the email as
   username) and copy `access_token`.
3. Call `GET /auth/me` or `GET /suppliers` with the token → 200; without it → 401.

Database read/write failures return a generic `503`; unexpected failures return
a generic `500`. Error logs include only the exception type. Run a single API
worker: TinyDB has no cross-process locking. The database file is created with
mode `0600` because it contains password hashes.

## Password recovery

Configure `RESEND_API_KEY`, `RESEND_FROM_EMAIL` (a verified Resend sender) and
`FRONTEND_BASE_URL` (the backoffice origin; HTTPS except for localhost).
`PASSWORD_RESET_EXPIRE_MINUTES` is optional and defaults to 30. The
email is required for every account.

`POST /auth/forgot-password` accepts `{"email":"..."}` and returns a neutral
message whether or not the account exists. Delivery failures are logged without
the address or token. `POST /auth/reset-password` accepts `token` and
`new_password`; links expire and are single-use. `POST /auth/change-password`
requires a bearer token, `current_password` and `new_password`. A password or
email change invalidates existing access and reset tokens, so users must sign
in again. Apply rate limits to login and password recovery at the deployment
gateway, and avoid logging query strings containing reset tokens.

## Inventory

Inventory uses PostgreSQL through SQLModel; users and authentication remain in
TinyDB and no SQL user table is created. Set `DATABASE_URL` to the PostgreSQL
connection URL for the intended Supabase project. For Supabase, use a TLS
connection such as `?sslmode=require` and never commit the URL. Missing or
unavailable inventory configuration returns `503` on inventory routes without
disabling the existing TinyDB routes.

To initialize and seed **a development database**, install the requirements,
configure `DATABASE_URL`, and run the following from the repository root with
the API stopped:

```bash
python -m services.api.seed_inventory --user-id EXISTING_TINYDB_USER_UUID
```

The command verifies that the user exists in TinyDB, creates missing inventory
tables and inserts the six supplies from the briefing. Its four deliveries and
three consumptions are **synthetic examples**, not actual clinic records.
Repeating it with the same user does not duplicate matching example records;
using another user creates another set of movements. Existing supplies with
conflicting seed fields cause an error rather than being overwritten. The
command is intended for initial development setup; `create_all` does not apply
schema migrations to an existing database.

All `/inventory` routes require a valid bearer token:

- `GET /inventory/products` and `GET /inventory/products/{id}` return supplies
  with computed `current_stock`; `POST /inventory/products` creates a supply.
  SKUs are unique, and duplicates return `409`.
- `POST /inventory/orders/inbound` accepts `supply_id`, `quantity`,
  `vendor_name` and `clinic_id`. `POST /inventory/orders/outbound` accepts
  `supply_id`, `quantity`, `consumption_type` and `clinic_id`. Both derive
  `user_uuid` from the authenticated user rather than accepting it from the
  request.
- `GET /inventory/orders` returns `inbound` and `outbound` lists, each movement
  including its `supply` details. Quantities must be positive 32-bit integers;
  clinic IDs must be between 1 and 12.

Stock is the global sum of deliveries minus consumptions for each supply, not
stored in a column and not tracked separately per clinic. Movement creation
locks the supply row in PostgreSQL so concurrent requests cannot overspend
stock. A consumption exceeding available stock returns `400` without writing.

The backoffice inventory interface is available at `/inventory/products`.
Its low-stock badge is an informational UI threshold of 10 units or less; it
does not change API stock validation. The outbound form shows global stock and
the API remains authoritative if stock changes concurrently.
