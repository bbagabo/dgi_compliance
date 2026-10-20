"""v5.0.5 - remove Matrix C (Invoice Type x Country / Pays de l'adresse client).

The invoice type (FV/FT/FA or EV/ET/EA) is no longer checked against the customer's country.
All Matrix C rows are deleted, whatever their name across versions. The other matrices
(D, E, F) are untouched. Idempotent.
"""
import frappe

from dgi_compliance.dgi_compliance.doctype.dgi_validation_matrix.dgi_validation_matrix import MATRIX_C_REMOVED


def execute():
    if not frappe.db.table_exists("DGI Validation Matrix"):
        return
    frappe.db.delete("DGI Validation Matrix", {"matrix_type": ["in", list(MATRIX_C_REMOVED)]})
    frappe.clear_cache(doctype="DGI Validation Matrix")
