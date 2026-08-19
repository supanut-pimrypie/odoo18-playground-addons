# odoo18-playground-addons
## Run (docker)

Odoo 18 in docker, connecting to an existing PostgreSQL — this compose file
starts **no** database of its own. The image is `odoo:18` plus
`python3-jsonschema` (see `Dockerfile`), which `core_api` needs for payload
validation; compose builds it on first use, `docker compose build` rebuilds it.

```bash
cp .env.example .env      # then fill in the real DB host/user/password/name
```

Plain `up` never creates the database — it only connects. Run the install once
first: with `-i`, odoo creates `DB_NAME` if it is missing (the role needs
CREATEDB) and installs the module into it.

```bash
docker compose run --rm odoo odoo -c /etc/odoo/odoo.conf -d "$DB_NAME" -i core_api --without-demo=all --stop-after-init
```

Then:

```bash
docker compose up -d
```

Open http://localhost:8069. If the container restart-loops, the reason is almost
always a missing database or wrong credentials: `docker compose logs -f odoo`.

* `config/odoo.conf` — addons path, `list_db = False` (a shared DB must not be
  droppable from the web UI), workers. Edit `admin_passwd` before any real use.
* DB credentials live in `.env` only; the odoo entrypoint turns `HOST`/`PORT`/
  `USER`/`PASSWORD` into command-line options. Do not repeat them in odoo.conf,
  the file would win.
* Both addon directories are mounted read-only at `/mnt/extra-addons`. Editing a
  file on the host is enough; restart the container to reload python code.

### Upgrading after a code change

Python is imported once per process. A running server that already loaded a
module will not see new `.py` code, so pressing **Apps > Upgrade** right after
editing a model fails with `Field "x" does not exist in model "y"` — the new XML
references a field the live process never imported. The database is rolled back,
nothing is broken, but the upgrade does not happen.

The upgrade therefore has to run in a **fresh process**. This is the one command
that always works, locally and in production:

```bash
docker compose run --rm odoo odoo -c /etc/odoo/odoo.conf -d "$DB_NAME" -u core_api --stop-after-init
docker compose restart odoo
```

In production this is not a workaround, it is the normal flow: a deploy ships
new code and restarts the container, so the process is new by definition and the
Apps UI is never how upgrades happen.

If you would rather not think about it, set `ODOO_UPDATE=core_api` in `.env`
and recreate the container — the upgrade then runs inside the new process on
every start:

```bash
docker compose up -d --force-recreate odoo
```

Clear `ODOO_UPDATE` again afterwards so ordinary restarts stay fast.

`ODOO_DEV=xml` (local only) makes odoo read views from the files, so XML edits
need no upgrade at all. `ODOO_DEV=reload` is documented to restart odoo when a
`.py` changes, but it does not work here: the watcher starts and never fires,
because Docker Desktop's Windows bind mounts do not deliver file events into the
container. Dev mode also disables asset minification and shows tracebacks in the
browser, so keep it empty anywhere real.

| Changed | Needed |
| --- | --- |
| XML views or data only | Apps > Upgrade in the UI is enough |
| Python: model, field, method | Fresh process: `compose run ... -u`, or restart first |

Run the test suite:

```bash
docker compose run --rm odoo odoo -c /etc/odoo/odoo.conf -d test_db -i core_api --test-enable --test-tags=/core_api --stop-after-init
```

## Modules

* `core-addons/core_api` — the base layer: call logging, per-route on/off
  switches, master switches in Settings > API, monthly log cleanup.

  Menus, all under **API** and restricted to Settings users:

  | Menu | What it is |
  |---|---|
  | Dashboard | Success/fail counts over 1, 7, 14 and 30 days, plus a doughnut and matching table of calls per module. A module selector at the top rescopes the whole page and drills the breakdown down to endpoints (or paths, for the no-module bucket). Every card, slice and table row opens the matching filtered log list. |
  | API Endpoints | One row per route, kanban grouped by module, toggle only. |
  | API Modules | Switch off every route of one module at once. |
  | API Logs | The raw rows, with payloads. |
* `pimrypie-addons/api_demo` — worked examples of every core_api pattern.
  Install it, hit the routes, watch the rows appear under API > API Endpoints.
  Covers public, session, bearer, error and outbound routes. Delete it once the
  patterns are copied.

## Writing an API on top of core_api

A new API is a normal addon that depends on `core_api` and decorates its routes:

```
pimrypie-addons/my_api/
├── __manifest__.py          'depends': ['core_api']
├── __init__.py              from . import controllers
└── controllers/
    ├── __init__.py          from . import main
    └── main.py
```

```python
from odoo import http
from odoo.addons.core_api.controllers.api import json_body, json_response, log_api
from odoo.http import request


class MyApi(http.Controller):

    @http.route('/api/v1/order/<int:order_id>', type='http', auth='bearer',
                methods=['GET'], csrf=False)
    @log_api
    def get_order(self, order_id):
        order = request.env['sale.order'].browse(order_id).exists()
        if not order:
            return json_response({'error': 'not found'}, status=404)
        return json_response({'id': order.id, 'name': order.name})
```

After adding or changing a route, upgrade the module so the sweep picks it up:

```bash
docker compose run --rm odoo odoo -c /etc/odoo/odoo.conf -d "$DB_NAME" -u my_api --stop-after-init
```

### Rules that bite

1. **`@http.route` goes above `@log_api`.** Swap them and the route is never
   registered, nothing is logged, and the toggle does nothing.
2. **Use `type='http'`, not `type='json'`.** The JSON-RPC dispatcher answers 200
   even for errors, which makes the `status_code` column meaningless.
3. **Pick the right `auth`** — see the table below. `auth='user'` answers a
   machine client with a 303 redirect to the login page, not a 401.
4. **`csrf=False`** on every route not posted from an Odoo form.
5. **Return the right status.** Expected failures get 400/404/409 through
   `json_response(..., status=N)`. Leave 500 for real breakage, so the fail
   counts on the Dashboard surface things worth looking at.
6. **Validate the input.** `json_body()` returns `{}` for a missing or malformed
   body rather than raising; required fields are yours to check.
7. **Name secrets conventionally.** The sanitizer redacts keys matching
   `password`, `token`, `api_key`, `secret`, `authorization` and friends. A key
   named `my_pass` is stored in full.
8. **Outbound calls go through the client**, never `requests` directly, or the
   call is invisible and the kill switch cannot stop it:
   `request.env['core.api.client']._call('post', url, json=payload, timeout=10)`
9. **Be careful with `sudo()`** on a public route: it hands anonymous callers
   whatever the record rules would have blocked.
10. **The endpoint toggle is not a security control.** It fails open on purpose,
    so a database hiccup does not take every route down. Authentication belongs
    on the route.

### Choosing `auth`

| Caller | `auth` | Unauthenticated response |
| --- | --- | --- |
| Machine with an API key | `bearer` | 401 |
| Genuinely public | `public` | n/a |
| Odoo UI / an existing session | `user` | 303 to the login page |

`auth='bearer'` is built into Odoo 18 and uses the standard API keys: My Profile
> Account Security > New API Key. The key is shown once and stored hashed, and
the log row records its owner as the caller.

Two limits worth knowing: the 401 body is Odoo's HTML error page, not JSON, so
clients must check `status_code` before parsing; and authentication runs before
dispatch, so a rejected call never reaches `@log_api` and leaves no log row.

### Payload schemas

`@log_api` takes optional JSON Schemas (draft 2020-12, validated with
`jsonschema`). They live in the code next to the route, and the sweep copies
them onto the endpoint record so they are visible in the UI.

```python
@http.route('/api/v1/order', type='http', auth='bearer', methods=['POST'], csrf=False)
@log_api(
    request_schema={
        'type': 'object',
        'required': ['name', 'qty'],
        'properties': {
            'name': {'type': 'string', 'minLength': 1},
            'qty': {'type': 'integer', 'minimum': 1},
        },
    },
    response_schema={'type': 'object', 'required': ['id']},
)
def create_order(self, **kw):
    ...
```

The two directions fail differently, on purpose:

| Where | On mismatch |
| --- | --- |
| Inbound request | 400 with the offending fields, endpoint never runs |
| Inbound response | Response is still sent; the log row is marked as an error |
| Outbound request | `UserError` before anything is sent — that one is our bug |
| Outbound response | The response is returned; the log row is marked as an error |

```bash
curl -X POST -H 'Content-Type: application/json' -d '{"qty": 0}'   http://localhost:8069/api/demo/order
# {"error": "Invalid request payload",
#  "details": ["name: 'name' is a required property", "qty: 0 is less than the minimum of 1"]}
```

Outbound calls pass them as keyword arguments:

```python
request.env['core.api.client']._call(
    'post', url, json=payload, timeout=10,
    request_schema=REQUEST, response_schema=RESPONSE)
```

A malformed schema is reported as a validation problem rather than raised, so a
typo in a schema never turns into a 500 for the caller.

### Tests

Copy the shape of `core-addons/core_api/tests/test_controller.py`: an `HttpCase`
with `self.url_open()`. Its controller is declared inside the test module, so it
only exists while the suite runs.
