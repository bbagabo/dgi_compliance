"""
Master-data refresh — pulls dictionaries from DGI and upserts them into
`DGI Reference Data`. Designed to be idempotent: re-running the job never
duplicates rows, and never removes rows that DGI dropped (we mark them
inactive instead).

Dictionaries handled:
    /api/info/itemTypes         → category 'Item Type'
    /api/info/invoiceTypes      → category 'Invoice Type'
    /api/info/paymentTypes      → category 'Payment Type'
    /api/info/clientTypes       → category 'Client Type'
    /api/info/referenceTypes    → category 'Reference Type'  (credit note reasons)

Plus two non-dictionary endpoints we mirror into the same store:
    /api/info/taxGroups         → category 'Tax Group'   (also seeds DGI Tax Group Mapping)
    /api/info/currencyRates     → category 'Currency Rate' (Rate stored in extra)
"""

from __future__ import annotations

import json

import frappe

from dgi_compliance.api import client


CATEGORY_TO_PATH = {
    "Item Type":      client.DICTIONARY_PATHS["Item Type"],
    "Invoice Type":   client.DICTIONARY_PATHS["Invoice Type"],
    "Payment Type":   client.DICTIONARY_PATHS["Payment Type"],
    "Client Type":    client.DICTIONARY_PATHS["Client Type"],
    "Reference Type": client.DICTIONARY_PATHS["Reference Type"],
    "Tax Group":      client.TAX_GROUPS_PATH,
    "Currency Rate":  client.CURR_RATE_PATH,
}


@frappe.whitelist()
def refresh_reference_data(category: str | None = None,
                            pos_code: str | None = None) -> dict:
    """Pull one or all dictionaries from DGI. Returns a per-category count."""
    settings = frappe.get_single("DGI Settings")
    # Pick any Active POS for the auth token if not provided.
    pos_code = pos_code or _pick_any_active_pos()
    if not pos_code:
        frappe.throw("No active DGI POS configured — cannot authenticate.")

    counts: dict[str, int] = {}
    categories = [category] if category else list(CATEGORY_TO_PATH)

    for cat in categories:
        path = client.DICTIONARY_PATHS.get(cat) or CATEGORY_TO_PATH.get(cat)
        if not path:
            continue
        url = f"{client.INFO_ROOT}{path}" if cat in client.DICTIONARY_PATHS \
            else path
        resp = client.get(url, pos_code=pos_code,
                          action=f"Refresh {cat}",
                          document_type="DGI Reference Data")
        rows = resp.get("data") or resp.get("items") or resp.get("result") \
            or (resp if isinstance(resp, list) else [])
        counts[cat] = _upsert_rows(cat, rows)

    settings.last_reference_data_refresh = frappe.utils.now_datetime()
    settings.save(ignore_permissions=True)
    return counts


def _pick_any_active_pos() -> str | None:
    rows = frappe.get_all("DGI eMCF POS", filters={"status": "Active"},
                          limit=1, pluck="name")
    return rows[0] if rows else None


def _upsert_rows(category: str, rows: list[dict]) -> int:
    """Insert or update one DGI Reference Data row per code; deactivate any
    code no longer present in the response."""
    if not isinstance(rows, list):
        return 0

    seen_codes: set[str] = set()
    inserted = 0
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        code = (raw.get("code") or raw.get("Code") or "").strip()
        if not code:
            continue
        description = raw.get("description") or raw.get("Description") or ""
        extra = {k: v for k, v in raw.items()
                 if k.lower() not in ("code", "description")}
        seen_codes.add(code)

        name = frappe.db.exists("DGI Reference Data",
                                {"category": category, "code": code})
        if name:
            doc = frappe.get_doc("DGI Reference Data", name)
            doc.description = description
            doc.extra = json.dumps(extra) if extra else ""
            doc.active = 1
            doc.save(ignore_permissions=True)
        else:
            frappe.get_doc({
                "doctype": "DGI Reference Data",
                "category": category,
                "code": code,
                "description": description,
                "extra": json.dumps(extra) if extra else "",
                "active": 1,
            }).insert(ignore_permissions=True)
            inserted += 1

    # Soft-deactivate anything missing from this refresh.
    existing = frappe.get_all("DGI Reference Data",
                              filters={"category": category, "active": 1},
                              fields=["name", "code"])
    for row in existing:
        if row.code not in seen_codes:
            frappe.db.set_value("DGI Reference Data", row.name, "active", 0)

    return inserted


@frappe.whitelist()
def check_pos_token_validity() -> dict:
    """Hit /status using each POS's token. Mark expired tokens on the POS."""
    out: dict[str, str] = {}
    for pos_name in frappe.get_all("DGI eMCF POS",
                                   filters={"status": "Active"},
                                   pluck="name"):
        try:
            resp = client.get(client.STATUS_PATH, pos_code=pos_name,
                              action="Token Health Check")
            frappe.db.set_value("DGI eMCF POS", pos_name, {
                "api_operational": 1,
                "api_version": resp.get("version", ""),
            })
            out[pos_name] = "ok"
        except client.DGIAPIError as exc:
            frappe.db.set_value("DGI eMCF POS", pos_name, "api_operational", 0)
            out[pos_name] = f"error: {exc}"
    return out
