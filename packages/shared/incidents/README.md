# `packages/shared/incidents`

Shared HealthCore incident rules, used **without duplication** by:

- `services/api` — `/api/incidents` validation, lifecycle and DB constraints
- `scripts/seed_incidents.py` — legacy CSV validation and transformation

| File | Contents |
| --- | --- |
| `catalog.json` | Single source of truth: branches (with labels), categories, statuses, origins, lifecycle transitions, limits and the legacy CSV maps from the BRIEFING |
| `catalog.py` | Loads `catalog.json` into Python constants |
| `validation.py` | `validate_incident`, `validate_filter`, `transition_error` (API) and `csv_row_errors` (the incidents-file-analyzer rules) |
| `transform.py` | `csv_row_to_incident`: description → title (120 chars), date → created_at (midnight UTC), status/category/branch maps, origin `customer` |

The backoffice receives a copy of the catalog (without the CSV maps):

```bash
node scripts/generate-incident-catalog.mjs          # regenerate
node scripts/generate-incident-catalog.mjs --check  # fail if stale
```

Import from Python with the repository root on `sys.path`:
`from packages.shared.incidents import validate_incident`.
