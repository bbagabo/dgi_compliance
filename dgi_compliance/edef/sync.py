"""Pull and refresh DGI reference data (points of sale + catalogs) from the e-DEF GET endpoints.

Stores results in two upgrade-safe DocTypes:
  - DGI EMCF             : points de vente (from /api/info/status -> emcfList)
  - DGI Reference Value  : generic catalog (clientTypes, itemTypes, invoiceTypes, paymentTypes,
                           referenceTypes, taxGroups, currencyRates)

Triggered manually (button on Settings) or by the scheduler (cadence in Settings).
"""
import frappe
from frappe.utils import now_datetime, nowdate, getdate
from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings
from dgi_compliance.edef import client

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ---------------- upsert helpers ----------------

def _upsert_emcf(rows):
    n = 0
    for r in (rows or []):
        nim = r.get("nim")
        if not nim:
            continue
        values = {
            "shop_name": r.get("shopName"),
            "emcf_status": r.get("status"),
            "address1": r.get("address1"),
            "address2": r.get("address2"),
            "address3": r.get("address3"),
            "contact1": r.get("contact1"),
            "contact2": r.get("contact2"),
            "contact3": r.get("contact3"),
            "last_synced": now_datetime(),
        }
        if frappe.db.exists("DGI EMCF", nim):
            doc = frappe.get_doc("DGI EMCF", nim)
            doc.update(values)
            doc.save(ignore_permissions=True)
        else:
            frappe.get_doc({"doctype": "DGI EMCF", "nim": nim, **values}).insert(ignore_permissions=True)
        n += 1
    return n


def _upsert_ref(category, code, description=None, value=None, value_date=None):
    if code in (None, ""):
        return 0
    name = f"{category}::{code}"
    values = {
        "category": category,
        "code": str(code),
        "description": description,
        "value": value,
        "value_date": value_date,
        "last_synced": now_datetime(),
    }
    if frappe.db.exists("DGI Reference Value", name):
        doc = frappe.get_doc("DGI Reference Value", name)
        doc.update(values)
        doc.save(ignore_permissions=True)
    else:
        frappe.get_doc({"doctype": "DGI Reference Value", **values}).insert(ignore_permissions=True)
    return 1


def _ref_list(category, res):
    """For endpoints returning [{type, description}, ...]."""
    rows = res.get("data") if isinstance(res, dict) else None
    n = 0
    for it in (rows or []):
        n += _upsert_ref(category, it.get("type"), it.get("description"))
    return n


# ------------- dedicated mapping DocTypes (v2) -------------

# e-DEF catalog name -> dedicated mapping DocType populated alongside DGI Reference Value.
DEDICATED_DOCTYPE = {
    "Client Type": "DGI Customer Type",
    "Item Type": "DGI Item Type",
    "Invoice Type": "DGI Invoice Type",
}


def _upsert_mapping(doctype, code, description=None, rate=None):
    """Insert/update a dedicated mapping row (DGI Item/Invoice/Customer Type, DGI Tax Group),
    honouring the per-row manual_override lock so user edits are never clobbered."""
    if code in (None, ""):
        return 0
    code = str(code)
    if frappe.db.exists(doctype, code):
        doc = frappe.get_doc(doctype, code)
        if doc.get("manual_override"):
            doc.db_set("last_synced", now_datetime(), update_modified=False)
            doc.db_set("sync_status", "Verrouille (override)", update_modified=False)
            return 0
        if description and not doc.get("description"):
            doc.description = description
        if rate is not None and doctype == "DGI Tax Group":
            doc.rate = rate
        doc.source = "API"
        doc.last_synced = now_datetime()
        doc.sync_status = "OK"
        doc.save(ignore_permissions=True)
    else:
        values = {"doctype": doctype, "code": code, "description": description or code,
                  "source": "API", "last_synced": now_datetime(), "sync_status": "OK"}
        if rate is not None and doctype == "DGI Tax Group":
            values["rate"] = rate
        frappe.get_doc(values).insert(ignore_permissions=True)
    return 1


def _upsert_pos_from_emcf(r):
    """Create/update a DGI Point of Sale from an emcfList entry. Auto-created records are NOT
    flagged as sales locations (so NID/Token stay optional until an admin configures them)."""
    nim = r.get("nim")
    name = (r.get("shopName") or nim or "").strip()
    if not name:
        return 0
    if frappe.db.exists("DGI Point of Sale", name):
        doc = frappe.get_doc("DGI Point of Sale", name)
        doc.nim = nim
        doc.api_status = r.get("status")
        doc.source = "API"
        doc.last_synced = now_datetime()
        doc.sync_status = "OK"
        doc.save(ignore_permissions=True)
    else:
        frappe.get_doc({
            "doctype": "DGI Point of Sale", "pos_name": name, "nim": nim,
            "api_status": r.get("status"), "is_sales_location": 0,
            "nid_requirement": "Optional", "token_requirement": "Optional",
            "source": "API", "last_synced": now_datetime(), "sync_status": "OK",
        }).insert(ignore_permissions=True)
    return 1


# ---------------- main sync ----------------

def sync_all() -> dict:
    """Fetch every reference endpoint and upsert locally. Returns a summary of counts."""
    summary = {"emcf": 0, "clientTypes": 0, "itemTypes": 0, "invoiceTypes": 0,
               "paymentTypes": 0, "referenceTypes": 0, "taxGroups": 0, "currencyRates": 0, "errors": []}

    # 1) Points of sale + token validity (from status)
    st = client.info_status()
    if st.get("ok") and isinstance(st.get("data"), dict):
        data = st["data"]
        emcf_rows = data.get("emcfList") or []
        summary["emcf"] = _upsert_emcf(emcf_rows)
        for r in emcf_rows:
            _upsert_pos_from_emcf(r)
        if data.get("tokenValid"):
            try:
                frappe.db.set_value("DGI Compliance Settings", None, "token_valid_until", data["tokenValid"])
            except Exception:
                pass
    else:
        summary["errors"].append(f"status: HTTP {st.get('status')} {st.get('error') or ''}".strip())

    # 2) Simple {type, description} catalogs
    for key, fn, cat in [
        ("clientTypes", client.client_types, "Client Type"),
        ("itemTypes", client.item_types, "Item Type"),
        ("invoiceTypes", client.invoice_types, "Invoice Type"),
        ("paymentTypes", client.payment_types, "Payment Type"),
        ("referenceTypes", client.reference_types, "Reference Type"),
    ]:
        res = fn()
        if res.get("ok"):
            summary[key] = _ref_list(cat, res)
            dt = DEDICATED_DOCTYPE.get(cat)
            if dt and isinstance(res.get("data"), list):
                for it in res["data"]:
                    _upsert_mapping(dt, it.get("type"), it.get("description"))
        else:
            summary["errors"].append(f"{key}: HTTP {res.get('status')} {res.get('error') or ''}".strip())

    # 3) Tax groups -> object { a: 16, b: 0, ... }
    tg = client.tax_groups()
    if tg.get("ok") and isinstance(tg.get("data"), dict):
        n = 0
        for letter, val in tg["data"].items():
            code = str(letter).upper()
            n += _upsert_ref("Tax Group", code, description=None, value=val)
            _upsert_mapping("DGI Tax Group", code, rate=val)
        summary["taxGroups"] = n
    else:
        summary["errors"].append(f"taxGroups: HTTP {tg.get('status')} {tg.get('error') or ''}".strip())

    # 4) Currency rates -> [{type, description, date, rate}, ...]
    cr = client.currency_rates()
    if cr.get("ok") and isinstance(cr.get("data"), list):
        n = 0
        for it in cr["data"]:
            n += _upsert_ref("Currency Rate", it.get("type"), description=it.get("description"),
                             value=it.get("rate"), value_date=it.get("date"))
        summary["currencyRates"] = n
    else:
        summary["errors"].append(f"currencyRates: HTTP {cr.get('status')} {cr.get('error') or ''}".strip())

    try:
        frappe.db.set_value("DGI Compliance Settings", None, "last_reference_sync", now_datetime())
    except Exception:
        pass
    frappe.db.commit()
    return summary


@frappe.whitelist()
def sync_now() -> dict:
    """Button entry. Requires write access to DGI Compliance Settings."""
    frappe.only_for(["System Manager", "Accounts Manager"])
    settings = get_settings()
    if not settings.get_token():
        frappe.throw("Aucun jeton e-DEF configure dans DGI Compliance Settings.")
    return sync_all()


@frappe.whitelist()
def refresh_catalog(catalog: str) -> dict:
    """Refresh a single dedicated mapping DocType from its e-DEF endpoint.
    `catalog` in: Item Type | Invoice Type | Client Type | Tax Group."""
    frappe.only_for(["System Manager", "Accounts Manager"])
    settings = get_settings()
    if not settings.get_token():
        frappe.throw("Aucun jeton e-DEF configure dans DGI Compliance Settings.")
    count, errors = 0, []
    if catalog == "Tax Group":
        tg = client.tax_groups()
        if tg.get("ok") and isinstance(tg.get("data"), dict):
            for letter, val in tg["data"].items():
                count += _upsert_mapping("DGI Tax Group", str(letter).upper(), rate=val)
        else:
            errors.append(f"taxGroups HTTP {tg.get('status')} {tg.get('error') or ''}".strip())
    else:
        spec = {
            "Item Type": (client.item_types, "DGI Item Type"),
            "Invoice Type": (client.invoice_types, "DGI Invoice Type"),
            "Client Type": (client.client_types, "DGI Customer Type"),
        }.get(catalog)
        if not spec:
            frappe.throw(f"Catalogue inconnu: {catalog}")
        fn, doctype = spec
        res = fn()
        if res.get("ok") and isinstance(res.get("data"), list):
            for it in res["data"]:
                count += _upsert_mapping(doctype, it.get("type"), it.get("description"))
        else:
            errors.append(f"{catalog} HTTP {res.get('status')} {res.get('error') or ''}".strip())
    frappe.db.commit()
    return {"count": count, "errors": errors or None}


@frappe.whitelist()
def refresh_points_of_sale() -> dict:
    """Refresh DGI EMCF + DGI Point of Sale from /api/info/status -> emcfList."""
    frappe.only_for(["System Manager", "Accounts Manager"])
    settings = get_settings()
    if not settings.get_token():
        frappe.throw("Aucun jeton e-DEF configure dans DGI Compliance Settings.")
    st = client.info_status()
    count, errors = 0, []
    if st.get("ok") and isinstance(st.get("data"), dict):
        rows = st["data"].get("emcfList") or []
        _upsert_emcf(rows)
        for r in rows:
            count += _upsert_pos_from_emcf(r)
    else:
        errors.append(f"status HTTP {st.get('status')} {st.get('error') or ''}".strip())
    frappe.db.commit()
    return {"count": count, "errors": errors or None}


# ---------------- scheduled refresh ----------------

def _should_run_today(settings) -> bool:
    freq = (settings.reference_sync_frequency or "Weekly")
    today = getdate(nowdate())
    if freq == "Daily":
        return True
    if freq == "Monthly":
        return today.day == int(settings.check_day_of_month or 1)
    # Weekly (default)
    return WEEKDAYS[today.weekday()] == (settings.check_weekday or "Monday")


def scheduled_sync():
    """Daily scheduler entry; runs only on the chosen cadence and when enabled."""
    settings = get_settings()
    if not settings.enabled or not settings.auto_sync_reference:
        return
    if not _should_run_today(settings):
        return
    summary = sync_all()
    if summary.get("errors"):
        frappe.log_error(title="[DGI] Sync referentiels - erreurs", message=frappe.as_json(summary))
