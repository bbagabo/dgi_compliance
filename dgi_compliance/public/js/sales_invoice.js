frappe.ui.form.on("Sales Invoice", {
    setup(frm) {
        // Restrict the DGI invoice type to the types valid for the current context:
        //   return / credit note  -> FA, EA only
        //   otherwise              -> FV, FT, EV, ET only
        frm.set_query("dgi_invoice_type", () => {
            const allowed = frm.doc.is_return ? ["FA", "EA"] : ["FV", "FT", "EV", "ET"];
            return { filters: { name: ["in", allowed] } };
        });
    },

    is_return(frm) {
        if (frm.doc.is_return) {
            // Auto-select FA/EA as soon as the invoice is flagged as a return.
            const want = frm.doc.custom_dgi_export ? "EA" : "FA";
            if (frm.doc.dgi_invoice_type !== want) frm.set_value("dgi_invoice_type", want);
        } else if (["FA", "EA"].includes(frm.doc.dgi_invoice_type)) {
            // Leaving return mode: clear FA/EA so the type re-derives (FV/EV) or is re-picked.
            frm.set_value("dgi_invoice_type", "");
        }
        dgi_apply_return_visibility(frm);
    },

    custom_dgi_export(frm) {
        if (frm.doc.is_return) {
            frm.set_value("dgi_invoice_type", frm.doc.custom_dgi_export ? "EA" : "FA");
        }
    },

    refresh(frm) {
        dgi_apply_return_visibility(frm);

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

// Show the "Nature de l'avoir" / origin-reference fields only for returns (or explicit FA/EA).
// The fields also carry depends_on server-side; this keeps the UI in sync on toggle.
function dgi_apply_return_visibility(frm) {
    const show = !!frm.doc.is_return || ["FA", "EA"].includes(frm.doc.dgi_invoice_type);
    ["custom_dgi_reference", "custom_dgi_reference_type", "custom_dgi_reference_desc"].forEach((f) => {
        frm.toggle_display(f, show);
    });
}
