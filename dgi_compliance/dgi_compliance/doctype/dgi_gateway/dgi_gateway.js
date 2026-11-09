frappe.ui.form.on("DGI Gateway", {
    refresh(frm) {
        if (frm.is_new()) return;
        const call = (method, args, done) => frappe.call({
            method: "dgi_compliance.dgi_compliance.doctype.dgi_gateway.dgi_gateway." + method,
            args: Object.assign({ gateway: frm.doc.name }, args || {}),
            callback: (r) => { if (done) done(r.message || {}); frm.reload_doc(); },
        });
        const manager = frappe.user.has_role(["DGI Fiscal Manager", "System Manager"]);
        const approver = manager || frappe.user.has_role("DGI Approbateur");     // v5.4.0
        if (frm.doc.status !== "Revoquee" && manager) {
            frm.add_custom_button(__("Generer un code d'enrolement"), () => {
                call("generate_enrollment_code", {}, (m) => {
                    frappe.msgprint({
                        title: __("Code d'enrolement (affiche une seule fois)"),
                        indicator: "blue",
                        message: `<p style="font-size:1.6em;letter-spacing:.1em"><b>${frappe.utils.escape_html(m.code)}</b></p>
                                  <p>${frm.doc.kind === "Poste navigateur"
                                      ? __("Valable jusqu'au {0}. A saisir dans la page Poste MCF de la caisse ou le MCF est branche.", [m.expires])
                                      : __("Valable jusqu'au {0}. A saisir sur le poste du site : dgi-gateway enroll --code {1}", [m.expires, frappe.utils.escape_html(m.code)])}</p>
                                  <p>${__("Transmettez-le de vive voix, pas par e-mail ni messagerie.")}</p>`,
                    });
                });
            }, __("Passerelle"));
        }
        if (frm.doc.status === "A approuver" && approver) {
            if (frm.doc.enrolled_by && frm.doc.enrolled_by === frappe.session.user) {
                frm.dashboard.add_comment(__("Vous avez enrole ce poste : une autre personne (DGI Fiscal Manager ou DGI Approbateur) compare l'empreinte et l'approuve."), "orange", true);
            }
            frm.add_custom_button(__("Approuver"), () => {
                frappe.prompt({ fieldname: "fingerprint", fieldtype: "Data", reqd: 1,
                                label: __("Empreinte affichee sur le poste du site"),
                                description: __("Attendue : {0}", [frm.doc.key_fingerprint]) },
                    (v) => call("approve_gateway", { fingerprint: v.fingerprint }),
                    __("Approuver la passerelle"), __("Approuver"));
            }, __("Passerelle"));
        }
        if (["Active", "A approuver", "Code emis"].includes(frm.doc.status) && manager) {
            frm.add_custom_button(__("Revoquer"), () => {
                frappe.prompt({ fieldname: "reason", fieldtype: "Small Text", reqd: 1, label: __("Motif") },
                    (v) => call("revoke_gateway", { reason: v.reason }), __("Revoquer la passerelle"), __("Revoquer"));
            }, __("Passerelle"));
        }
        if (frm.doc.kind === "Poste navigateur") {
            frm.set_intro(__("Poste navigateur : sur la caisse ou le MCF est branche, ouvrir la page {0} dans Chrome ou Edge avec un utilisateur DGI Caisse MCF, connecter le MCF, puis saisir le code d'enrolement.",
                [`<a href="/app/dgi-mcf-station">${__("Poste MCF")}</a>`]), "blue");
        }
        const colors = { "Active": "green", "A approuver": "orange", "Code emis": "blue", "Revoquee": "red", "Nouvelle": "gray" };
        frm.page.set_indicator(__(frm.doc.status), colors[frm.doc.status] || "gray");
    },
});
