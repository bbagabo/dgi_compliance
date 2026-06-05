"""Thin client for the DGI RDC e-MCF / e-DEF API (server-side, Frappe Cloud safe)."""
import json
import requests
import frappe
from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def call(method: str, path: str, body: dict | None = None) -> dict:
    s = get_settings()
    base = s.base_url()
    token = s.get_token()
    url = f"{base}{path}"
    timeout = int(s.request_timeout or 15)
    try:
        resp = requests.request(
            method, url, headers=_headers(token),
            data=json.dumps(body) if body is not None else None,
            timeout=timeout,
        )
        try:
            data = resp.json() if resp.text else None
        except ValueError:
            data = resp.text
        return {"ok": resp.ok, "status": resp.status_code, "data": data, "url": url}
    except requests.RequestException as e:
        return {"ok": False, "status": 0, "data": None, "url": url, "error": str(e)}


# Information API
def info_status() -> dict:
    return call("GET", "/api/info/status")


# Billing API
def invoice_status() -> dict:
    return call("GET", "/api/invoice")


def create_invoice(dto: dict) -> dict:
    return call("POST", "/api/invoice", dto)


def confirm_invoice(uid: str, total: float, vtotal: float) -> dict:
    return call("PUT", f"/api/invoice/{uid}/confirm", {"total": total, "vtotal": vtotal})


def cancel_invoice(uid: str) -> dict:
    return call("PUT", f"/api/invoice/{uid}/cancel")
