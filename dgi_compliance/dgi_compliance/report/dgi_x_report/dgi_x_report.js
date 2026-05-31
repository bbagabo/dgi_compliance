// Rapport X -- lecture intermediaire (sans cloture, sans remise a zero).
frappe.query_reports["DGI X Report"] = {
    filters: [
        {
            fieldname: "pos",
            label: __("DGI eMCF POS"),
            fieldtype: "Link",
            options: "DGI eMCF POS",
        },
        {
            fieldname: "date",
            label: __("Date"),
            fieldtype: "Date",
            default: frappe.datetime.get_today(),
            reqd: 1,
        },
    ],
};
