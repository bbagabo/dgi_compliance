from frappe import _


def get_data():
    return [
        {
            "module_name": "DGI Compliance",
            "category": "Modules",
            "label": _("DGI Compliance"),
            "color": "#cd5c5c",
            "icon": "octicon octicon-shield",
            "type": "module",
            "description": _("DGI RDC e-DEF (Facture Normalisée) integration."),
        }
    ]
