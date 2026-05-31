# dgi_compliance

Frappe / ERPNext app providing certification of sales documents with the
DGI RDC **e-DEF** (e-Dispositif Électronique Fiscal) platform —
the "Facture Normalisée" mandate of the Direction Générale des Impôts,
République Démocratique du Congo.

* **Production base URL:** `https://edef.dgirdc.cd`
* **Sandbox base URL:**    `https://developper.dgirdc.cd/edef`
* **Frappe Framework:** 15+ (designed and tested on 16)
* **ERPNext:** 15+ (designed and tested on 16)
* **License:** MIT
* **Maintainer:** HeloSystems — `contact@helosystems.com`

The app is **upgrade-safe**: it never modifies an ERPNext core DocType.
Everything ships as new DocTypes, Custom Fields (installed via patch),
hooks, server scripts, and scheduled jobs.

## Install (Frappe Cloud)

1. Push this repo to GitHub.
2. Frappe Cloud → Bench → **Apps → Add App** → paste GitHub URL → branch → **Save**.
3. **Deploy** the bench (runs `bench migrate`, executes all patches in `patches.txt`).
4. Site → **Apps → Install App** → tick `dgi_compliance`.
5. Open Desk → **DGI Settings** → environment + JWT token + Refresh Reference Data.

## Install (on-premise / dev)

```bash
bench get-app https://github.com/<your-org>/dgi_compliance --branch main
bench --site <your-site> install-app dgi_compliance
bench --site <your-site> migrate
```

## Folder layout

```
dgi_compliance/                        <- repo root
├── pyproject.toml
├── README.md
├── license.txt
├── MANIFEST.in
├── .gitignore
└── dgi_compliance/                    <- Python package (app_name)
    ├── __init__.py                    # __version__ = "1.0.0"
    ├── hooks.py
    ├── modules.txt
    ├── patches.txt
    ├── tasks.py                       # scheduled job entry-points
    ├── api/                           # DGI HTTP client + endpoint wrappers
    │   ├── client.py
    │   ├── invoice.py                 # POST /api/invoice
    │   └── info.py                    # GET  /api/info/...
    ├── overrides/
    │   └── sales_invoice.py           # validate / on_submit / on_cancel hooks
    ├── utils/
    │   ├── mapping.py                 # ERPNext ↔ DGI field mapping
    │   ├── json_builder.py            # request body construction
    │   └── qr.py                      # QR code rendering helper
    ├── dgi_compliance/                <- module folder (matches modules.txt)
    │   └── doctype/
    │       ├── dgi_settings/          # Single — connection + defaults
    │       ├── dgi_emcf_pos/          # Per-POS NIM + Token + status
    │       ├── dgi_invoice_log/       # Full HTTP request/response audit
    │       ├── dgi_invoice_log_comment/
    │       ├── dgi_pending_invoice/   # Retry queue + certification result
    │       ├── dgi_reference_data/    # Synced lookup tables
    │       └── dgi_tax_group_mapping/ # ERPNext Tax Template ↔ DGI Tax Group
    ├── fixtures/
    │   └── custom_field.json
    ├── patches/v1_0/
    │   ├── add_custom_fields.py
    │   ├── seed_reference_data.py
    │   ├── create_default_settings.py
    │   └── backfill_dgi_status.py
    ├── public/
    │   ├── js/sales_invoice_dgi.js
    │   └── css/dgi.css
    ├── print_format/
    │   └── dgi_sales_invoice/
    └── tests/
        ├── test_api_client.py
        └── test_invoice_mapping.py
```

## Patches (executed automatically on every deploy)

Listed in `dgi_compliance/patches.txt`:

```
[post_model_sync]
dgi_compliance.patches.v1_0.create_default_settings
dgi_compliance.patches.v1_0.add_custom_fields
dgi_compliance.patches.v1_0.seed_reference_data
dgi_compliance.patches.v1_0.backfill_dgi_status
```

All patches are idempotent and safe to re-run.

## Configuration

After installing, configure once via the Desk UI:

1. **DGI Settings** (Single) — environment (Test / Production), URLs, JWT token, NIF, NIM, ISF.
2. **DGI eMCF POS** — one record per physical point of sale, with its own JWT.
3. **DGI Tax Group Mapping** — map each ERPNext Sales Tax Template to a DGI tax group (A–P).

No code changes required for day-to-day operation.

## Smoke test

```python
# bench --site <site> console
from dgi_compliance.api.info import refresh_reference_data
refresh_reference_data()
```

Then create a Sales Invoice with one item, submit, and check:
- DGI UID, Code DEF/DGI, QR Code populated
- DGI Status = Normalized
- DGI Invoice Log has request/response payloads
