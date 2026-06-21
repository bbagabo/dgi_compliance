frappe.ui.form.on("Sales Invoice", {
    refresh(frm) {
        // --- Draft: manual normalization (unlocks posting) ---
        if (frm.doc.docstatus === 0 && !frm.is_new() && frm.doc.custom_dgi_status !== "Normalized") {
            frm.add_custom_button(__("Normaliser (DGI)"), () => {
                frappe.dom.freeze(__("Normalisation DGI en cours..."));
                frappe.call({
                    method: "dgi_compliance.edef.tasks.normalize_sales_invoice",
                    args: { invoice: frm.doc.name },
                    callback: (r) => {
                        frappe.dom.unfreeze();
                        const res = r.message || {};
                        if (res.ok && (res.stage === "normalized" || res.stage === "pending")) {
                            frappe.show_alert({ message: __("Normalisation: {0}", [res.code_def || res.stage]), indicator: "green" });
                        } else {
                            frappe.msgprint({ title: __("Echec normalisation"), message: __("Etape: {0}", [res.stage || "?"]), indicator: "red" });
                        }
                        frm.reload_doc();
                    },
                    error: () => frappe.dom.unfreeze(),
                });
            }, __("DGI"));
        }

        // --- Draft status banner ---
        if (frm.doc.docstatus === 0 && frm.doc.custom_dgi_status && frm.doc.custom_dgi_status !== "Normalized") {
            frm.dashboard.set_headline_alert(
                __("Facture en attente de normalisation DGI - le postage est bloque tant qu'elle n'est pas normalisee."),
                "orange"
            );
        }

        // --- Submitted + Error: retry ---
        if (frm.doc.docstatus === 1 && frm.doc.custom_dgi_status === "Error") {
            frm.add_custom_button(__("Re-tenter la normalisation DGI"), () => {
                frappe.dom.freeze(__("Normalisation DGI en cours..."));
                frappe.call({
                    method: "dgi_compliance.edef.tasks.retry_normalization",
                    args: { invoice: frm.doc.name },
                    callback: (r) => {
                        frappe.dom.unfreeze();
                        const res = r.message || {};
                        if (res.ok) {
                            frappe.show_alert({ message: __("Normalisation reussie ({0})", [res.code_def || res.stage]), indicator: "green" });
                        } else {
                            frappe.msgprint({ title: __("Echec normalisation"), message: __("Etape: {0}", [res.stage || "?"]), indicator: "red" });
                        }
                        frm.reload_doc();
                    },
                    error: () => frappe.dom.unfreeze(),
                });
            }, __("DGI"));
        }
    },
});
