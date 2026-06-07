frappe.ui.form.on("DGI Point of Sale", {
    refresh(frm) {
        frm.add_custom_button(__("Rafraichir depuis la DGI (e-MCF)"), () => {
            frappe.dom.freeze(__("Synchronisation des points de vente..."));
            frappe.call({
                method: "dgi_compliance.edef.sync.refresh_points_of_sale",
                callback: (r) => {
                    frappe.dom.unfreeze();
                    const m = r.message || {};
                    frappe.show_alert({ message: __("{0} point(s) de vente synchronise(s)", [m.count || 0]), indicator: m.errors ? "orange" : "green" });
                    frm.reload_doc();
                },
                error: () => frappe.dom.unfreeze(),
            });
        }, __("DGI"));
    },

    is_sales_location(frm) {
        // Reflect Matrix A immediately in the form.
        const req = frm.doc.is_sales_location ? "Mandatory" : "Optional";
        frm.set_value("nid_requirement", req);
        frm.set_value("token_requirement", req);
    },
});
