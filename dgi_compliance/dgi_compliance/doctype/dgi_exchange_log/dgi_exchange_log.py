from frappe.model.document import Document


class DGIExchangeLog(Document):
    """Journal of every exchange with the e-MCF (electronic journal, SFE 1.1.20)."""

    @staticmethod
    def clear_old_logs(days=180):
        # Log Settings hook: only technical entries are purged, fiscal exchanges are kept.
        from dgi_compliance.edef.audit import purge_old_logs
        purge_old_logs(days)
