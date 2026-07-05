# What's New in dgi_compliance 2.0.0

A clean, upgrade-safe rebuild that adds a full mapping + validation-matrix layer on top of the
proven v1.4 e-DEF engine. **No ERPNext core DocType is modified.**

## New DocTypes (mapping layer)

| DocType | Purpose |
|---------|---------|
| **DGI Item Type** | BIE / SER / TAX catalog (synced from `/api/info/itemTypes`). |
| **DGI Customer Type** | PP / PM / PC / PL / AO catalog **+ Matrix B** (per-type mandatory customer fields). |
| **DGI Invoice Type** | FV / FT / FA / EV / ET / EA catalog **+ behaviour flags** (export / prepayment / credit / requires-reference). |
| **DGI Tax Group** | A..P catalog with rate (synced from `/api/info/taxGroups`). |
| **DGI Point of Sale** | POS / e-MCF binding with NID, optional per-POS token, sales-location flag, **Matrix A** requirements. |
| **DGI Validation Matrix** | User-editable combination rules implementing **Matrices C, D, E, F**. |

Each catalog has: API **Refresh** button, **manual-override lock**, source, **last-synced** timestamp and sync status.

## Validation & locking engine (`edef/matrix.py`)

- Wired into `Sales Invoice.validate` (after the VAT ceiling).
- Evaluates **all six matrices**:
  - **A** POS → NID/Token (on DGI Point of Sale)
  - **B** Customer type → mandatory fields (on DGI Customer Type)
  - **C** Invoice type × VAT group (LOC/FOR) × Country (CD/Non-CD)
  - **D** Item type × Tax group
  - **E** Prepayment/invoice type × Item type
  - **F** Credit nature × Item type
- Per-rule **Allow/Blocked**, **Block/Warn** severity, custom message, and priority.
- Settings → **Validation Mode** = `Enforce | Warn only | Off`; **Override Roles** can force-save.
- Every evaluation with a violation is written to **DGI Exchange Log** (`matrix-validate`).

## New custom fields (fixtures, upgrade-safe)

`Sales Invoice`: `dgi_invoice_type`, `custom_dgi_vat_group`, `custom_dgi_export`,
`custom_dgi_reference[_type|_desc]`, `dgi_validation_override` (+ existing result fields).
`Customer`: `dgi_customer_type` · `Item`: `dgi_item_type` · `Company`: `dgi_isf_number` ·
`POS Profile`: `dgi_point_of_sale`.

## Engine corrections

- Mapper now prefers `Company.dgi_isf_number`, `Item.dgi_item_type` and the explicit `dgi_invoice_type`.
- Added the previously-missing classification custom fields the mapper referenced (`custom_dgi_export`,
  `custom_dgi_reference*`) — these were used in code but absent from fixtures in v1.4.
- `validate` event is now a list (VAT ceiling → matrix locking).

## Sync & seed

- `sync_all` and dedicated **Refresh** endpoints (`refresh_catalog`, `refresh_points_of_sale`) populate
  the new catalogs and auto-create points of sale from the e-MCF list.
- `seed_all` seeds static catalogs, the mapping DocTypes, and the default Matrices A–F
  (matrix seed runs only when the table is empty — never clobbers customisation).
- Patch `v2_0.seed_mapping_and_matrix` brings existing v1.x installs up to date on `migrate`.

## Upgrade

```bash
bench --site <site> migrate
bench build && bench restart
```

Insert-only seeding preserves existing data. See the v2.0 **Implementation**, **Setup**,
**Configuration**, and **Maintenance & Debugging** guides, and the rewritten **FRD**.

## Hotfix (2.0.0) - migration on Frappe v16

Fixed a fatal `bench migrate` error on newer Frappe builds:

```
frappe.exceptions.ValidationError: Patch type PatchType.pre_model_sync not found in patches.txt
```

Newer Frappe parses `patches.txt` with a ConfigParser that requires **both** section
headers. `patches.txt` now declares an (empty) `[pre_model_sync]` section in addition to
`[post_model_sync]`:

```
[pre_model_sync]

[post_model_sync]
dgi_compliance.patches.v1_2.seed_static_catalogs
dgi_compliance.patches.v2_0.seed_mapping_and_matrix
```

Also aligned the version string to `2.0.0` in `__init__.py` and `pyproject.toml`
(previously `1.4.0`) so it matches `hooks.py`.

## Hotfix 2.0.1 - reference sync datetime + per-catalog buttons

- Fixed `MySQLdb.OperationalError 1292 Incorrect datetime value` when refreshing reference data.
  The e-DEF API returns timezone-aware ISO-8601 timestamps (e.g. `2026-06-08T16:04:42+01:00`,
  `tokenValid: ...Z`) which MariaDB `DATETIME` columns reject. New helper `edef.util.to_db_datetime`
  normalises them to `YYYY-MM-DD HH:MM:SS`; applied to currency-rate `value_date` and `tokenValid`.
- DGI Compliance Settings now has **individual update buttons**: MAJ Points de vente, Types d'article,
  Types de client, Types de facture, Groupes de taxe - plus **Tester la connexion DGI** (live
  `/api/info/status` check) and the existing Synchroniser TOUT / Purger les logs.
