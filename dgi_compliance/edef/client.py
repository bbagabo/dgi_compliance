"""Thin client for the DGI RDC e-MCF / e-DEF API (server-side, Frappe Cloud safe).

v3.5 hardening:
  * missing-token short-circuit (clear error instead of an HTTP 401 round-trip);
  * automatic retry with backoff for IDEMPOTENT calls only (GET, and PUT confirm/cancel)
    on network failures and 502/503/504. POST /api/invoice is NEVER retried automatically:
    a lost response could otherwise create a duplicate invoice at the DGI;
  * (connect, read) timeout tuple so a dead host fails fast;
  * duration_ms in every result for the audit log.
"""
import json
import time
import requests
import frappe
from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings

RETRY_STATUSES = (502, 503, 504)
CONNECT_TIMEOUT = 5  # seconds; read timeout comes from Settings.request_timeout


def _auth_value(scheme: str, token: str) -> str:
    # Some e-DEF deployments accept the raw token without the "Bearer " prefix.
    # Configurable in Settings -> auth_scheme ("Bearer" | "None").
    if (scheme or "Bearer").strip().lower() in ("none", "raw", ""):
        return token
    return f"Bearer {token}"


def _headers(token: str, scheme: str) -> dict:
    return {
        "Authorization": _auth_value(scheme, token),
        "Content-Type": "application/json",
        "Accept": "application/json",
        # Avoid brotli (content-encoding: br) so we never need the optional brotli package.
        "Accept-Encoding": "gzip, deflate",
    }


def call(method: str, path: str, body: dict | None = None, retries: int | None = None) -> dict:
    """One HTTP exchange with the e-DEF API.

    `retries`: max automatic retries on transient failures. Defaults to 2 for idempotent
    methods (GET/PUT), 0 for POST (create is not idempotent)."""
    s = get_settings()
    base = s.base_url()
    token = s.get_token()
    if not token:
        return {"ok": False, "status": 0, "data": None, "url": f"{base}{path}",
                "error": "Jeton e-DEF manquant: configurez le token dans DGI Compliance Settings."}
    scheme = getattr(s, "auth_scheme", "Bearer")
    url = f"{base}{path}"
    read_timeout = int(s.request_timeout or 15)
    if retries is None:
        retries = 2 if method.upper() in ("GET", "PUT") else 0

    attempt, last = 0, None
    while True:
        started = time.monotonic()
        try:
            resp = requests.request(
                method, url, headers=_headers(token, scheme),
                data=json.dumps(body) if body is not None else None,
                timeout=(CONNECT_TIMEOUT, read_timeout),
            )
            elapsed = round((time.monotonic() - started) * 1000)
            try:
                data = resp.json() if resp.text else None
            except ValueError:
                data = resp.text
            last = {"ok": resp.ok, "status": resp.status_code, "data": data,
                    "url": url, "duration_ms": elapsed, "attempts": attempt + 1}
            if resp.status_code not in RETRY_STATUSES or attempt >= retries:
                return last
        except requests.RequestException as e:
            elapsed = round((time.monotonic() - started) * 1000)
            last = {"ok": False, "status": 0, "data": None, "url": url,
                    "error": str(e), "duration_ms": elapsed, "attempts": attempt + 1}
            if attempt >= retries:
                return last
        attempt += 1
        time.sleep(min(2 ** attempt, 5))  # 2s, 4s, 5s...


# Information API
def info_status() -> dict:
    return call("GET", "/api/info/status")


def tax_groups() -> dict:
    return call("GET", "/api/info/taxGroups")


def invoice_types() -> dict:
    return call("GET", "/api/info/invoiceTypes")


def payment_types() -> dict:
    return call("GET", "/api/info/paymentTypes")


def client_types() -> dict:
    return call("GET", "/api/info/clientTypes")


def reference_types() -> dict:
    return call("GET", "/api/info/referenceTypes")


def item_types() -> dict:
    return call("GET", "/api/info/itemTypes")


def currency_rates() -> dict:
    return call("GET", "/api/info/currencyRates")


# Billing API
def invoice_status() -> dict:
    return call("GET", "/api/invoice")


def create_invoice(dto: dict) -> dict:
    # POST is NOT retried automatically (duplicate-invoice risk at the DGI).
    return call("POST", "/api/invoice", dto, retries=0)


def confirm_invoice(uid: str, total: float, vtotal: float) -> dict:
    return call("PUT", f"/api/invoice/{uid}/confirm", {"total": total, "vtotal": vtotal})


def cancel_invoice(uid: str) -> dict:
    return call("PUT", f"/api/invoice/{uid}/cancel")
