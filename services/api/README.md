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
`GET /me` returns the current user's ID, username and role; authenticated
`GET /auth/me` also returns their email. User-management responses do not
include account emails or password hashes. This changes the previous `/me`
and `/users` response contracts for clients that read email. All six supplier
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
tables, adds the columns introduced for telemetry (`product_category`,
`expiry_date`, `department`) to databases created earlier, and inserts ten
supplies covering the four BRIEFING categories. Its 16 deliveries, 16
consumptions (by department, never by patient) across clinics 1, 2 (US) and
10, 11 (UK), and six per-clinic minimums are **synthetic examples**, not actual
clinic records. They leave two pairs two units above their minimum
(`HCR-WND-001` at clinic 1 and `HCR-MED-001` at clinic 2: an outbound order of
3 units crosses it) and one vaccine expiring in 20 days.
Repeating it with the same user does not duplicate matching example records;
using another user creates another set of movements. Existing supplies with
conflicting seed fields cause an error rather than being overwritten; new
fields are only filled when empty. The command is intended for initial
development setup and only performs these additive schema changes.

All `/inventory` routes require a valid bearer token:

- `GET /inventory/products` and `GET /inventory/products/{id}` return supplies
  with computed `current_stock`; `POST /inventory/products` creates a supply.
  SKUs are unique, and duplicates return `409`.
- `POST /inventory/products` requires `product_category` (`medication`, `ppe`,
  `consumable` or `equipment`) and accepts an optional `expiry_date`; expiry
  lives on the product, not on orders.
- `POST /inventory/orders/inbound` accepts `supply_id`, `quantity`,
  `vendor_name` and `clinic_id`. `POST /inventory/orders/outbound` accepts
  `supply_id`, `quantity`, `consumption_type`, `department`
  (`primary_care`, `general_consultation`, `specialty_care`, `chronic_care`;
  a clinical area, never a patient) and `clinic_id`. Both derive `user_uuid`
  from the authenticated user rather than accepting it from the request.
- The outbound response includes `stock_threshold`: `null`, or
  `{clinic_stock, min_stock_threshold}` when that order took the clinic below
  its configured minimum (`clinic_stock_minimum` table).
- `PUT`/`PATCH /inventory/clinics/{clinic_id}/products/{id}/stock` always
  returns `403`: stock only changes through orders.
- `GET /inventory/expiring?within_days=30` lists, per clinic with positive
  stock, the supplies whose `expiry_date` falls within the window.
- `GET /inventory/orders` returns `inbound` and `outbound` lists, each movement
  including its `supply` details. Quantities must be positive 32-bit integers;
  clinic IDs must be between 1 and 12.

Stock is delivered minus consumed units, not stored in a column. Catalog
responses show the global total; an outbound order is validated against the
stock of its own clinic, and `X-Available-Stock` reports that clinic stock.
Movement creation locks the supply row in PostgreSQL so concurrent requests
cannot overspend stock. A consumption exceeding available stock returns `400`
without writing.

The backoffice inventory interface is available at `/inventory/products`.
Its low-stock badge is an informational UI threshold of 10 units or less; it
does not change API stock validation and is not the per-clinic minimum. The
outbound form shows global stock and the API remains authoritative.
