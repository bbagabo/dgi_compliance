"""Test data for the dgi_compliance integration tests (idempotent: safe to run several times).

Requires a site where the ERPNext setup wizard created a company in CDF. The company name can
be given with the environment variable DGI_TEST_COMPANY (default "DGI Test SARL").
"""
import os

import frappe
from frappe.utils import nowdate

COMPANY = os.environ.get("DGI_TEST_COMPANY", "DGI Test SARL")
TOKEN = "TEST-TOKEN-NOT-A-SECRET"
USD_RATE = 2850.0


def abbr():
    return frappe.get_cached_value("Company", COMPANY, "abbr")


def acc(name):
    return f"{name} - {abbr()}"


def _get_or_insert(doc):
    name = doc.get("name") or doc.get("account_name") or doc.get("title")
    if doc["doctype"] == "Account":
        existing = frappe.db.get_value("Account", {"account_name": doc["account_name"], "company": doc["company"]})
    elif doc["doctype"] == "Item":
        existing = frappe.db.exists("Item", doc["item_code"])
    elif doc["doctype"] == "Customer":
        existing = frappe.db.exists("Customer", {"customer_name": doc["customer_name"]})
    elif doc["doctype"] in ("Item Tax Template", "Sales Taxes and Charges Template"):
        existing = frappe.db.exists(doc["doctype"], {"title": doc["title"], "company": doc["company"]})
    else:
        existing = frappe.db.exists(doc["doctype"], name) if name else None
    if existing:
        return frappe.get_doc(doc["doctype"], existing)
    d = frappe.get_doc(doc)
    d.insert(ignore_permissions=True)
    return d


VAT = {"16": "TVA 16%", "5": "TVA 5%", "8": "TVA 8%", "1": "TVA 1%"}

# item tax template title -> (DGI group, {vat account rate})
ITEM_TAX = {
    "DGI B 16": ("B", {"16": 16}),
    "DGI C 5": ("C", {"5": 5}),
    "DGI Q 8": ("Q", {"8": 8}),
    "DGI O 1": ("O", {"1": 1}),
    "DGI A Exonere": ("A", {"16": 0}),
    "DGI D Derogatoire": ("D", {"16": 0}),
    "DGI E Export": ("E", {"16": 0}),
    "DGI S Hors champ": ("S", {"16": 0}),
}

ITEMS = [
    # code, name, item tax template, stock item, dgi item type, specific tax, hors champ
    ("DGI-B-LAIT", "Lait en poudre 400g", "DGI B 16", 1, "BIE", None, 0),
    ("DGI-B-SERV", "Prestation de maintenance", "DGI B 16", 0, "SER", None, 0),
    ("DGI-C-FARINE", "Farine de mais 25kg", "DGI C 5", 1, "BIE", None, 0),
    ("DGI-Q-BOISSON", "Boisson gazeuse 33cl", "DGI Q 8", 1, "BIE", None, 0),
    ("DGI-O-RIZ", "Riz local 50kg", "DGI O 1", 1, "BIE", None, 0),
    ("DGI-A-EAU", "Eau minerale 1,5L", "DGI A Exonere", 1, "BIE", None, 0),
    ("DGI-A-HC", "Indemnite hors champ", "DGI A Exonere", 0, "SER", None, 1),
    ("DGI-D-EQUIP", "Equipement (regime derogatoire)", "DGI D Derogatoire", 1, "BIE", None, 0),
    ("DGI-E-CAFE", "Cafe vert export", "DGI E Export", 1, "BIE", None, 0),
    ("DGI-TS-BIERE", "Biere 65cl (taxe specifique)", "DGI B 16", 1, "BIE", "230", 0),
    ("DGI-B-VRAC", "Sucre en vrac (kg)", "DGI B 16", 1, "BIE", None, 0),
]

CUSTOMERS = [
    # name, native type, DGI type, NIF
    ("CLIENT PM SARL", "Company", "PM", "A1234567B"),
    ("Jean Particulier", "Individual", "PP", ""),
    ("Marie Commercante", "Individual", "PC", "A7654321C"),
    ("Cabinet Avocat Liberal", "Individual", "PL", "A1112223D"),
    ("Ambassade de Test", "Company", "AO", ""),
    ("CLIENT USD SARL", "Company", "PM", "A9988776E"),
]


def setup_all():
    if not frappe.db.exists("UOM", "Kg"):
        frappe.get_doc({"doctype": "UOM", "uom_name": "Kg"}).insert(ignore_permissions=True)
    frappe.db.set_value("UOM", "Kg", "must_be_whole_number", 0)
    company = frappe.get_doc("Company", COMPANY)
    company.db_set({"dgi_rccm": "CD/KIN/RCCM/14-B4143", "dgi_id_nat": "01-83-N12345A",
                    "phone_no": "+243815105198", "email": "facturation@dgitest.cd", "tax_id": "A0700190Q"})
    _company_address()
    for rate, name in VAT.items():
        _get_or_insert({"doctype": "Account", "account_name": name, "company": COMPANY,
                        "parent_account": acc("Duties and Taxes"), "account_type": "Tax", "tax_rate": float(rate)})
    _get_or_insert({"doctype": "Account", "account_name": "Debtors USD", "company": COMPANY,
                    "parent_account": acc("Accounts Receivable"), "account_type": "Receivable",
                    "account_currency": "USD"})
    for title, (_g, rates) in ITEM_TAX.items():
        _get_or_insert({"doctype": "Item Tax Template", "title": title, "company": COMPANY,
                        "taxes": [{"tax_type": acc(VAT[k]), "tax_rate": v} for k, v in rates.items()]})
    for title, inclusive in (("DGI TVA HT", 0), ("DGI TVA TTC", 1)):
        _get_or_insert({"doctype": "Sales Taxes and Charges Template", "title": title, "company": COMPANY,
                        "taxes": [{"charge_type": "On Net Total", "account_head": acc(VAT[k]), "rate": 0,
                                   "description": VAT[k], "included_in_print_rate": inclusive,
                                   "cost_center": acc("Main")} for k in VAT]})
    _get_or_insert({"doctype": "Sales Taxes and Charges Template", "title": "TVA 16 simple", "company": COMPANY,
                    "taxes": [{"charge_type": "On Net Total", "account_head": acc("TVA 16%"), "rate": 16,
                               "description": "TVA 16%", "cost_center": acc("Main")}]})
    if not frappe.db.exists("Price List", "Standard Selling USD"):
        frappe.get_doc({"doctype": "Price List", "price_list_name": "Standard Selling USD", "currency": "USD",
                        "selling": 1, "enabled": 1}).insert(ignore_permissions=True)
    if not frappe.db.exists("Currency Exchange", {"from_currency": "USD", "to_currency": "CDF", "date": nowdate()}):
        frappe.get_doc({"doctype": "Currency Exchange", "from_currency": "USD", "to_currency": "CDF",
                        "exchange_rate": USD_RATE, "date": nowdate(), "for_selling": 1}).insert(ignore_permissions=True)
    for code, name, itt, stock, itype, ts, hc in ITEMS:
        uom = "Kg" if code == "DGI-B-VRAC" else "Nos"
        item = _get_or_insert({"doctype": "Item", "item_code": code, "item_name": name, "item_group": "Products",
                               "stock_uom": uom, "is_stock_item": stock, "dgi_item_type": itype,
                               "dgi_specific_tax": ts, "dgi_hors_champ": hc,
                               "taxes": [{"item_tax_template": _itt(itt)}]})
        item.db_set({"dgi_item_type": itype, "dgi_specific_tax": ts, "dgi_hors_champ": hc})
    for name, native, dgi_type, nif in CUSTOMERS:
        cust = _get_or_insert({"doctype": "Customer", "customer_name": name, "customer_type": native,
                               "customer_group": "Commercial", "territory": "Congo, The Democratic Republic of the",
                               "dgi_customer_type": dgi_type, "tax_id": nif or None})
        cust.db_set({"dgi_customer_type": dgi_type, "tax_id": nif or None})
    for mop in ("Cash", "Wire Transfer", "Credit Card", "Cheque", "Bank Draft"):
        m = frappe.get_doc("Mode of Payment", mop)
        if not any(a.company == COMPANY for a in m.accounts):
            m.append("accounts", {"company": COMPANY, "default_account": acc("Cash")})
            m.save(ignore_permissions=True)
    _settings()
    frappe.db.commit()


def _itt(title):
    return frappe.db.get_value("Item Tax Template", {"title": title, "company": COMPANY})


def _stt(title):
    return frappe.db.get_value("Sales Taxes and Charges Template", {"title": title, "company": COMPANY})


def _company_address():
    if frappe.db.exists("Address", {"address_title": "DGI Test Siege"}):
        return
    frappe.get_doc({"doctype": "Address", "address_title": "DGI Test Siege", "address_type": "Billing",
                    "address_line1": "19284/4b, Avenue des Poids Lourds", "address_line2": "Kingabwa/Limete - B.P. 7263",
                    "city": "Kinshasa", "country": "Congo, The Democratic Republic of the",
                    "is_your_company_address": 1,
                    "links": [{"link_doctype": "Company", "link_name": COMPANY}]}).insert(ignore_permissions=True)


def _settings():
    s = frappe.get_single("DGI Compliance Settings")
    s.enabled = 1
    s.communication_mode = "E-MCF (API)"
    s.environment = "Test"
    s.base_url_test = "https://developper.dgirdc.cd/edef"
    s.token = TOKEN
    s.isf = "TST-ISF-01"
    s.default_nif = "A0700190Q"
    s.auto_normalize = 1
    s.emcf_check_action = "Bloquer"
    s.emcf_check_tolerance = 1
    s.dgi_total_tolerance = 1
    s.usd_rate_control = "Bloquer"
    s.usd_rate_max_deviation = 1
    s.default_payment_type = "ESPECES"
    s.currency_enforcement = "Enforce"
    s.validation_enforcement = "Enforce"
    s.set("payment_mode_map", [])
    for mop, ptype in (("Cash", "ESPECES"), ("Wire Transfer", "VIREMENT"), ("Credit Card", "CARTEBANCAIRE"),
                       ("Cheque", "CHEQUES")):
        s.append("payment_mode_map", {"erpnext_mode_of_payment": mop, "edef_payment_type": ptype})
    s.set("tax_group_map", [])
    for title, (group, _r) in ITEM_TAX.items():
        s.append("tax_group_map", {"item_tax_template": _itt(title), "edef_tax_group": group, "priority": 10})
    s.append("tax_group_map", {"sales_taxes_template": _stt("TVA 16 simple"), "edef_tax_group": "B", "priority": 20})
    s.flags.ignore_mandatory = True
    s.save(ignore_permissions=True)


def make_invoice(customer, lines, template="DGI TVA HT", currency="CDF", rate=None, submit=False,
                 save=True, **extra):
    """lines: [(item_code, qty, price, discount_percentage)]."""
    si = frappe.new_doc("Sales Invoice")
    si.company = COMPANY
    si.customer = frappe.db.get_value("Customer", {"customer_name": customer}) or customer
    si.posting_date = nowdate()
    si.due_date = nowdate()
    si.currency = currency
    if currency != "CDF":
        si.conversion_rate = rate or USD_RATE
        si.debit_to = acc("Debtors USD")
        si.selling_price_list = "Standard Selling USD"
        si.price_list_currency = "USD"
        si.plc_conversion_rate = rate or USD_RATE
    else:
        si.selling_price_list = "Standard Selling"
    for ln in lines:
        code, qty, price = ln[0], ln[1], ln[2]
        disc = ln[3] if len(ln) > 3 else 0
        si.append("items", {"item_code": code, "qty": qty, "price_list_rate": price, "rate": 0 if disc else price,
                            "discount_percentage": disc, "income_account": acc("Sales"),
                            "cost_center": acc("Main"),
                            "item_tax_template": frappe.db.get_value("Item Tax", {"parent": code}, "item_tax_template")})
    if template:
        si.taxes_and_charges = _stt(template)
        for t in frappe.get_doc("Sales Taxes and Charges Template", si.taxes_and_charges).taxes:
            si.append("taxes", {k: t.get(k) for k in ("charge_type", "account_head", "rate", "description",
                                                       "included_in_print_rate", "cost_center")})
    si.update(extra)
    if save:
        si.insert(ignore_permissions=True)
    if submit:
        si.submit()
    return si
