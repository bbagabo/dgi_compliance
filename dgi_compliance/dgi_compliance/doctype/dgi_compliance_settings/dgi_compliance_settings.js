frappe.ui.form.on("DGI Compliance Settings", {
    refresh(frm) {
        const grp = __("DGI");

        const run = (method, args, freezeMsg, done) => {
            frappe.dom.freeze(freezeMsg);
            frappe.call({
                method, args: args || {},
                callback: (r) => { frappe.dom.unfreeze(); done && done(r.message || {}); },
                error: () => frappe.dom.unfreeze(),
            });
        };

        const refreshCatalog = (catalog, label) => {
            run("dgi_compliance.edef.sync.refresh_catalog", { catalog },
                __("Mise a jour: {0}...", [label]), (m) => {
                    frappe.show_alert({
                        message: __("{0}: {1} ligne(s) mises a jour", [label, m.count || 0]),
                        indicator: m.errors ? "orange" : "green",
                    });
                    if (m.errors) frappe.msgprint({ title: __("Erreurs"),
                        message: frappe.utils.escape_html((m.errors || []).join("; ")), indicator: "orange" });
                });
        };

        // ----- Test connection (live) -----
        frm.add_custom_button(__("Tester la connexion DGI"), () => {
            run("dgi_compliance.edef.sync.test_connection", {}, __("Test de connexion DGI..."), (m) => {
                const ok = m.ok && (m.service_status !== false);
                const lines = [
                    `<b>HTTP:</b> ${m.status_code || "-"}`,
                    `<b>Service:</b> ${m.service_status}`,
                    `<b>NIF:</b> ${frappe.utils.escape_html(m.nif || "-")}`,
                    `<b>NIM:</b> ${frappe.utils.escape_html(m.nim || "-")}`,
                    `<b>Version e-DEF:</b> ${frappe.utils.escape_html(m.version || "-")}`,
                    `<b>Jeton valide jusqu'au:</b> ${frappe.utils.escape_html(m.tokenValid || "-")}`,
                    `<b>Heure serveur:</b> ${frappe.utils.escape_html(m.serverDateTime || "-")}`,
                    `<b>Points de vente:</b> ${m.pos_count || 0}`,
                ];
                if (m.error) lines.push(`<b style="color:var(--red-500)">Erreur:</b> ${frappe.utils.escape_html(m.error)}`);
                frappe.msgprint({
                    title: ok ? __("Connexion DGI reussie") : __("Connexion DGI: probleme"),
                    message: lines.join("<br>"), indicator: ok ? "green" : "red",
                });
            });
        }, grp);

        // ----- Global sync (everything) -----
        frm.add_custom_button(__("Synchroniser TOUT"), () => {
            run("dgi_compliance.edef.sync.sync_now", {}, __("Synchronisation complete..."), (s) => {
                const lines = [
                    `Points de vente: ${s.emcf || 0}`, `Types client: ${s.clientTypes || 0}`,
                    `Types article: ${s.itemTypes || 0}`, `Types facture: ${s.invoiceTypes || 0}`,
                    `Types paiement: ${s.paymentTypes || 0}`, `Types reference: ${s.referenceTypes || 0}`,
                    `Groupes de taxe: ${s.taxGroups || 0}`, `Taux de change: ${s.currencyRates || 0}`,
                ];
                if (s.errors && s.errors.length) lines.push(`<b style="color:var(--red-500)">Erreurs:</b> ${frappe.utils.escape_html(s.errors.join("; "))}`);
                frappe.msgprint({ title: __("Synchronisation terminee"), message: lines.join("<br>"),
                    indicator: (s.errors && s.errors.length) ? "orange" : "green" });
                frm.reload_doc();
            });
        }, grp);

        // ----- Individual updates (one button each) -----
        frm.add_custom_button(__("MAJ Points de vente"), () => {
            run("dgi_compliance.edef.sync.refresh_points_of_sale", {}, __("Mise a jour des points de vente..."), (m) => {
                frappe.show_alert({ message: __("Points de vente: {0} mis a jour", [m.count || 0]),
                    indicator: m.errors ? "orange" : "green" });
                if (m.errors) frappe.msgprint({ title: __("Erreurs"),
                    message: frappe.utils.escape_html((m.errors || []).join("; ")), indicator: "orange" });
            });
        }, grp);
        frm.add_custom_button(__("MAJ Types d'article"), () => refreshCatalog("Item Type", __("Types d'article")), grp);
        frm.add_custom_button(__("MAJ Types de client"), () => refreshCatalog("Client Type", __("Types de client")), grp);
        frm.add_custom_button(__("MAJ Types de facture"), () => refreshCatalog("Invoice Type", __("Types de facture")), grp);
        frm.add_custom_button(__("MAJ Groupes de taxe"), () => refreshCatalog("Tax Group", __("Groupes de taxe")), grp);

        // ----- Logs -----
        frm.add_custom_button(__("Purger les logs DGI"), () => {
            frappe.confirm(__("Supprimer les DGI Exchange Log plus anciens que la retention configuree ?"), () => {
                run("dgi_compliance.edef.audit.purge_now", {}, __("Purge..."), (m) => {
                    frappe.show_alert({ message: __("{0} log(s) supprime(s)", [m.deleted || 0]), indicator: "green" });
                });
            });
        }, grp);
    },
});
