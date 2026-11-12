"""DGI Gateway: identity and life cycle of a site gateway.

Nouvelle -> (code d'enrolement) Code emis -> (enroll depuis le poste) A approuver
         -> (approbation humaine) Active -> (revocation) Revoquee
v5.4.2: A approuver -> (demande annulee) Nouvelle ; Active -> (approbation rejetee) Nouvelle. Both by a
DGI Fiscal Manager or a DGI Approbateur, with a reason; the key is discarded and the poste must be enrolled
again with a new code (a revoked poste, on the contrary, is closed for good).
The status only changes through these actions. The enrolment code is shown once, stored as a
hash, valid 30 minutes and single use. Approval compares the key fingerprint shown on the site
machine with the one stored here (mandatory) and, for a browser station, must be done by another
person than the one who enrolled it (setting station_approval_two_people).
Key types: browser station = ECDSA P-256 (WebCrypto, non-extractable); service = Ed25519.
"""
import base64
import hashlib
import hmac
import secrets

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_to_date, get_datetime, now_datetime

from dgi_compliance.fiscal import mcf_format as F
from dgi_compliance.gateway import signing
from dgi_compliance.gateway.events import log_event

MANAGERS = ("System Manager", "DGI Fiscal Manager")
# v5.4.1: written by the actions only (code, enrolment, approval, revocation, heartbeat), never by a form or
# an API call: 'read only' on the form is not a server-side protection.
SYSTEM_FIELDS = ("status", "public_key", "key_fingerprint", "key_algorithm", "api_user", "enrolled_by", "enrolled_on",
                 "approved_by", "approved_on", "revoked_on", "enrollment_code_hash", "enrollment_expires",
                 "code_issued_by", "last_seen", "last_ip", "gateway_version", "host_info", "offline_alert")
# written at every heartbeat WITHOUT changing 'modified': an open form is stale within a minute. They are put
# back as stored, silently; a difference is never treated as a forgery.
TELEMETRY_FIELDS = ("last_seen", "last_ip", "gateway_version", "host_info", "offline_alert")
IDENTITY_FIELDS = ("kind", "point_of_sale")      # frozen once a key was received


from dgi_compliance.fiscal.compare import same_value as _same  # noqa: E402


class EnrollmentRefused(frappe.AuthenticationError):
    """Refused enrolment code. HTTP 401 for the service gateway (public endpoint). A SUBCLASS on purpose:
    Frappe clears the session cookies for frappe.AuthenticationError itself, which logged the cashier out
    of ERPNext at every mistyped code (v5.3.2 - v5.4.0)."""


CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_VALIDITY_MIN = 30


def _code_hash(gateway_id: str, code: str) -> str:
    norm = (code or "").replace("-", "").replace(" ", "").upper()
    return hashlib.sha256(f"{gateway_id}|{norm}".encode()).hexdigest()


class DGIGateway(Document):
    def _guard_system_fields(self):
        if self.flags.get("dgi_action") or frappe.flags.in_patch or frappe.flags.in_install or frappe.flags.in_migrate:
            return
        if self.is_new():
            # a gateway is born 'Nouvelle', without key: it becomes Active by enrolment + approval only
            for f in SYSTEM_FIELDS:
                self.set(f, None)
            self.status, self.offline_alert = "Nouvelle", 0
            return
        before = self.get_doc_before_save()
        if not before:
            return
        for f in TELEMETRY_FIELDS:
            self.set(f, before.get(f))
        changed = [f for f in SYSTEM_FIELDS if f not in TELEMETRY_FIELDS and not _same(self.get(f), before.get(f))]
        if before.get("public_key"):
            changed += [f for f in IDENTITY_FIELDS if not _same(self.get(f), before.get(f))]
        if changed:
            try:
                log_event(self.name, "MODIFICATION_REFUSEE", _("{0} a tente de modifier {1} sur la passerelle {2}.")
                          .format(frappe.session.user, ", ".join(changed), self.name), level="Critique")
                frappe.db.commit()
            except Exception:
                pass
            frappe.throw(_("Champs geres par le systeme, non modifiables : {0}. Utilisez les actions Generer un "
                           "code, Approuver ou Revoquer.").format(", ".join(self.meta.get_label(f) for f in changed)),
                         frappe.PermissionError)

    def validate(self):
        self._guard_system_fields()
        self.gateway_id = (self.gateway_id or "").strip().upper()
        if not self.gateway_id or any(c.isspace() for c in self.gateway_id):
            frappe.throw(_("Identifiant de passerelle invalide (pas d'espace)."))
        device, company = frappe.db.get_value("DGI Point of Sale", self.point_of_sale, ["mcf_device", "company"]) or (None, None)
        if not device and self.status not in ("Nouvelle", "Revoquee"):
            frappe.throw(_("Le point de vente {0} n'a pas de MCF rattache.").format(self.point_of_sale))
        if self.mcf_device and self.mcf_device != device and self.status == "Active":
            frappe.throw(_("Le MCF du point de vente a change : revoquez cette passerelle et enrolez-en une nouvelle."))
        self.mcf_device, self.company = device, company
        if not self.is_new() and self.has_value_changed("status") and not self.flags.dgi_action:
            frappe.throw(_("Le statut d'une passerelle change uniquement par les actions Generer un code, "
                           "Approuver ou Revoquer."))
        if self.status == "Active":
            other = frappe.db.get_value("DGI Gateway", {"mcf_device": self.mcf_device, "status": "Active",
                                                        "name": ["!=", self.name]}, "name")
            if other:
                frappe.throw(_("La passerelle {0} est deja active pour ce MCF.").format(other))

    def on_trash(self):
        if self.status in ("Active", "A approuver") or frappe.db.exists("DGI Fiscal Operation", {"gateway": self.name}):
            frappe.throw(_("Une passerelle utilisee ne se supprime pas : revoquez-la."))


def _action_save(gw):
    gw.flags.dgi_action = True
    gw.save(ignore_permissions=True)


@frappe.whitelist(methods=["POST"])
def generate_enrollment_code(gateway: str) -> dict:
    frappe.only_for(MANAGERS)
    gw = frappe.get_doc("DGI Gateway", gateway)
    if gw.status == "Revoquee":
        frappe.throw(_("Passerelle revoquee : creez-en une nouvelle."))
    if not gw.mcf_device:
        frappe.throw(_("Le point de vente {0} n'a pas encore de MCF valide. Faites lire et proposer le MCF depuis la page "
                       "Poste MCF, validez-le (DGI MCF Device > Valider ce MCF), puis generez le code.").format(gw.point_of_sale))
    code = "".join(secrets.choice(CODE_ALPHABET) for _i in range(12))
    code = "-".join(code[i:i + 4] for i in range(0, 12, 4))
    gw.enrollment_code_hash = _code_hash(gw.name, code)
    gw.enrollment_expires = add_to_date(now_datetime(), minutes=CODE_VALIDITY_MIN)
    gw.code_issued_by = frappe.session.user
    if gw.status != "Active":
        gw.status = "Code emis"
    _action_save(gw)
    log_event(gw.name, "CODE_EMIS", _("Code d'enrolement emis par {0}, valable {1} min.")
              .format(frappe.session.user, CODE_VALIDITY_MIN))
    return {"code": code, "expires": str(gw.enrollment_expires), "gateway": gw.name}


def _key_algorithm(raw: bytes) -> str | None:
    from dgi_compliance.gateway.auth import ALG_ED25519, ALG_P256
    if len(raw) == 32:
        return ALG_ED25519
    if len(raw) == 65 and raw[0] == 4:
        try:
            from cryptography.hazmat.primitives.asymmetric import ec
            ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
            return ALG_P256
        except ValueError:
            return None
    return None


def enroll_gateway(gateway_id, code, public_key, device, host, enrolled_by=None) -> dict:
    """Called by the service gateway (public endpoint, rate limited) or by the Poste MCF page
    (gateway.station.enroll_station, logged-in operator)."""
    from dgi_compliance.gateway.auth import ALG_ED25519, ALG_P256, KIND_BROWSER
    gateway_id = (gateway_id or "").strip().upper()
    generic = _("Enrolement refuse : code invalide, expire ou deja utilise.")
    if not gateway_id or not frappe.db.exists("DGI Gateway", gateway_id):
        frappe.throw(generic, EnrollmentRefused)
    gw = frappe.get_doc("DGI Gateway", gateway_id)
    if not gw.enrollment_code_hash or not gw.enrollment_expires \
            or get_datetime(gw.enrollment_expires) < now_datetime() \
            or not hmac.compare_digest(gw.enrollment_code_hash, _code_hash(gw.name, code)):
        log_event(gw.name, "ENROLEMENT_REFUSE", _("Code invalide, expire ou deja utilise (saisi par {0}).")
                  .format(enrolled_by or _("la passerelle")), level="Alerte")
        frappe.db.commit()
        frappe.throw(generic, EnrollmentRefused)
    # the code is consumed whatever happens next
    gw.enrollment_code_hash, gw.enrollment_expires = None, None
    try:
        raw = base64.b64decode(public_key or "", validate=True)
    except ValueError:
        raw = b""
    algorithm = _key_algorithm(raw)
    browser = (gw.get("kind") or KIND_BROWSER) == KIND_BROWSER
    expected = ALG_P256 if browser else ALG_ED25519
    if algorithm != expected or (browser and not enrolled_by):
        _action_save(gw)
        log_event(gw.name, "ENROLEMENT_REFUSE", _("Cle {0} refusee pour une passerelle de type {1}.")
                  .format(algorithm or "invalide", gw.get("kind")), level="Alerte")
        frappe.db.commit()
        frappe.throw(_("Cle publique invalide pour ce type de passerelle ({0}).").format(gw.get("kind")))
    device = device or {}
    dev = frappe.get_doc("DGI MCF Device", gw.mcf_device)
    expected_nif = (frappe.db.get_value("Company", gw.company, "tax_id") or "").strip() if gw.company else ""
    seen_nid = F.nid_of(device.get("mid"))
    problems = []
    if seen_nid != dev.nid:
        problems.append(_("MCF branche : NID {0} ; NID attendu pour {1} : {2}").format(seen_nid or "?", gw.point_of_sale, dev.nid))
    if expected_nif and (device.get("nif") or "") != expected_nif:
        problems.append(_("NIF du MCF {0} different du NIF de la societe {1}").format(device.get("nif"), expected_nif))
    if problems:
        _action_save(gw)
        if seen_nid and seen_nid != dev.nid and not frappe.db.exists("DGI MCF Device", seen_nid):
            try:
                from dgi_compliance.fiscal.registry import record_observation
                record_observation(device, enrolled_by or "Guest", gateway=gw.name)
            except Exception:
                frappe.log_error(title="DGI : MCF inconnu a l'enrolement")
        log_event(gw.name, "ENROLEMENT_REFUSE", "; ".join(problems), level="Critique", details={"device": device})
        frappe.db.commit()
        frappe.throw("; ".join(problems))
    gw.public_key = base64.b64encode(raw).decode()
    gw.key_fingerprint = signing.fingerprint(raw)
    gw.key_algorithm = algorithm
    gw.enrolled_by = enrolled_by
    gw.host_info = frappe.as_json(host or {})[:1000]
    gw.gateway_version = str((host or {}).get("gateway_version") or "")[:20]
    gw.enrolled_on = now_datetime()
    gw.last_ip = getattr(frappe.local, "request_ip", None)
    if gw.status == "Active":
        # key rotation of an active gateway: the new key must be approved again
        if gw.api_user:
            frappe.db.set_value("User", gw.api_user, "enabled", 0)
    gw.status = "A approuver"
    _action_save(gw)
    if device.get("nif"):
        dev.db_set("nif", device.get("nif"), update_modified=False)
    log_event(gw.name, "ENROLEE", _("Cle {0} recue, en attente d'approbation.").format(gw.key_fingerprint),
              details={"host": host or {}, "mid": device.get("mid")})
    return {"status": gw.status, "fingerprint": gw.key_fingerprint}


def _ensure_user(gw) -> str:
    email = f"{gw.name.lower()}@dgi-gateway.invalid"
    if frappe.db.exists("User", email):
        user = frappe.get_doc("User", email)
        user.enabled = 1
        if "DGI Gateway" not in [r.role for r in user.roles]:
            user.append("roles", {"role": "DGI Gateway"})
        user.save(ignore_permissions=True)
    else:
        user = frappe.get_doc({"doctype": "User", "email": email, "first_name": f"Passerelle {gw.name}",
                               "user_type": "Website User", "send_welcome_email": 0, "enabled": 1,
                               "roles": [{"role": "DGI Gateway"}]})
        user.flags.no_welcome_mail = True
        user.insert(ignore_permissions=True)
    return user.name


@frappe.whitelist(methods=["POST"])
def approve_gateway(gateway: str, fingerprint: str | None = None) -> dict:
    """v5.4.0: a DGI Fiscal Manager OR a DGI Approbateur approves, never the person who enrolled the poste
    (two-person rule), never a technical account (fiscal.approvals)."""
    from dgi_compliance.fiscal import approvals as A
    role = A.require_approver(_("Approbation du poste"))
    frappe.db.sql("select name from `tabDGI Gateway` where name=%s for update", gateway)
    gw = frappe.get_doc("DGI Gateway", gateway)
    if gw.status != "A approuver":
        frappe.throw(_("Seule une passerelle 'A approuver' peut etre approuvee."))
    if gw.api_user and gw.api_user == frappe.session.user:
        frappe.throw(_("Le compte technique d'une passerelle ne peut pas approuver."), frappe.PermissionError)
    if not frappe.has_permission("DGI Point of Sale", "read", gw.point_of_sale):
        frappe.throw(_("Vous n'avez pas acces au point de vente {0}.").format(gw.point_of_sale), frappe.PermissionError)
    typed = (fingerprint or "").strip().upper().replace(" ", "")
    if not typed or not hmac.compare_digest(typed, (gw.key_fingerprint or "").upper()):
        # committed before the refusal: the request is rolled back, the journal entry must stay (v5.4.1)
        log_event(gw.name, "APPROBATION_REFUSEE", _("Empreinte saisie par {0} differente de la cle recue.")
                  .format(frappe.session.user), level="Alerte")
        frappe.db.commit()
        frappe.throw(_("L'empreinte saisie ne correspond pas a la cle recue : n'approuvez pas ce poste."))
    # v5.4.1: a service gateway has no logged-in enrolling person: the one who issued the code is its
    # initiator. Before, a manager could create a 'Service' gateway, enrol a key of his own and approve it alone.
    initiator = gw.get("enrolled_by") or gw.get("code_issued_by")
    mode = A.check_two_people(initiator, A.RULE_STATION, _("l'enrolement du poste {0}").format(gw.name),
                              reference=gw.name, gateway=gw.name)
    for other in frappe.get_all("DGI Gateway", filters={"mcf_device": gw.mcf_device, "status": "Active",
                                                        "name": ["!=", gw.name]}, pluck="name"):
        _revoke(other, _("Remplacee par {0}").format(gw.name))
    gw.api_user = _ensure_user(gw)
    gw.status, gw.approved_by, gw.approved_on = "Active", frappe.session.user, now_datetime()
    _action_save(gw)
    log_event(gw.name, "APPROUVEE", _("Passerelle approuvee par {0} (empreinte {1} ; {2} ; enrolee par {3}).")
              .format(frappe.session.user, gw.key_fingerprint, A.approval_note(role, mode), initiator or "-"),
              details={"approved_by": frappe.session.user, "role": role, "mode": mode,
                       "enrolled_by": gw.get("enrolled_by"), "code_issued_by": gw.get("code_issued_by")})
    return {"status": gw.status}


@frappe.whitelist(methods=["POST"])
def revoke_gateway(gateway: str, reason: str | None = None) -> dict:
    frappe.only_for(MANAGERS)
    return _revoke(gateway, reason)


def _revoke(gateway: str, reason: str | None = None) -> dict:
    """Revocation itself (also used when an approved poste replaces another one for the same MCF)."""
    gw = frappe.get_doc("DGI Gateway", gateway)
    if gw.api_user:
        frappe.db.set_value("User", gw.api_user, "enabled", 0)
    gw.status, gw.revoked_on = "Revoquee", now_datetime()
    gw.enrollment_code_hash = None
    _action_save(gw)
    log_event(gw.name, "REVOQUEE", reason or _("Revoquee par {0}").format(frappe.session.user), level="Alerte")
    return {"status": gw.status}


def _withdraw(gateway: str, reason: str | None, expected_status: str, action: str, event_code: str, message: str) -> dict:
    """Common part of 'Annuler la demande' and 'Rejeter l'approbation' (v5.4.2): the poste goes back to
    'Nouvelle' without key. Never the two-person rule: withdrawing a right is always allowed to an approver,
    including the person who enrolled the poste."""
    from dgi_compliance.fiscal import approvals as A
    role = A.require_approver(action)
    reason = (reason or "").strip()
    if len(reason) < 3:
        frappe.throw(_("{0} : le motif est obligatoire.").format(action))
    frappe.db.sql("select name from `tabDGI Gateway` where name=%s for update", gateway)
    gw = frappe.get_doc("DGI Gateway", gateway)
    if gw.status != expected_status:
        frappe.throw(_("{0} : le poste {1} est a l'etat {2} (attendu : {3}).")
                     .format(action, gw.name, _(gw.status), _(expected_status)))
    if gw.api_user and gw.api_user == frappe.session.user:
        frappe.throw(_("Le compte technique d'une passerelle ne peut pas faire cette action."), frappe.PermissionError)
    if not frappe.has_permission("DGI Point of Sale", "read", gw.point_of_sale):
        frappe.throw(_("Vous n'avez pas acces au point de vente {0}.").format(gw.point_of_sale), frappe.PermissionError)
    before = {"status": gw.status, "fingerprint": gw.key_fingerprint, "enrolled_by": gw.get("enrolled_by"),
              "enrolled_on": str(gw.get("enrolled_on") or ""), "approved_by": gw.get("approved_by"),
              "approved_on": str(gw.get("approved_on") or ""), "code_issued_by": gw.get("code_issued_by")}
    if gw.api_user:
        frappe.db.set_value("User", gw.api_user, "enabled", 0)
    gw.public_key = gw.key_fingerprint = gw.key_algorithm = None
    gw.enrolled_by = gw.enrolled_on = gw.approved_by = gw.approved_on = None
    gw.enrollment_code_hash = gw.enrollment_expires = gw.code_issued_by = None
    gw.status = "Nouvelle"
    _action_save(gw)
    log_event(gw.name, event_code, message.format(user=frappe.session.user, fingerprint=before["fingerprint"] or "-",
                                                 enrolled_by=before["enrolled_by"] or "-",
                                                 approved_by=before["approved_by"] or "-", reason=reason[:300]),
              level="Alerte", details=dict(before, by=frappe.session.user, role=role, reason=reason))
    return {"status": gw.status}


@frappe.whitelist(methods=["POST"])
def cancel_enrollment_request(gateway: str, reason: str | None = None) -> dict:
    """v5.4.2 - 'Annuler la demande' : a poste waiting for approval is refused. DGI Fiscal Manager or
    DGI Approbateur. The key received is discarded; a new code is needed to enrol the poste again."""
    return _withdraw(gateway, reason, "A approuver", _("Annulation de la demande d'approbation"), "DEMANDE_ANNULEE",
                     _("Demande d'approbation annulee par {user} (empreinte {fingerprint}, enrolee par {enrolled_by}). "
                       "Motif : {reason}"))


@frappe.whitelist(methods=["POST"])
def reject_approval(gateway: str, reason: str | None = None) -> dict:
    """v5.4.2 - 'Rejeter l'approbation' : an approved (Active) poste loses its approval. DGI Fiscal Manager or
    DGI Approbateur. The poste stops at once (its signed requests are refused), its technical account is
    disabled; it can be enrolled again with a new code and approved again."""
    return _withdraw(gateway, reason, "Active", _("Rejet de l'approbation du poste"), "APPROBATION_REJETEE",
                     _("Approbation rejetee par {user} (empreinte {fingerprint}, approuvee par {approved_by}, "
                       "enrolee par {enrolled_by}). Motif : {reason}"))


def gateway_config(gw) -> dict:
    from frappe.utils import cint, flt
    from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings
    s = get_settings()
    dev = frappe.db.get_value("DGI MCF Device", gw.mcf_device, ["nid", "status"], as_dict=True) or {}
    nif = (frappe.db.get_value("Company", gw.company, "tax_id") or "").strip() if gw.company else ""
    return {"gateway_id": gw.name, "status": gw.status, "point_of_sale": gw.point_of_sale,
            "kind": gw.get("kind"), "station_poll_interval_s": flt(s.get("station_poll_interval_s") or 15),
            "expected_nid": dev.get("nid") or "", "device_status": dev.get("status"), "expected_nif": nif,
            "serial_port": gw.serial_port or "", "baudrate": cint(gw.baudrate or 115200),
            "lease_seconds": cint(s.get("mcf_lease_seconds") or 180),
            "poll_interval_s": flt(s.get("gateway_poll_interval_s") or 2.0),
            "tolerance": flt(s.get("mcf_totals_tolerance") or 1.0),
            "pending_timeout_s": cint(s.get("mcf_pending_timeout_s") or 90),
            "server_time": str(now_datetime())}
