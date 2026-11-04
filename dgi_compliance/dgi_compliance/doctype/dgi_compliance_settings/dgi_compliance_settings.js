frappe.ui.form.on("DGI Compliance Settings", {
    refresh(frm) {
        // ---- v5.3.0: canal prioritaire + MCF a valider ----
        if (frm.doc.primary_channel) {
            frm.dashboard.set_headline_alert(
                __("Canal prioritaire : {0}. L'autre canal sert de secours automatique, seulement quand le canal prioritaire n'a certainement pas recu la facture (selon la politique de chaque point de vente).",
                    [frappe.utils.escape_html(frm.doc.primary_channel)]),
                frm.doc.primary_channel === "MCF" ? "blue" : "green");
        }
        frappe.db.count("DGI MCF Device", { filters: { status: ["in", ["Detecte", "Declare", "A revalider"]] } }).then((n) => {
            frm.add_custom_button(__("MCF a valider ({0})", [n]), () =>
                frappe.set_route("List", "DGI MCF Device", { status: ["in", ["Detecte", "Declare", "A revalider"]] }), __("MCF"));
        });
        // v5.3.1: the search really reads the MCF plugged into THIS computer (Web Serial, no fiscal effect)
        frm.add_custom_button(__("Rechercher un MCF (sur ce PC)"), () => dgi_search_mcf(), __("MCF"));
        frm.add_custom_button(__("Journal des transmissions"), () => frappe.set_route("List", "DGI Fiscal Operation"), __("MCF"));
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


// ---------------------------------------------------------------------------- v5.3.1
// "Rechercher un MCF (sur ce PC)": ERPNext (cloud) does not see the USB ports, but the browser of the
// computer where the MCF is plugged in does (Web Serial, Chrome / Edge). The MCF is read without fiscal
// effect (C1h state, 2Bh taxpayer, C2h DGI connection), shown, and can be proposed ('Detecte'). It is
// never activated here: a DGI Fiscal Manager validates it on DGI MCF Device (another person when the
// two-person rule is on).
function dgi_search_mcf() {
    const esc = frappe.utils.escape_html;
    if (!frappe.user.has_role(["System Manager", "DGI Fiscal Manager"])) {
        frappe.msgprint({
            title: __("Rechercher un MCF"), indicator: "orange",
            message: __("Reserve au role <b>DGI Fiscal Manager</b> (ou System Manager). Un caissier utilise la page <b>Poste MCF</b> (/app/dgi-mcf-station)."),
        });
        return;
    }
    if (!("serial" in navigator) || !window.isSecureContext) {
        frappe.msgprint({
            title: __("Rechercher un MCF"),
            indicator: "orange",
            message: __("Ce navigateur ne peut pas lire un port serie. Ouvrez ERPNext dans <b>Google Chrome</b> ou <b>Microsoft Edge</b> a jour, en <b>HTTPS</b>, sur l'ordinateur ou le MCF est branche, puis cliquez de nouveau sur <b>Rechercher un MCF (sur ce PC)</b>. Vous pouvez aussi utiliser la page <b>Poste MCF</b> (/app/dgi-mcf-station)."),
        });
        return;
    }
    frappe.require("/assets/dgi_compliance/js/dgi_mcf.js", async () => {
        const M = window.DGIMCF;
        let port;
        try {
            port = await navigator.serial.requestPort({});
        } catch (e) {
            if (e && e.name === "NotFoundError") return;            // window closed without choosing
            frappe.msgprint({ title: __("Rechercher un MCF"), indicator: "red", message: esc((e && e.message) || String(e)) });
            return;
        }
        frappe.dom.freeze(__("Lecture du MCF (sans effet fiscal)..."));
        let read, dev;
        try {
            const t = await new M.WebSerialTransport(port, { baudRate: 115200 }).open();
            dev = new M.MCFDevice(t, {});
            read = await dev.lock(async () => {
                const st = await dev.status();
                const tp = {};
                for (const opt of ["I0", "I1", "I2", "I3", "I4"]) {
                    try { tp[opt] = await dev.taxpayer(opt); } catch (e) { tp[opt] = ""; }
                }
                let srv = null;                              // ER (srv.error) is the MCF's own last error, not a read failure
                try { srv = await dev.serverStatus(); } catch (e) { srv = { read_error: e.message }; }
                return { st, tp, srv, port: t.name };
            });
        } catch (e) {
            const msg = String((e && e.message) || e);
            frappe.msgprint({
                title: __("Aucun MCF lu sur ce port"), indicator: "red",
                message: /open|ouvr|busy|Access/i.test(msg)
                    ? __("Le port est deja utilise (page Poste MCF ouverte dans un autre onglet, ou autre programme). Fermez-le puis recommencez.") + "<br><small>" + esc(msg) + "</small>"
                    : __("Le port choisi ne repond pas comme un MCF (protocole SFE-MCF, 115200 bit/s). Verifiez le port COM dans le Gestionnaire de peripheriques.") + "<br><small>" + esc(msg) + "</small>",
            });
            return;
        } finally {
            frappe.dom.unfreeze();
            if (dev) { try { await dev.close(); } catch (e) { /* already closed */ } }
        }
        dgi_show_mcf(read);
    });
}

async function dgi_show_mcf({ st, tp, srv, port }) {
    const esc = frappe.utils.escape_html;
    const companies = await frappe.db.get_list("Company", { fields: ["name", "tax_id"], limit: 100 });
    const owner = companies.find((c) => String(c.tax_id || "").replace(/\s/g, "").toUpperCase() === String(st.nif || "").toUpperCase());
    const rates = Object.entries(st.tax_rates || {}).map(([g, r]) => `${g} ${r}%`).join(" · ");
    const h = (v) => esc(v == null ? "" : String(v));
    const rows = [                                   // [label, already-escaped HTML]
        [__("Port"), h(port)],
        [__("MID (NID-activation)"), h(st.mid)], [__("NID"), h(st.nid)],
        [__("NIF du MCF"), h(st.nif) + (owner ? ` <span class="text-success">(${h(owner.name)})</span>`
            : ` <span class="text-danger">(${h(__("aucune societe avec ce NIF"))})</span>`)],
        [__("Date du MCF"), h(dgi_mcf_dt(st.dt))],
        [__("Compteurs (total / ventes / avoirs)"), h(`${st.tc} / ${st.fvc} / ${st.frc}`)],
        [__("Groupes de taxation ({0})", [st.group_count]), h(rates) + (st.group_count > 16 ? ""
            : ` <small>(${h(__("protocole v1.0 : groupes A a P"))})</small>`)],
        [__("Facture ouverte"), h(st.invoice_open ? __("Oui") : __("Non"))],
        [__("Etat"), h((st.flags || []).length ? st.flags.join(", ") : "OK")],
        [__("Connexion DGI (C2h)"), dgi_c2h_html(srv, st.dt, h)],
        // 2Bh fields as defined by protocol SFE-MCF v1.0 (I0 name, I1 address, I2 city, I3 phone, I4 e-mail):
        // shown one by one so that an MCF configured otherwise is visible before validation
        [__("Contribuable (2Bh)"), [["I0", __("Nom")], ["I1", __("Adresse")], ["I2", __("Ville")], ["I3", __("Telephone")],
            ["I4", __("E-mail")]].map(([k, l]) => `${h(l)} : ${h(tp[k] || "-")}`).join("<br>")],
    ];
    const table = `<table class="table table-bordered table-sm">${rows.map(([k, v]) =>
        `<tr><td style="width:38%">${h(k)}</td><td>${v}</td></tr>`).join("")}</table>`;
    if (!st || !st.nid) {
        frappe.msgprint({ title: __("Aucun MCF lu sur ce port"), indicator: "red",
            message: __("Le MCF a repondu sans NID lisible : il ne peut pas etre propose.") });
        return;
    }
    const known = await frappe.db.get_value("DGI MCF Device", { name: st.nid }, "status")
        .then((r) => r.message && r.message.status, () => null);
    const d = new frappe.ui.Dialog({
        title: __("MCF lu sur ce PC (sans effet fiscal)"),
        size: "large",
        fields: [
            { fieldtype: "HTML", fieldname: "info", options: table + (known
                ? `<div class="alert alert-info">${__("Ce MCF existe deja dans ERPNext (etat : {0}).", [esc(__(known))])}</div>` : "") },
            { fieldtype: "Link", fieldname: "point_of_sale", options: "DGI Point of Sale", label: __("Point de vente prevu (facultatif)") },
        ],
        primary_action_label: __("Proposer ce MCF"),
        primary_action: async (v) => {
            const observation = {
                mid: st.mid, nid: st.nid, nif: st.nif, tc: st.tc, fvc: st.fvc, frc: st.frc, dt: st.dt,
                group_count: st.group_count, tax_rates: st.tax_rates, flags: st.flags || [],
                taxpayer_name: tp.I0 || "", taxpayer_address: [tp.I1, tp.I2].filter(Boolean).join(", "),
                taxpayer_phone: tp.I3 || "", taxpayer_email: tp.I4 || "", dgi: srv, port,
            };
            const r = await frappe.xcall("dgi_compliance.fiscal.registry.propose_from_browser",
                { observation: JSON.stringify(observation), point_of_sale: v.point_of_sale || null });
            d.hide();
            frappe.msgprint({
                title: __("MCF propose"), indicator: "green",
                message: __("MCF {0} enregistre (etat : {1}). Il n'est pas actif : un responsable fiscal doit le valider (DGI MCF Device > Valider ce MCF), en comparant les valeurs lues.", [esc(r.nid), esc(__(r.status))]),
                primary_action: { label: __("Ouvrir le MCF"), action: () => frappe.set_route("Form", "DGI MCF Device", r.nid) },
            });
        },
    });
    d.show();
}

// AAAAMMJJHHmmss (MCF) -> date of the user's format; any other value is shown as read
function dgi_mcf_dt(v) {
    const m = /^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})$/.exec(v || "");
    return m ? frappe.datetime.str_to_user(`${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}:${m[6]}`) : (v || "");
}

// C2h (protocol SFE-MCF v1.0): EC sent, DC available in the MCF, DT last successful connection, STA DIS/CON/TRA/RES,
// optional ER (manufacturer's last error). The SFE must show the last connection and alert beyond 1 day.
function dgi_c2h_html(srv, mcf_dt, h) {
    if (!srv) return "";
    if (srv.read_error) return `<span class="text-danger">${h(srv.read_error)}</span>`;
    const states = { DIS: __("non connecte au reseau"), CON: __("connecte, aucun envoi en cours"),
        TRA: __("envoi de donnees en cours"), RES: __("redemarrage en cours") };
    const ts = (v) => (/^\d{14}$/.test(v || "") ? new Date(+v.slice(0, 4), +v.slice(4, 6) - 1, +v.slice(6, 8),
        +v.slice(8, 10), +v.slice(10, 12), +v.slice(12, 14)) : null);
    const last = ts(srv.last_sync), now = ts(mcf_dt) || new Date();
    const fmt = () => dgi_mcf_dt(srv.last_sync);
    const old = !last || now - last > 24 * 3600 * 1000;
    return `${h(srv.state)} (${h(states[srv.state] || __("etat inconnu"))})<br>`
        + `${h(__("Transactions envoyees"))} : ${h(srv.sent)} · ${h(__("disponibles dans le MCF"))} : ${h(srv.pending)}<br>`
        + `<span class="${old ? "text-danger" : ""}">${h(__("Derniere connexion reussie a la DGI"))} : ${last ? h(fmt()) : h(__("jamais"))}`
        + `${old ? " - " + h(__("plus d'un jour : verifiez la connexion du MCF")) : ""}</span>`
        + (srv.error ? `<br><small>${h(__("Dernier message du MCF"))} : ${h(srv.error)}</small>` : "");
}
