"""Reference sequentielle par point de vente fiscal - "Ref. PDV" (v5.4.0).

What it is
  A secondary reference printed under the ERPNext invoice number: [code]-[year]-[6 digits], e.g.
  KIN01-2026-000001. The ERPNext number stays the main number (it is the FN / rn sent to the DGI).
  Nothing of this reference is sent to the DGI: the protocol and the e-MCF API are unchanged.

What it is NOT
  The per-device sequence of the DGI already exists: every MCF / e-MCF (NIM / NID) numbers its own
  transactions (DEF Compteurs FC/TC, protocol SFE-MCF v1.0 C1h / 38h, API e-MCF v1.3 `counters`).
  They are returned with the security elements, stored on the invoice and printed. The SFE never
  generates them and does not duplicate them per device.

Rules
  * Scope: one series per point de vente fiscal (setting `sequence_scope`), so it stays continuous when
    a point de vente switches between its e-MCF and its MCF (fallback); "Societe" gives one series per
    company; invoices without point de vente use `sequence_default_code`.
  * The number is given once the DGI has returned the security elements (status Normalized), right after
    they are committed, in a short transaction of the same request: a failed, rejected, retried or rerouted
    transmission never consumes a number, so the series has no gap (requirement SFE 1.15 / 1.2.1 k: unique
    reference, uninterrupted series per year), and a problem with the reference never undoes a fiscal fact.
  * Year = year of the DEF Heure (date of the normalized invoice), the series restarts every year.
  * Anti-duplicates: the counter row (code + year) is taken exclusively (INSERT ... ON DUPLICATE KEY UPDATE,
    SELECT ... FOR UPDATE), invoice row first, never a range lock (no deadlock between points de vente);
    the register line DGI Sequence Entry has the reference as primary key and the invoice as unique key;
    a reference is never changed once given (ORIGINAL and DUPLICATA show the same one).
  * If the attribution fails, it is undone (savepoint), a Critique event is logged and the hourly job
    `assign_missing` completes it.
"""
import re
import unicodedata

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

COUNTER = "DGI Sequence Counter"
ENTRY = "DGI Sequence Entry"
FIELD = "dgi_sequence_ref"
DIGITS = 6
CODE_RE = re.compile(r"^[A-Z0-9]{2,10}$")
DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
SCOPE_PVF, SCOPE_COMPANY, SCOPE_GLOBAL = "Point de vente fiscal", "Societe", "Sans point de vente"
DEFAULT_LABEL = "Réf. PDV"
DEFAULT_CODE = "SIEGE"
SAVEPOINT = "dgi_sequence"
READ_ROLES = ("System Manager", "DGI Fiscal Manager", "DGI Approbateur", "Accounts Manager")


def _settings():
    from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings
    return get_settings()


def _on(value, default=1) -> bool:
    return bool(cint(default if value in (None, "") else value))


def enabled(settings=None) -> bool:
    s = settings or _settings()
    return _on(s.get("enabled"), 0) and _on(s.get("sequence_enabled"))


def label(settings=None) -> str:
    s = settings or _settings()
    return (s.get("sequence_print_label") or "").strip() or DEFAULT_LABEL


def clean_code(value) -> str:
    """Upper case letters and digits only (accents removed), 10 characters at most."""
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]", "", text.upper())[:10]


def validate_code(code: str, what: str) -> str:
    code = clean_code(code)
    if not CODE_RE.match(code):
        frappe.throw(_("{0} : 2 a 10 lettres majuscules ou chiffres, sans espace ni tiret (ex. KIN01).").format(what))
    return code


def format_reference(code: str, year: int, number: int) -> str:
    return f"{code}-{int(year)}-{int(number):0{DIGITS}d}"


def year_of(dgi_datetime) -> int:
    """Year of the DEF Heure (jj/MM/aaaa HH:mm:ss for both channels), else the current year."""
    m = DATE_RE.search(str(dgi_datetime or ""))
    if m:
        return int(m.group(3))
    return now_datetime().year


def code_in_use(code: str, *, exclude_pvf: str | None = None) -> str | None:
    """Who already uses this code (another point de vente, or the code of invoices without point de vente)."""
    other = frappe.db.get_value("DGI Point of Sale", {"sequence_code": code, "name": ["!=", exclude_pvf or ""]})
    if other:
        return _("point de vente {0}").format(other)
    if exclude_pvf is not None and clean_code(_settings().get("sequence_default_code") or DEFAULT_CODE) == code:
        return _("factures sans point de vente (DGI Compliance Settings)")
    return None


def suggest_code(pos_name: str | None, pos_code: str | None = None, exclude_pvf: str | None = None) -> str:
    """Code proposed for a point de vente: its 'Code magasin' when valid and free, else 5 letters of its
    name + 2 digits (KINSH01)."""
    exact = clean_code(pos_code)
    if CODE_RE.match(exact) and not code_in_use(exact, exclude_pvf=exclude_pvf or ""):
        return exact
    base = (exact if CODE_RE.match(exact) else clean_code(pos_name))[:5]
    if len(base) < 2:
        base = "PDV"
    for i in range(1, 1000):
        candidate = f"{base[:10 - len(str(i).zfill(2))]}{i:02d}"
        if not code_in_use(candidate, exclude_pvf=exclude_pvf or ""):
            return candidate
    frappe.throw(_("Aucun code de sequence libre pour {0}.").format(pos_name))


def used_codes(point_of_sale: str) -> list[str]:
    return frappe.get_all(ENTRY, filters={"point_of_sale": point_of_sale}, pluck="sequence_code", distinct=True)


def _pvf_code(point_of_sale: str) -> str:
    code = frappe.db.get_value("DGI Point of Sale", point_of_sale, "sequence_code")
    if code and CODE_RE.match(code):
        return code
    # point de vente created before v5.4.0 and never saved since: give it its code now (traced)
    pvf = frappe.db.get_value("DGI Point of Sale", point_of_sale, ["pos_name", "pos_code"], as_dict=True) or {}
    code = suggest_code(pvf.get("pos_name") or point_of_sale, pvf.get("pos_code"), exclude_pvf=point_of_sale)
    frappe.db.set_value("DGI Point of Sale", point_of_sale, "sequence_code", code, update_modified=False)
    from dgi_compliance.gateway.events import log_event
    log_event(None, "CODE_SEQUENCE", _("Code de sequence {0} attribue au point de vente {1}.").format(code, point_of_sale),
              level="Info", point_of_sale=point_of_sale)
    return code


def _company_code(company: str) -> str:
    code = clean_code(frappe.db.get_value("Company", company, "abbr"))
    return code if CODE_RE.match(code) else (code + "SOC")[:10]


def scope_of(point_of_sale: str | None, company: str | None, settings=None) -> tuple[str, str]:
    """(code, scope) carrying the counter of an invoice."""
    s = settings or _settings()
    if (s.get("sequence_scope") or SCOPE_PVF) == SCOPE_COMPANY and company:
        return _company_code(company), SCOPE_COMPANY
    if point_of_sale:
        return _pvf_code(point_of_sale), SCOPE_PVF
    code = clean_code(s.get("sequence_default_code")) or DEFAULT_CODE
    return (code if CODE_RE.match(code) else DEFAULT_CODE), SCOPE_GLOBAL


def ensure_started(settings=None):
    """Activation date: invoices normalized before it never receive a reference (already printed)."""
    s = settings or _settings()
    if not s.get("sequence_start_on"):
        frappe.db.set_single_value("DGI Compliance Settings", "sequence_start_on", now_datetime())
        frappe.clear_document_cache("DGI Compliance Settings", "DGI Compliance Settings")


# --------------------------------------------------------------------------- attribution
_INVOICE_FIELDS = ("name", "company", "dgi_point_of_sale", "custom_dgi_status", "custom_dgi_code_def",
                   "custom_dgi_nim", "custom_dgi_counters", "custom_dgi_datetime", "custom_dgi_channel",
                   "custom_dgi_operation", "dgi_invoice_type", FIELD)


def _locked_invoice(invoice: str) -> dict | None:
    cols = ", ".join(f"`{c}`" for c in _INVOICE_FIELDS)
    rows = frappe.db.sql(f"select {cols} from `tabSales Invoice` where name=%s for update", invoice, as_dict=True)
    return rows[0] if rows else None


def assign(invoice: str, *, source: str, operation: str | None = None, doc=None) -> str | None:
    """Give its reference to a NORMALIZED invoice (idempotent). Returns the reference, or None when the
    invoice is not normalized or the feature is off. Raises on a database problem (see assign_safely)."""
    s = _settings()
    if not enabled(s):
        return None
    # Locks, always in this order and never on a range (no gap lock, no shared lock to upgrade): the invoice
    # row, then the counter row (exclusive from the start). The field of the invoice, read under its lock, says
    # whether a reference was already given: it is written in the same transaction as the register line.
    row = _locked_invoice(invoice)
    if not row or row.custom_dgi_status != "Normalized" or not row.custom_dgi_code_def:
        return None
    if row.get(FIELD):
        ref = row.get(FIELD)
        if not frappe.db.exists(ENTRY, ref):
            # a reference without register line is never replaced: it may already be printed
            from dgi_compliance.gateway.events import log_event
            log_event(None, "REFERENCE_HORS_REGISTRE", _("Facture {0} : reference {1} absente du registre.").format(
                invoice, ref), level="Critique", point_of_sale=row.dgi_point_of_sale)
        if doc is not None:
            doc.set(FIELD, ref)
        return ref

    ensure_started(s)
    code, scope = scope_of(row.dgi_point_of_sale, row.company, s)
    year = year_of(row.custom_dgi_datetime)
    key = f"{code}-{year}"
    now, user = now_datetime(), frappe.session.user
    # creates the row of a new code / year, or takes the existing row EXCLUSIVELY (ON DUPLICATE KEY UPDATE)
    frappe.db.sql(
        f"""insert into `tab{COUNTER}` (name, counter_key, sequence_code, year, scope, point_of_sale, company,
            last_number, creation, modified, owner, modified_by, docstatus, idx)
            values (%s, %s, %s, %s, %s, %s, %s, 0, %s, %s, %s, %s, 0, 0)
            on duplicate key update idx = idx""",
        (key, key, code, year, scope, row.dgi_point_of_sale if scope == SCOPE_PVF else None, row.company,
         now, now, user, user))
    last = cint(frappe.db.sql(f"select last_number from `tab{COUNTER}` where name=%s for update", key)[0][0])
    # defensive only (counter altered outside the SFE); the primary key of the register stays the real guard
    top = cint(frappe.db.sql(f"select max(number) from `tab{ENTRY}` where sequence_code=%s and year=%s",
                             (code, year))[0][0])
    if top > last:
        from dgi_compliance.gateway.events import log_event
        log_event(None, "SEQUENCE_INCOHERENTE", _("Compteur {0} : {1} alors que le registre va jusqu'a {2}. "
                                                 "La numerotation reprend apres {2} (aucun numero reutilise).")
                  .format(key, last, top), level="Critique", point_of_sale=row.dgi_point_of_sale)
        last = top
    number = last + 1
    ref = format_reference(code, year, number)
    entry = frappe.get_doc({
        "doctype": ENTRY, "reference": ref, "sales_invoice": invoice, "sequence_code": code, "year": year,
        "number": number, "point_of_sale": row.dgi_point_of_sale, "company": row.company,
        "channel": row.custom_dgi_channel or ("MCF" if source.startswith("MCF") else "e-MCF"),
        "dgi_device": row.custom_dgi_nim,
        "dgi_counters": row.custom_dgi_counters, "code_def": row.custom_dgi_code_def,
        "dgi_datetime": row.custom_dgi_datetime, "invoice_type": row.dgi_invoice_type,
        "operation": operation or row.custom_dgi_operation or None, "source": source,
        "assigned_on": now, "assigned_by": user if frappe.db.exists("User", user) else None,
    })
    if entry.operation and not frappe.db.exists("DGI Fiscal Operation", entry.operation):
        entry.operation = None
    entry.flags.dgi_sequence = True
    entry.insert(ignore_permissions=True)
    frappe.db.sql(f"""update `tab{COUNTER}` set last_number=%s, last_reference=%s, last_invoice=%s,
                      last_assigned_on=%s, modified=%s, modified_by=%s where name=%s""",
                  (number, ref, invoice, now, now, user, key))
    frappe.db.set_value("Sales Invoice", invoice, FIELD, ref, update_modified=False)
    if doc is not None:
        doc.set(FIELD, ref)
    return ref


def assign_when_committed(invoice: str, *, source: str, operation: str | None = None, doc=None):
    """Called where the security elements are written. The attribution runs right after the transaction that
    holds them is committed, in its own short transaction of the same request: a problem with the reference
    (lock, database error) can never undo a fiscal fact. If that transaction is rolled back instead, nothing
    is attributed (there is nothing normalized to number)."""
    if not invoice:
        return

    def _run():
        assign_safely(invoice, source=source, operation=operation, doc=doc)
        frappe.db.commit()

    frappe.db.after_commit.add(_run)


def assign_safely(invoice: str, *, source: str, operation: str | None = None, doc=None) -> str | None:
    """Attribution that never raises: on failure it is undone (savepoint), journalized, and completed later by
    the hourly job `assign_missing`."""
    if not invoice:
        return None
    try:
        frappe.db.savepoint(SAVEPOINT)
    except Exception:
        return None
    try:
        ref = assign(invoice, source=source, operation=operation, doc=doc)
    except Exception as e:
        try:
            frappe.db.rollback(save_point=SAVEPOINT)
        except Exception:
            pass        # a deadlock already rolled the (short) transaction back, savepoint included
        frappe.log_error(title="DGI : reference sequentielle", message=frappe.get_traceback())
        try:
            from dgi_compliance.gateway.events import log_event
            log_event(None, "REFERENCE_EN_ATTENTE", _("Facture {0} normalisee : reference non attribuee ({1}). "
                                                      "Elle le sera par la tache horaire.").format(invoice, str(e)[:300]),
                      level="Critique")
        except Exception:
            pass
        return None
    frappe.db.release_savepoint(SAVEPOINT)
    return ref


def assign_missing(limit: int = 500) -> int:
    """Hourly job: normalized invoices of the activation period that have no reference yet (attribution
    rolled back, or invoices normalized by an older worker during the update). Oldest first."""
    s = _settings()
    if not enabled(s) or not s.get("sequence_start_on"):
        return 0
    rows = frappe.db.sql(f"""select si.name from `tabSales Invoice` si
        left join `tab{ENTRY}` e on e.sales_invoice = si.name
        where si.custom_dgi_status = 'Normalized' and ifnull(si.custom_dgi_code_def, '') != ''
          and ifnull(si.`{FIELD}`, '') = '' and e.name is null and si.docstatus < 2
          and coalesce(si.custom_dgi_normalized_on, si.modified) >= %s
        order by coalesce(si.custom_dgi_normalized_on, si.modified), si.name limit %s""",
                         (s.get("sequence_start_on"), int(limit)), pluck=True)
    done = 0
    for name in rows:
        if assign_safely(name, source=_("Rattrapage")):
            done += 1
            frappe.db.commit()
    return done


# --------------------------------------------------------------------------- desk
def keep_system_value(doc, method=None):
    """`validate` of Sales Invoice: the reference is written by the SFE only. Whatever a form or an API
    call sends is replaced by the stored value (a stale form never erases it, nobody can type one)."""
    if doc.is_new() or not doc.name or not frappe.db.exists("Sales Invoice", doc.name):
        if doc.get(FIELD) and not frappe.db.exists(ENTRY, {"sales_invoice": doc.name or ""}):
            doc.set(FIELD, None)
        return
    stored = frappe.db.get_value("Sales Invoice", doc.name, FIELD)
    if (doc.get(FIELD) or None) != (stored or None):
        doc.set(FIELD, stored)


@frappe.whitelist(methods=["POST"])
def assign_now(invoice: str) -> dict:
    """Button on a normalized invoice without reference (DGI Fiscal Manager)."""
    frappe.only_for(("System Manager", "DGI Fiscal Manager"))
    s = _settings()
    if not enabled(s):
        frappe.throw(_("La reference sequentielle est desactivee (DGI Compliance Settings)."))
    start = s.get("sequence_start_on")
    normalized_on = frappe.db.get_value("Sales Invoice", invoice, "custom_dgi_normalized_on")
    if start and normalized_on and normalized_on < frappe.utils.get_datetime(start):
        frappe.throw(_("Facture normalisee avant l'activation de la reference ({0}) : elle a ete imprimee sans "
                       "reference et n'en recoit pas.").format(start))
    ref = assign_safely(invoice, source=_("Manuel ({0})").format(frappe.session.user))
    if not ref:
        frappe.throw(_("Aucune reference attribuee : la facture n'est pas normalisee, ou voir le journal des erreurs."))
    frappe.db.commit()
    return {"reference": ref}


@frappe.whitelist()
def check_sequence(sequence_code: str | None = None, year: int | None = None, point_of_sale: str | None = None) -> dict:
    """Control of a series (code + year): gaps, counter, references differing from the invoice."""
    frappe.only_for(READ_ROLES)
    if point_of_sale and not sequence_code:
        sequence_code = frappe.db.get_value("DGI Point of Sale", point_of_sale, "sequence_code")
    year = cint(year) or now_datetime().year
    code = clean_code(sequence_code)
    rows = frappe.db.sql(f"""select e.name, e.number, e.sales_invoice, si.`{FIELD}` as on_invoice
        from `tab{ENTRY}` e left join `tabSales Invoice` si on si.name = e.sales_invoice
        where e.sequence_code=%s and e.year=%s order by e.number""", (code, year), as_dict=True)
    numbers = [r.number for r in rows]
    expected = set(range(1, (max(numbers) if numbers else 0) + 1))
    gaps = sorted(expected - set(numbers))
    duplicates = sorted({n for n in numbers if numbers.count(n) > 1})
    mismatches = [r.name for r in rows if (r.on_invoice or "") != r.name]
    counter = cint(frappe.db.get_value(COUNTER, f"{code}-{year}", "last_number"))
    return {"code": code, "year": year, "count": len(rows), "first": rows[0].name if rows else None,
            "last": rows[-1].name if rows else None, "counter": counter, "gaps": gaps[:100],
            "duplicates": duplicates, "mismatches": mismatches[:100],
            "ok": not gaps and not duplicates and not mismatches and counter == (max(numbers) if numbers else counter)}
