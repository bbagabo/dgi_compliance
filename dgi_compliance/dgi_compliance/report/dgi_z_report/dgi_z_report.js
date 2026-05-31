// Rapport Z -- cloture journaliere (totaux cumules du jour par groupe de TVA).
frappe.query_reports["DGI Z Report"] = {
    filters: [
        {
            fieldname: "pos",
            label: __("DGI eMCF POS"),
            fieldtype: "Link",
            options: "DGI eMCF POS",
        },
        {
            fieldname: "date",
            label: __("Date de cloture"),
            fieldtype: "Date",
            default: frappe.datetime.get_today(),
            reqd: 1,
        },
    ],
};
