frappe.ui.form.on("DGI Rapport Fiscal", {
    refresh(frm) {
        if (!frm.is_new()) {
            frm.disable_save();
            frm.set_read_only();
            frm.add_custom_button(__("Imprimer"), () => frm.print_doc());
        } else {
            frm.set_intro(__("Choisissez le type de rapport puis enregistrez: le rapport est calcule et fige. "
                + "Un rapport Z cloture la periode depuis le dernier Z."));
        }
    },
});
