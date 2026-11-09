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
        // v5.4.0: reference sequentielle (Ref. PDV) of this point de vente
        if (frm.doc.sequence_code) {
            frm.add_custom_button(__("Registre des references"), () => frappe.set_route("List", "DGI Sequence Entry",
                { point_of_sale: frm.doc.name }), __("Reference"));
            frm.add_custom_button(__("Controler la serie"), () => frappe.prompt(
                { fieldname: "year", fieldtype: "Int", label: __("Annee"), reqd: 1, default: new Date().getFullYear() },
                (v) => frappe.call({ method: "dgi_compliance.fiscal.sequence.check_sequence",
                    args: { point_of_sale: frm.doc.name, year: v.year }, callback: (r) => {
                        const m = r.message || {};
                        const esc = frappe.utils.escape_html;
                        frappe.msgprint({ title: __("Serie {0}-{1}", [esc(m.code || ""), m.year]), indicator: m.ok ? "green" : "red",
                            message: `<p>${__("{0} reference(s), de {1} a {2} ; compteur : {3}.", [m.count || 0, esc(m.first || "-"), esc(m.last || "-"), m.counter || 0])}</p>
                                <p>${m.ok ? __("Serie continue, sans doublon, identique aux factures.")
                                    : __("Trous : {0} - doublons : {1} - differences avec la facture : {2}", [esc((m.gaps || []).join(", ") || "-"), esc((m.duplicates || []).join(", ") || "-"), esc((m.mismatches || []).join(", ") || "-")])}</p>` });
                    } }), __("Controler la serie"), __("Controler")), __("Reference"));
        }
        if (frm.doc.mcf_device) {
            // v5.3.2: "Statut MCF" block (like "Statut & Synchro" of the e-MCF) and a connection test from this PC
            dgi_pvf_mcf_status(frm);
            if (frappe.user.has_role(["DGI Caisse MCF", "DGI Fiscal Manager", "System Manager"])) {
                frm.add_custom_button(__("Tester la connexion (sur ce PC)"), () => frappe.require(
                    ["/assets/dgi_compliance/js/dgi_mcf.js", "/assets/dgi_compliance/js/dgi_mcf_desk.js"],
                    () => DGIMCFDesk.test({ nid: frm.doc.mcf_device, origin: __("Point de vente {0}", [frm.doc.name]), stay: true,
                        on_done: () => dgi_pvf_mcf_status(frm) })), __("MCF"));
            }
            frm.add_custom_button(__("Etat du MCF"), () => frappe.call({
                method: "dgi_compliance.fiscal.api.device_status", args: { point_of_sale: frm.doc.name },
                callback: (r) => {
                    const m = r.message || {};
                    frappe.require(["/assets/dgi_compliance/js/dgi_mcf_desk.js"], () => DGIMCFDesk.load_status({ point_of_sale: frm.doc.name })
                        .then((s) => frappe.msgprint({ title: __("MCF {0}", [m.nid || ""]), indicator: s.indicator || "gray", wide: true,
                            message: DGIMCFDesk.status_html(s) + `<p class="text-muted small" style="margin-top:8px">${frappe.utils.escape_html(
                                __("Operations en cours sur ce MCF : {0} - transactions non envoyees a la DGI (dernier heartbeat) : {1}",
                                    [m.operations_en_cours || 0, m.transactions_en_attente_dgi ?? "-"]))}</p>` })));
                } }), __("MCF"));
            frm.add_custom_button(__("Ouvrir la page Poste MCF"), () => frappe.set_route("dgi-mcf-station"), __("MCF"));
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

// v5.3.2: consolidated state of the MCF of this point de vente (no call to the MCF itself)
function dgi_pvf_mcf_status(frm) {
    const f = frm.fields_dict.mcf_status_html;
    if (!f || !frm.doc.mcf_device) return;
    frappe.require(["/assets/dgi_compliance/js/dgi_mcf_desk.js"], () => {
        DGIMCFDesk.load_status({ point_of_sale: frm.doc.name })
            .then((s) => f.$wrapper.html(DGIMCFDesk.status_html(s)))
            .catch(() => f.$wrapper.html(""));
    });
}
