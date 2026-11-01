// DGI MCF Device - v5.3.0 : validation explicite d'un MCF detecte (livrable B section 7)
frappe.ui.form.on("DGI MCF Device", {
    refresh(frm) {
        if (frm.is_new()) return;
        const colors = { Detecte: "orange", Declare: "orange", Actif: "green", "A revalider": "red", Suspendu: "gray", Retire: "gray" };
        frm.page.set_indicator(__(frm.doc.status), colors[frm.doc.status] || "gray");
        if (!["Detecte", "Declare", "A revalider"].includes(frm.doc.status)) return;
        if (!frappe.user.has_role(["DGI Fiscal Manager", "System Manager"])) return;
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
            { fieldname: "sb", fieldtype: "Section Break", label: __("Canal prioritaire (facultatif)") },
            { fieldname: "primary_channel", fieldtype: "Select", options: "\ne-MCF\nMCF",
              label: __("Derogation pour ce point de vente (vide : parametres generaux)") },
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
                d.fields_dict.cmp.$wrapper.html(`<p class="text-muted">${__("Valeurs lues dans le MCF par {0}, comparees aux valeurs attendues. Le NIF doit etre celui de la societe.", [esc(m.discovered_by || "?")])}</p>
                    <table class="table table-bordered table-condensed"><thead><tr><th>${__("Controle")}</th><th>${__("Lu dans le MCF")}</th><th>${__("Attendu")}</th><th></th></tr></thead><tbody>${rows}</tbody></table>`);
            } });
    };
    d.fields_dict.point_of_sale.df.onchange = render;
    d.show();
    render();
}
