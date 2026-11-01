frappe.ui.form.on("DGI Fiscal Operation", {
    refresh(frm) {
        const colors = { Queued: "blue", Pending: "blue", Leased: "orange", Uncertain: "red", Checking: "orange", Normalized: "green",
                         Rejected: "red", Cancelled: "gray", Dispute: "red", Done: "green" };
        frm.page.set_indicator(__(frm.doc.status), colors[frm.doc.status] || "gray");
        if (frm.doc.status === "Queued") {
            frm.add_custom_button(__("Annuler (avant traitement)"), () => frappe.call({
                method: "dgi_compliance.fiscal.api.cancel_queued", args: { operation: frm.doc.name },
                callback: () => frm.reload_doc() }));
        }
        if (frm.doc.status === "Rejected" && frappe.user.has_role(["DGI Fiscal Manager", "System Manager"])) {
            const other = frm.doc.channel === "MCF" ? "e-MCF" : "MCF";
            frm.add_custom_button(__("Renvoyer sur l'autre canal ({0})", [other]), () => frappe.confirm(
                __("Le refus prouve que la facture n'a pas ete fiscalisee sur {0}. A utiliser seulement quand la cause tient a l'equipement, pas a la facture. Envoyer la facture sur {1} ?", [frm.doc.channel, other]),
                () => frappe.call({ method: "dgi_compliance.fiscal.registry.reroute_rejected", args: { operation: frm.doc.name },
                    freeze: true, callback: () => frm.reload_doc() })));
        }
        if (frm.doc.routing_explanation) {
            frm.dashboard.add_comment(frappe.utils.escape_html(frm.doc.routing_explanation), "blue", true);
        }
        if (frm.doc.status === "Dispute" && frappe.user.has_role(["DGI Fiscal Manager", "System Manager"])) {
            frm.add_custom_button(__("Clore le litige : NON normalisee"), () => {
                frappe.prompt({ fieldname: "justification", fieldtype: "Small Text", reqd: 1,
                                label: __("Justification (verification sur le portail DGI)") },
                    (v) => frappe.call({ method: "dgi_compliance.fiscal.api.resolve_dispute",
                        args: { operation: frm.doc.name, decision: "Rejected", justification: v.justification },
                        callback: () => frm.reload_doc() }),
                    __("Litige"), __("Confirmer"));
            });
        }
    },
});
