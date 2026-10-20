"""Customer helpers (v5.0.4).

ERPNext creates the customer's primary address when a new Customer carries ``address_line1``
(erpnext/selling/doctype/customer/customer.py, ``make_address``: City and Country mandatory).

The quick-entry dialog keeps the country in the alias field ``country_address`` and maps it to
``country`` only when the customer is saved from the dialog itself
(erpnext/public/js/utils/contact_address_quick_entry.js, ``insert``). With "Modifier le formulaire
complet" the alias is carried to the full form unchanged, and the full form has no country
field: saving fails with "Les champs suivants sont obligatoires pour créer une adresse : Country".

This hook (Customer ``before_insert``) restores the mapping. When no country was typed at all,
the default country of the site (System Settings, else the default company) is used and the user
is told to check the address.
No ERPNext file is modified.
"""
import frappe
from frappe import _


def _default_country():
    country = frappe.db.get_single_value("System Settings", "country")
    if not country:
        company = frappe.defaults.get_defaults().get("company")
        if company:
            country = frappe.db.get_value("Company", company, "country")
    return country


def map_quick_entry_address_country(doc, method=None):
    if not doc.get("address_line1") or doc.get("country"):
        return
    country = doc.get("country_address")
    if not country:
        country = _default_country()
        if country:
            frappe.msgprint(
                _("Pays de l'adresse non renseigné : {0} utilisé par défaut. Vérifiez l'adresse du client.").format(
                    frappe.bold(country)),
                indicator="orange", alert=True)
    if country:
        doc.country = country
