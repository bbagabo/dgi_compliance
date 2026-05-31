"""
DGI HTTP client — single point of egress to the e-DEF platform.

Design notes
------------
* Per-POS Bearer tokens: every POS has its own JWT in `DGI eMCF POS`. We
  *never* take a token from `DGI Settings`; the BC reference implementation
  proves that the same company can operate multiple physical POS, each with
  its own NIM / token pair.
* Every outbound call goes through `_request()` so we always:
    1. resolve the right base URL (prod vs sandbox) from DGI Settings,
    2. attach the right Bearer token,
    3. write a full `DGI Invoice Log` row (with the token redacted),
    4. raise `DGIAPIError` on transport or HTTP failure.
* Idempotency: the *caller* is responsible for adding a Document UID to the
  request body; this client never invents one. That keeps retries safe.

The endpoints, the URL shape, and the token-per-POS pattern are mined from
the existing Microsoft Dynamics 365 BC module
(`HeloSystems DGI Exchange Module 1.0.0.0`).
"""

from __future__ import annotations

import json
from typing import Any

import frappe
import requests
from frappe import _

from dgi_compliance.utils.mapping import redact_token


# DGI endpoints — taken from BC codeunit "DGI General Functions".
BILLING_PATH = "/api/invoice"        # POST  certify, GET  status
INFO_ROOT = "/api/info"              # GET   /api/info/<dictionary>
TAX_GROUPS_PATH = "/taxGroups"
CURR_RATE_PATH = "/currencyRates"
STATUS_PATH = "/status"

DICTIONARY_PATHS = {
    "Item Type":         "/itemTypes",
    "Invoice Type":      "/invoiceTypes",
    "Payment Type":      "/paymentTypes",
    "Client Type":       "/clientTypes",
    "Reference Type":    "/referenceTypes",   # credit note reasons
}


class DGIAPIError(Exception):
    """Anything between 'we never reached the server' and 'we got a 5xx'."""

    def __init__(self, message: str, http_status: int | None = None,
                 response_body: str | None = None):
        super().__init__(message)
        self.http_status = http_status
        self.response_body = response_body


# ---------------------------------------------------------------------------
# Settings / POS resolution
# ---------------------------------------------------------------------------

def get_base_url() -> str:
    """Return the production or sandbox base URL, per DGI Settings."""
    s = frappe.get_single("DGI Settings")
    if s.environment == "Production":
        return (s.production_url or "https://edef.dgirdc.cd").rstrip("/")
    return (s.test_url or "https://developper.dgirdc.cd/edef").rstrip("/")


def get_pos_token(pos_code: str) -> str:
    """Decrypt and return the bearer token for a POS. Errors loudly if missing."""
    pos = frappe.get_doc("DGI eMCF POS", pos_code)
    if pos.status != "Active":
        frappe.throw(_("DGI POS {0} is not Active.").format(pos_code))
    token = pos.get_password("token") if pos.token else None
    if not token:
        frappe.throw(_("DGI POS {0} has no token configured.").format(pos_code))
    return token


# ---------------------------------------------------------------------------
# Core request
# ---------------------------------------------------------------------------

def _request(method: str,
             path: str,
             pos_code: str | None = None,
             json_body: dict | None = None,
             *,
             action: str,
             document_type: str | None = None,
             document_no: str | None = None,
             entry_type: str = "API Call") -> dict:
    """Issue one HTTP call, log everything, return the parsed JSON response.

    Raises DGIAPIError on any non-2xx, transport failure, or JSON decode error.
    """
    settings = frappe.get_single("DGI Settings")
    timeout = int(settings.connection_timeout or 30)
    url = f"{get_base_url()}{path}"

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Frappe-DGI-Compliance/1.0",
    }
    if pos_code:
        headers["Authorization"] = f"Bearer {get_pos_token(pos_code)}"

    request_dump = {
        "method": method.upper(),
        "url": url,
        "headers": redact_token(dict(headers)),
        "body": json_body,
    }

    success = False
    http_status: int | None = None
    response_text = ""
    message_text = ""
    parsed: dict[str, Any] | None = None

    try:
        resp = requests.request(
            method.upper(), url,
            headers=headers,
            data=json.dumps(json_body) if json_body is not None else None,
            timeout=timeout,
        )
        http_status = resp.status_code
        response_text = resp.text or ""
        if 200 <= resp.status_code < 300:
            try:
                parsed = resp.json() if response_text else {}
            except ValueError:
                raise DGIAPIError(
                    f"DGI returned non-JSON body (HTTP {resp.status_code})",
                    http_status=resp.status_code,
                    response_body=response_text,
                )
            success = True
            message_text = parsed.get("message", "") if isinstance(parsed, dict) else ""
        else:
            try:
                err = resp.json()
                message_text = err.get("message") or err.get("error") or response_text[:240]
            except ValueError:
                message_text = response_text[:240] or f"HTTP {resp.status_code}"
            raise DGIAPIError(
                f"DGI HTTP {resp.status_code}: {message_text}",
                http_status=resp.status_code,
                response_body=response_text,
            )

    except requests.RequestException as exc:
        message_text = f"Transport error: {exc}"
        raise DGIAPIError(message_text) from exc

    finally:
        # Always log — success or failure.
        try:
            _write_log(
                document_type=document_type,
                document_no=document_no,
                entry_type=entry_type,
                action=action,
                pos_code=pos_code,
                request_json=json.dumps(request_dump, indent=2, default=str),
                response_json=response_text,
                http_status=http_status,
                success=success,
                message_text=message_text[:240],
            )
        except Exception:  # noqa: BLE001
            # Logging must never break the caller. Fall back to error log.
            frappe.log_error(
                title="DGI Invoice Log write failed",
                message=frappe.get_traceback(),
            )

    return parsed or {}


def _write_log(*, document_type, document_no, entry_type, action,
               pos_code, request_json, response_json, http_status,
               success, message_text) -> None:
    doc = frappe.get_doc({
        "doctype": "DGI Invoice Log",
        "document_type": document_type or "",
        "document_no": document_no or "",
        "entry_type": entry_type,
        "action": action,
        "direction": "Outbound",
        "pos_code": pos_code,
        "request_json": request_json,
        "response_json": response_json,
        "http_status": str(http_status) if http_status is not None else "",
        "success": 1 if success else 0,
        "message_text": message_text,
        "action_datetime": frappe.utils.now_datetime(),
        "user": frappe.session.user,
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()  # log durability beats transactional cleanliness


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def get(path: str, *, pos_code: str | None = None, action: str = "GET",
        document_type=None, document_no=None) -> dict:
    return _request("GET", path, pos_code=pos_code, action=action,
                    document_type=document_type, document_no=document_no)


def post(path: str, body: dict, *, pos_code: str | None = None,
         action: str = "POST", document_type=None, document_no=None,
         entry_type="API Call") -> dict:
    return _request("POST", path, pos_code=pos_code, json_body=body,
                    action=action, document_type=document_type,
                    document_no=document_no, entry_type=entry_type)


def ping() -> dict:
    """GET /status — no auth required. Useful for health checks."""
    return _request("GET", STATUS_PATH, action="API Status",
                    entry_type="Health Check")
