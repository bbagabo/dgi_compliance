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

    onload(frm) {
        // v3.6: default the DGI type on new documents so the field is never left empty.
        if (frm.is_new() && !frm.doc.dgi_invoice_type) {
            const dft = frm.doc.is_return
                ? (frm.doc.custom_dgi_export ? "EA" : "FA")
                : (frm.doc.custom_dgi_export ? "EV" : "FV");
            frm.set_value("dgi_invoice_type", dft);
        }
    },

    is_return(frm) {
        if (frm.doc.is_return) {
            // Auto-select FA/EA as soon as the invoice is flagged as a return.
            const want = frm.doc.custom_dgi_export ? "EA" : "FA";
            if (frm.doc.dgi_invoice_type !== want) frm.set_value("dgi_invoice_type", want);
        } else if (["FA", "EA"].includes(frm.doc.dgi_invoice_type)) {
            // Leaving return mode: fall back to the default sale type (v3.6: never left empty).
            frm.set_value("dgi_invoice_type", frm.doc.custom_dgi_export ? "EV" : "FV");
        }
        dgi_apply_return_visibility(frm);
    },

    custom_dgi_export(frm) {
        if (frm.doc.is_return) {
            frm.set_value("dgi_invoice_type", frm.doc.custom_dgi_export ? "EA" : "FA");
        } else if (["FV", "EV"].includes(frm.doc.dgi_invoice_type)) {
            frm.set_value("dgi_invoice_type", frm.doc.custom_dgi_export ? "EV" : "FV");
        }
    },

    refresh(frm) {
        dgi_apply_return_visibility(frm);

        // --- Draft: manual normalization (unlocks posting) ---
        if (frm.doc.docstatus === 0 && !frm.is_new() && frm.doc.custom_dgi_status !== "Normalized") {
            frm.add_custom_button(__("Normaliser (DGI)"), () => {
                const run = () => frappe.call({
                    method: "dgi_compliance.edef.tasks.normalize_sales_invoice",
                    args: { invoice: frm.doc.name },
                    freeze: true,
                    freeze_message: __("Normalisation DGI en cours..."),
                    callback: (r) => {
                        const res = r.message || {};
                        if (res.ok && res.stage === "queued-mcf") {
                            frappe.show_alert({ message: __("Envoye a l'agent MCF local (job {0})", [res.job || "?"]), indicator: "blue" });
                        } else if (res.ok) {
                            frappe.show_alert({ message: __("Facture normalisee: {0}", [res.code_def || res.stage]), indicator: "green" });
                        }
                        frm.reload_doc();
                    },
                });
                if (frm.is_dirty()) {
                    frm.save().then(run);
                } else {
                    run();
                }
            }, __("DGI"));
        }

        // --- Uncertain confirmation: release after checking the DGI portal (System Manager) ---
        if (frm.doc.docstatus === 0 && (frm.doc.custom_dgi_error || "").startsWith("CONFIRMATION INCERTAINE")
                && frappe.user.has_role("System Manager")) {
            frm.add_custom_button(__("Liberer la confirmation incertaine"), () => {
                frappe.confirm(
                    __("Confirmez-vous avoir verifie sur le portail de verification DGI que cette facture N'Y FIGURE PAS ?"),
                    () => frappe.call({
                        method: "dgi_compliance.edef.tasks.clear_uncertain_confirmation",
                        args: { invoice: frm.doc.name, verified_not_registered: 1 },
                        callback: () => frm.reload_doc(),
                    })
                );
            }, __("DGI"));
        }

        // --- Draft status banner ---
        if (frm.doc.docstatus === 0 && frm.doc.custom_dgi_status === "Normalized") {
            frm.dashboard.set_headline_alert(
                __("Facture normalisee par la DGI ({0}): son contenu fiscal est fige. Vous pouvez la soumettre.",
                    [frm.doc.custom_dgi_code_def || ""]), "green");
        } else if (frm.doc.docstatus === 0 && frm.doc.custom_dgi_status) {
            frm.dashboard.set_headline_alert(
                __("Facture en attente de normalisation DGI: la soumission est impossible tant qu'elle n'est pas normalisee.")
                + (frm.doc.custom_dgi_error ? "<br><b>" + frappe.utils.escape_html(frm.doc.custom_dgi_error) + "</b>" : ""),
                frm.doc.custom_dgi_status === "Error" ? "red" : "orange"
            );
        }

        // --- Submitted + Error: retry ---
        if (frm.doc.docstatus === 1 && frm.doc.custom_dgi_status === "Error") {
            frm.add_custom_button(__("Re-tenter la normalisation DGI"), () => {
                frappe.call({
                    method: "dgi_compliance.edef.tasks.retry_normalization",
                    args: { invoice: frm.doc.name },
                    freeze: true,
                    freeze_message: __("Normalisation DGI en cours..."),
                    callback: (r) => {
                        const res = r.message || {};
                        if (res.ok) {
                            frappe.show_alert({ message: __("Normalisation reussie ({0})", [res.code_def || res.stage]), indicator: "green" });
                        } else {
                            frappe.msgprint({ title: __("Echec normalisation"), message: res.message || __("Etape: {0}", [res.stage || "?"]), indicator: "red" });
                        }
                        frm.reload_doc();
                    },
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
