// DGI MCF Device - v5.3.0 : validation explicite d'un MCF detecte (livrable B section 7)
// v5.3.2 : bloc "Statut MCF" et "Tester la connexion (sur ce PC)" (C1h + C2h, sans effet fiscal)
frappe.ui.form.on("DGI MCF Device", {
    refresh(frm) {
        if (frm.is_new()) return;
        const colors = { Detecte: "orange", Declare: "orange", Actif: "green", "A revalider": "red", Suspendu: "gray", Retire: "gray" };
        frm.page.set_indicator(__(frm.doc.status), colors[frm.doc.status] || "gray");
        dgi_mcf_status_block(frm);
        if (frappe.user.has_role(["DGI Caisse MCF", "DGI Fiscal Manager", "System Manager"])) {
            frm.add_custom_button(__("Tester la connexion (sur ce PC)"), () => frappe.require(
                ["/assets/dgi_compliance/js/dgi_mcf.js", "/assets/dgi_compliance/js/dgi_mcf_desk.js"],
                () => DGIMCFDesk.test({ nid: frm.doc.name, origin: __("Fiche MCF"), stay: true,
                    on_done: () => frm.reload_doc() })));
        }
        frm.add_custom_button(__("Ouvrir la page Poste MCF"), () => frappe.set_route("dgi-mcf-station"), __("Poste"));
        if (!["Detecte", "Declare", "A revalider"].includes(frm.doc.status)) return;
        // v5.4.0: a DGI Approbateur validates too (another person than the one who proposed it)
        if (!frappe.user.has_role(["DGI Fiscal Manager", "System Manager", "DGI Approbateur"])) return;
        frm.add_custom_button(__("Valider ce MCF"), () => validate_dialog(frm), __("Validation"));
        frm.add_custom_button(__("Refuser"), () => frappe.prompt(
            { fieldname: "reason", fieldtype: "Small Text", reqd: 1, label: __("Motif du refus") },
            (v) => frappe.call({ method: "dgi_compliance.fiscal.registry.refuse_device",
                args: { nid: frm.doc.name, reason: v.reason }, callback: () => frm.reload_doc() }),
            __("Refuser le MCF"), __("Refuser")), __("Validation"));
        if (!frm.doc.last_mid) {
            frm.dashboard.add_comment(__("Ce MCF n'a jamais ete lu : branchez-le sur la caisse et lisez-le depuis la page Poste MCF avant de le valider."), "orange", true);
        }
    },
});

// v5.3.2: consolidated state (last test, poste, DGI connection) in the HTML field status_html
function dgi_mcf_status_block(frm) {
    const $w = frm.fields_dict.status_html && frm.fields_dict.status_html.$wrapper;
    if (!$w) return;
    $w.html(`<div class="text-muted small">${__("Chargement du statut...")}</div>`);
    frappe.require(["/assets/dgi_compliance/js/dgi_mcf_desk.js"], () => {
        DGIMCFDesk.load_status({ nid: frm.doc.name }).then((s) => {
            $w.html(DGIMCFDesk.status_html(s));
            if (s.can_create_station) {
                frm.add_custom_button(__("Creer le poste MCF de {0}", [s.point_of_sale]), () => frappe.xcall(
                    "dgi_compliance.fiscal.mcf_status.create_station", { point_of_sale: s.point_of_sale })
                    .then((r) => frappe.set_route("Form", "DGI Gateway", r.name)), __("Poste"));
            }
        }).catch(() => $w.html(""));
    });
}

function validate_dialog(frm) {
    const esc = frappe.utils.escape_html;
    const d = new frappe.ui.Dialog({
        title: __("Valider le MCF {0}", [frm.doc.name]),
        size: "large",
        fields: [
            { fieldname: "point_of_sale", fieldtype: "Link", options: "DGI Point of Sale", reqd: 1,
              label: __("Point de vente fiscal"), default: frm.doc.point_of_sale || (frm.doc.last_status && JSON.parse(frm.doc.last_status || "{}").proposed_for) || "" },
            { fieldname: "cmp", fieldtype: "HTML" },
            { fieldname: "accept_differences", fieldtype: "Small Text",
              label: __("Ecarts acceptes (motif obligatoire s'il y a des ecarts non bloquants)") },
            { fieldname: "sb", fieldtype: "Section Break", label: __("Canal prioritaire (facultatif)"),
              hidden: !frappe.user.has_role(["DGI Fiscal Manager", "System Manager"]) },
            { fieldname: "primary_channel", fieldtype: "Select", options: "\ne-MCF\nMCF",
              label: __("Derogation pour ce point de vente (vide : parametres generaux)"),
              hidden: !frappe.user.has_role(["DGI Fiscal Manager", "System Manager"]) },
            { fieldname: "priority_reason", fieldtype: "Small Text", label: __("Motif de la derogation"),
              depends_on: "primary_channel", mandatory_depends_on: "primary_channel" },
        ],
        primary_action_label: __("Valider"),
        primary_action(v) {
            frappe.call({ method: "dgi_compliance.fiscal.registry.validate_device",
                args: { nid: frm.doc.name, point_of_sale: v.point_of_sale, accept_differences: v.accept_differences,
                        primary_channel: v.primary_channel, priority_reason: v.priority_reason },
                freeze: true, callback: () => { d.hide(); frm.reload_doc(); } });
        },
    });
    const render = () => {
        const pvf = d.get_value("point_of_sale");
        frappe.call({ method: "dgi_compliance.fiscal.registry.device_comparison", args: { nid: frm.doc.name, point_of_sale: pvf || null },
            callback: (r) => {
                const m = r.message || {};
                const rows = (m.rows || []).map((x) => `<tr class="${x.ok ? "" : (x.blocking ? "danger" : "warning")}">
                    <td>${esc(x.field)}</td><td>${esc(x.read || "-")}</td><td>${esc(x.expected || "-")}</td>
                    <td>${x.ok ? "OK" : (x.blocking ? __("Bloquant") : __("Ecart a accepter"))}</td></tr>`).join("");
                const own = m.is_initiator ? `<p class="text-danger">${__("Vous avez propose ce MCF : une autre personne (DGI Fiscal Manager ou DGI Approbateur) doit le valider.")}</p>` : "";
                d.fields_dict.cmp.$wrapper.html(`${own}<p class="text-muted">${__("Valeurs lues dans le MCF par {0}, comparees aux valeurs attendues. Le NIF doit etre celui de la societe.", [esc(m.discovered_by || "?")])}</p>
                    <table class="table table-bordered table-condensed"><thead><tr><th>${__("Controle")}</th><th>${__("Lu dans le MCF")}</th><th>${__("Attendu")}</th><th></th></tr></thead><tbody>${rows}</tbody></table>`);
            } });
    };
    d.fields_dict.point_of_sale.df.onchange = render;
    d.show();
    render();
}
