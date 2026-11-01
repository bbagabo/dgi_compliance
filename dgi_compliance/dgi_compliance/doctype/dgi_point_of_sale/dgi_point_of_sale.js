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

frappe.ui.form.on("DGI Point of Sale", {
    refresh(frm) {
        if (frm.is_new()) return;
        frm.add_custom_button(__("Changer de canal"), () => {
            frappe.new_doc("DGI Channel Switch", { point_of_sale: frm.doc.name });
        }, __("Canal"));
        if (frm.doc.mcf_device) {
            frm.add_custom_button(__("Etat du MCF"), () => frappe.call({
                method: "dgi_compliance.fiscal.api.device_status", args: { point_of_sale: frm.doc.name },
                callback: (r) => {
                    const m = r.message || {};
                    frappe.msgprint({ title: __("MCF {0}", [m.nid || ""]), indicator: m.gateway_online ? "green" : "red",
                        message: `<table class="table table-bordered">${Object.entries(m).map(([k, v]) =>
                            `<tr><td>${frappe.utils.escape_html(k)}</td><td>${frappe.utils.escape_html(String(v ?? ""))}</td></tr>`).join("")}</table>` });
                } }), __("MCF"));
            frm.add_custom_button(__("Relancer la synchronisation DGI (C2h RESTART)"), () => frappe.confirm(
                __("Le MCF va redemarrer pour relancer sa synchronisation avec la DGI. Continuer ?"),
                () => frappe.call({ method: "dgi_compliance.fiscal.api.request_resync", args: { point_of_sale: frm.doc.name },
                    callback: () => frappe.show_alert({ message: __("Demande transmise a la passerelle"), indicator: "blue" }) })),
                __("MCF"));
        }
        frm.add_custom_button(__("Tester le jeton e-MCF"), () => frappe.call({
            method: "dgi_compliance.dgi_compliance.doctype.dgi_point_of_sale.dgi_point_of_sale.check_emcf_token",
            args: { point_of_sale: frm.doc.name }, freeze: true,
            callback: (r) => {
                const m = r.message || {};
                frappe.msgprint({ title: __("Test du jeton e-MCF (sans effet fiscal)"),
                    indicator: m.result === "OK" ? "green" : (m.result === "Different" ? "red" : "orange"),
                    message: frappe.utils.escape_html(m.message || "") });
                frm.reload_doc();
            } }), __("DGI"));
        frm.add_custom_button(__("Journal des transmissions"), () =>
            frappe.set_route("List", "DGI Fiscal Operation", { point_of_sale: frm.doc.name }), __("DGI"));
        if (frm.doc.fallback_since) {
            frm.dashboard.set_headline_alert(__("Secours actif depuis {0} : {1}", [
                frappe.datetime.str_to_user(frm.doc.fallback_since), frappe.utils.escape_html(frm.doc.fallback_reason || "")]), "orange");
            frm.add_custom_button(__("Revenir au canal prioritaire maintenant"), () => frappe.confirm(
                __("Le secours est ferme. S'il allait vers le MCF, les factures encore en file pour le MCF (jamais prises par le poste) repartent sur l'e-MCF. Continuer ?"),
                () => frappe.call({ method: "dgi_compliance.fiscal.fallback.close_fallback", args: { point_of_sale: frm.doc.name },
                    callback: (r) => { frappe.show_alert({ message: __("Secours ferme ; {0} facture(s) renvoyee(s) a l'e-MCF", [(r.message || {}).moved || 0]), indicator: "green" }); frm.reload_doc(); } })),
                __("Canal"));
        }
        if (frm.doc.token_check === "Different") {
            frm.dashboard.add_comment(__("Le jeton e-MCF designe l'e-MCF {0}, pas le NIM de ce point de vente : l'e-MCF n'est pas utilise.", [frappe.utils.escape_html(frm.doc.token_nim || "?")]), "red", true);
        }
        if (frm.doc.active_channel) {
            const fb = frm.doc.fallback_since ? " " + __("(secours actif)") : "";
            frm.page.set_indicator(__("Canal en vigueur : {0}", [frm.doc.active_channel]) + (frm.doc.forced_switch ? " " + __("(bascule manuelle)") : "") + fb,
                frm.doc.fallback_since ? "orange" : (frm.doc.active_channel === "MCF" ? "blue" : "green"));
        }
    },
});
