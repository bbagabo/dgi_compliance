// DGI Compliance - Sales Order: informational DGI invoice type (v3.6).
// FV by default; FT/ET only for an explicit prepayment; FA/EA never apply to an order.
frappe.ui.form.on("Sales Order", {
    setup(frm) {
        frm.set_query("dgi_invoice_type", () => ({
            filters: { name: ["in", ["FV", "FT", "EV", "ET"]] },
        }));
    },

    onload(frm) {
        if (frm.is_new() && !frm.doc.dgi_invoice_type) {
            frm.set_value("dgi_invoice_type", "FV");
        }
    },
});
