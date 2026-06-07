"""v2.0 upgrade: create the dedicated mapping catalogs (Item/Invoice/Customer Type, Tax Group)
and seed the default validation matrices A-F. Idempotent and insert-only:

  * mapping rows are created only when missing (user edits preserved);
  * validation-matrix rows are seeded only when the table is still empty.
"""


def execute():
    from dgi_compliance.edef.seed import seed_mapping_doctypes, seed_validation_matrix
    seed_mapping_doctypes()
    seed_validation_matrix()
