# dgi_compliance (ERPNext v16, Frappe Cloud)

Custom Frappe app that normalises ERPNext Sales Invoices through the **DGI RDC e-MCF / e-DEF**
fiscal API and writes back the QR code and Code DEF/DGI. **Upgrade-safe**: it adds Custom Fields and
its own DocTypes only — it never edits ERPNext/Frappe core, so `bench update` / ERPNext v16 upgrades
remain clean.

What it does:
- On **Sales Invoice submit** → builds the e-DEF payload from real ERPNext fields, calls
  `POST /api/invoice`, then `PUT /confirm`, stores the fiscal elements on Custom Fields.
- On **Sales Invoice cancel** → calls `PUT /cancel`.
- **Scheduler** → monitors the JWT (`tokenValid`) and alerts before expiry (daily / weekly / monthly).
- All exchanges are logged (Error Log + optional `DGI Exchange Log`).

See `INSTALL_FRAPPE_CLOUD.md` for deployment and `TOKEN_MONITORING.md` for the scheduler.
