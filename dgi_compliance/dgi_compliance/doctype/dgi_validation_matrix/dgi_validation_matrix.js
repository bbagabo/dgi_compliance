frappe.listview_settings["DGI Validation Matrix"] = {
    add_fields: ["status", "enforcement", "is_active"],
    get_indicator(doc) {
        if (!doc.is_active) return [__("Inactif"), "gray", "is_active,=,0"];
        if (doc.status === "Blocked") return [__("Bloque"), "red", "status,=,Blocked"];
        return [__("Autorise"), "green", "status,=,Allow"];
    },
};
