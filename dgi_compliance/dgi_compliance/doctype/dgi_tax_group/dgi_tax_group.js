frappe.ui.form.on("DGI Tax Group", {
    refresh(frm) {
        frm.add_custom_button(__("Rafraichir depuis la DGI"), () => {
            frappe.dom.freeze(__("Synchronisation des groupes de taxe..."));
            frappe.call({
                method: "dgi_compliance.edef.sync.refresh_catalog",
                args: { catalog: "Tax Group" },
                callback: (r) => {
                    frappe.dom.unfreeze();
                    const m = r.message || {};
                    frappe.show_alert({ message: __("{0} groupe(s) synchronise(s)", [m.count || 0]), indicator: m.errors ? "orange" : "green" });
                    frm.reload_doc();
                },
                error: () => frappe.dom.unfreeze(),
            });
        }, __("DGI"));
    },
});
