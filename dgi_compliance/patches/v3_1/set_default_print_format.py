"""v3.1.1 - set 'DGI Sales Invoice' as the default Print Format for Sales Invoice.

Upgrade-safe Property Setter (DocType-level default_print_format). No ERPNext core change.
Idempotent. Runs after the print format itself is synced by `migrate`.
"""


def execute():
    from dgi_compliance.edef.seed import set_default_print_format
    set_default_print_format()
