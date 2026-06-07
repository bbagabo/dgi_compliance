frappe.ui.form.on("DGI Compliance Settings", {
    refresh(frm) {
        frm.add_custom_button(__("Synchroniser les referentiels DGI"), () => {
            frappe.dom.freeze(__("Synchronisation depuis la DGI..."));
            frappe.call({
                method: "dgi_compliance.edef.sync.sync_now",
                callback: (r) => {
                    frappe.dom.unfreeze();
                    const s = r.message || {};
                    const lines = [
                        `Points de vente: ${s.emcf || 0}`, `Types client: ${s.clientTypes || 0}`,
                        `Types article: ${s.itemTypes || 0}`, `Types facture: ${s.invoiceTypes || 0}`,
                        `Types paiement: ${s.paymentTypes || 0}`, `Types reference: ${s.referenceTypes || 0}`,
                        `Groupes de taxe: ${s.taxGroups || 0}`, `Taux de change: ${s.currencyRates || 0}`,
                    ];
                    if (s.errors && s.errors.length) lines.push(`<b style="color:var(--red-500)">Erreurs:</b> ${frappe.utils.escape_html(s.errors.join("; "))}`);
                    frappe.msgprint({ title: __("Synchronisation terminee"), message: lines.join("<br>"), indicator: (s.errors && s.errors.length) ? "orange" : "green" });
                    frm.reload_doc();
                },
                error: () => frappe.dom.unfreeze(),
            });
        }, __("DGI"));

        frm.add_custom_button(__("Purger les logs DGI"), () => {
            frappe.confirm(__("Supprimer les DGI Exchange Log plus anciens que la retention configuree ?"), () => {
                frappe.call({
                    method: "dgi_compliance.edef.audit.purge_now",
                    callback: (r) => {
                        const n = (r.message || {}).deleted || 0;
                        frappe.show_alert({ message: __("{0} log(s) supprime(s)", [n]), indicator: "green" });
                    },
                });
            });
        }, __("DGI"));
    },
});
