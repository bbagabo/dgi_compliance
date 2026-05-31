"""
DGI eMCF POS controller.

Adds two behaviours on top of the v1.0.0 NIM-uniqueness check:

1. Auto-update on full setup.
   The moment a POS becomes fully configured (POS Code, NIM, an Active
   status and a Bearer Token are all present) we fire a one-off background
   job that:
     - validates the token against DGI /status,
     - stamps api_operational / api_version,
     - pulls the DGI reference dictionaries (item/invoice/payment/client
       types, tax groups) using this POS's token,
     - sets setup_complete = 1.
   This means an operator only has to paste the token and save; everything
   else provisions itself. The job is enqueued (not run inline) so a slow
   DGI response never blocks the save.

2. Idempotency.
   setup_complete guards the job: it only runs again if configuration
   actually changed (e.g. a new token pasted), so repeated saves are cheap.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document


class DGIeMCFPOS(Document):
    def validate(self):
        # NIM uniqueness (mirrors BC pattern Point Of Sales.CheckNIMUniqness)
        if self.nim:
            other = frappe.db.exists(
                "DGI eMCF POS",
                {"nim": self.nim, "name": ["!=", self.name]},
            )
            if other:
                frappe.throw(f"NIM {self.nim} is already used by POS {other}.")

        # If a core field that affects auth changed, re-arm the setup job.
        if not self.is_new() and self._auth_inputs_changed():
            self.setup_complete = 0

    def on_update(self):
        """Auto-provision when the POS is fully configured."""
        if self.setup_complete:
            return  # already provisioned and nothing auth-related changed
        if not self.is_fully_configured():
            return
        # Defer to a background job so the save returns immediately.
        frappe.enqueue(
            "dgi_compliance.api.info.complete_pos_setup",
            queue="short",
            timeout=120,
            pos_code=self.name,
            enqueue_after_commit=True,
        )

    # ----------------------------------------------------------------- #
    # Helpers
    # ----------------------------------------------------------------- #

    def is_fully_configured(self) -> bool:
        return bool(
            self.pos_code
            and self.nim
            and self.status == "Active"
            and self.token
        )

    def _auth_inputs_changed(self) -> bool:
        """True if pos_code / nim / status / token differ from stored values."""
        before = self.get_doc_before_save()
        if not before:
            return True
        for f in ("pos_code", "nim", "status", "token"):
            if (before.get(f) or "") != (self.get(f) or ""):
                return True
        return False
