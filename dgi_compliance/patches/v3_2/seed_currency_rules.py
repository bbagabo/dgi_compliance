"""v3.2 - seed the default DGI currency matrix (CDF, USD) for existing installs. Insert-only."""


def execute():
    from dgi_compliance.edef.seed import seed_currency_rules
    seed_currency_rules()
