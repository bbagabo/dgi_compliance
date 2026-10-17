frappe.listview_settings["DGI Rapport Fiscal"] = {
    onload(listview) {
        const make = (type, label, confirm_msg) => {
            listview.page.add_inner_button(label, () => {
                const run = (args) => frappe.call({
                    method: "dgi_compliance.dgi_compliance.doctype.dgi_rapport_fiscal.dgi_rapport_fiscal.generate",
                    args: Object.assign({ report_type: type }, args || {}),
                    freeze: true,
                    freeze_message: __("Calcul du rapport..."),
                    callback: (r) => r.message && frappe.set_route("Form", "DGI Rapport Fiscal", r.message),
                });
                if (type === "X périodique") {
                    frappe.prompt([
                        { fieldname: "from_datetime", label: __("Du"), fieldtype: "Datetime", reqd: 1 },
                        { fieldname: "to_datetime", label: __("Au"), fieldtype: "Datetime", reqd: 1 },
                    ], (v) => run(v), __("Rapport X périodique"), __("Générer"));
                } else if (confirm_msg) {
                    frappe.confirm(confirm_msg, () => run());
                } else {
                    run();
                }
            }, __("Générer"));
        };
        make("X quotidien", __("Rapport X quotidien"));
        make("X périodique", __("Rapport X périodique"));
        make("Z", __("Rapport Z (clôture)"), __("Le rapport Z clôture la période en cours et en ouvre une nouvelle. Continuer ?"));
        make("A", __("Rapport A (articles)"));
    },
};
