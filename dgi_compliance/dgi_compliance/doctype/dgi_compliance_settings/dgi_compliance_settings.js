frappe.ui.form.on("DGI Compliance Settings", {
    refresh(frm) {
        // ---- v4.0: communication mode banner + MCF agent health ----
        if (frm.doc.communication_mode === "MCF Physique (USB)") {
            frm.dashboard.set_headline_alert(
                __("Mode MCF Physique (USB) actif - toutes les communications e-MCF (API) sont bloquees. La normalisation passe par l'agent MCF local."),
                "blue");
            frm.add_custom_button(__("Etat de l'agent MCF"), () => {
                const seen = frm.doc.mcf_agent_last_seen;
                const mins = seen ? moment().diff(moment(seen), "minutes") : null;
                frappe.msgprint({
                    title: __("Agent MCF"),
                    indicator: mins !== null && mins < 3 ? "green" : "red",
                    message: (seen
                        ? __("Dernier signe de vie: {0} (il y a {1} min)", [frappe.datetime.str_to_user(seen), mins])
                        : __("Aucun heartbeat recu - l'agent n'a jamais contacte ce site.")) +
                        "<br><pre style='white-space:pre-wrap'>" +
                        frappe.utils.escape_html(frm.doc.mcf_agent_status || "") + "</pre>",
                });
            }, __("MCF"));
            frm.add_custom_button(__("Jobs MCF"), () => frappe.set_route("List", "DGI MCF Job"), __("MCF"));
        } else if (frm.doc.communication_mode) {
            frm.dashboard.set_headline_alert(
                __("Mode E-MCF (API) actif - l'agent MCF physique est bloque (pull/push refuses)."),
                "green");
        }
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

        // ----- v5.0.0: statistical reports X / Z / A -----
        frm.add_custom_button(__("Rapports X / Z / A"), () => frappe.set_route("List", "DGI Rapport Fiscal"), __("Rapports"));
        frm.add_custom_button(__("Factures en attente de normalisation"),
            () => frappe.set_route("query-report", "Factures en attente de normalisation"), __("Rapports"));

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

        // ----- Diagnostic: unmapped tax templates (v3.5) -----
        frm.add_custom_button(__("Verifier le mapping taxes"), () => {
            run("dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings.check_tax_group_mapping",
                {}, __("Verification du mapping taxes..."), (m) => {
                    const itt = m.unmapped_item_tax_templates || [];
                    const stt = m.unmapped_sales_taxes_templates || [];
                    const esc = (s) => frappe.utils.escape_html(s);
                    const lines = [
                        `<b>${__("Mappings configures")}:</b> ${m.mapped_count || 0}`,
                        `<b>${__("Default Tax Group")}:</b> ${esc(m.default_tax_group || "A")}`,
                    ];
                    if (itt.length) lines.push(`<b>${__("Modeles de taxe d'article NON mappes")}:</b><br>&bull; ${itt.map(esc).join("<br>&bull; ")}`);
                    if (stt.length) lines.push(`<b>${__("Modeles Taxes et Frais de Vente NON mappes")}:</b><br>&bull; ${stt.map(esc).join("<br>&bull; ")}`);
                    if (!itt.length && !stt.length) lines.push(__("Tous les modeles de taxe actifs sont mappes."));
                    frappe.msgprint({
                        title: (itt.length || stt.length) ? __("Mapping taxes incomplet") : __("Mapping taxes OK"),
                        message: lines.join("<br><br>"),
                        indicator: (itt.length || stt.length) ? "orange" : "green",
                    });
                });
        }, grp);

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
