def execute():
    # Seed static e-DEF catalogs (insert-only). Safe to run repeatedly.
    from dgi_compliance.edef.seed import seed_static_catalogs
    seed_static_catalogs()
