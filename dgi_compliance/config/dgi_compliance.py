"""
Desk module config — controls what shows up in /app/dgi-compliance.
"""

from frappe import _


def get_data():
    return [
        {
            "label": _("Setup"),
            "icon": "fa fa-cogs",
            "items": [
                {"type": "doctype", "name": "DGI Settings",
                 "description": _("Connection & defaults for the DGI e-DEF platform.")},
                {"type": "doctype", "name": "DGI eMCF POS",
                 "description": _("Per-POS NIM + Bearer token + status.")},
                {"type": "doctype", "name": "DGI Tax Group Mapping",
                 "description": _("ERPNext Item Tax Template ↔ DGI Tax Group (A–P).")},
            ],
        },
        {
            "label": _("Master Data"),
            "icon": "fa fa-database",
            "items": [
                {"type": "doctype", "name": "DGI Reference Data",
                 "description": _("Synced dictionaries from /api/info/*.")},
            ],
        },
        {
            "label": _("Monitoring"),
            "icon": "fa fa-list-alt",
            "items": [
                {"type": "doctype", "name": "DGI Invoice Log",
                 "description": _("Full HTTP request/response audit trail.")},
                {"type": "doctype", "name": "DGI Pending Invoice",
                 "description": _("Retry queue & certification results.")},
            ],
        },
    ]
