// Rapport A -- rapport periodique / d'audit (cumul par jour sur une periode).
frappe.query_reports["DGI A Report"] = {
    filters: [
        {
            fieldname: "pos",
            label: __("DGI eMCF POS"),
            fieldtype: "Link",
            options: "DGI eMCF POS",
        },
        {
            fieldname: "from_date",
            label: __("Du"),
            fieldtype: "Date",
            default: frappe.datetime.month_start(),
            reqd: 1,
        },
        {
            fieldname: "to_date",
            label: __("Au"),
            fieldtype: "Date",
            default: frappe.datetime.get_today(),
            reqd: 1,
        },
    ],
};
