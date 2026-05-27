// Desk client script — adds the DGI buttons & status indicators to the
// Sales Invoice form. Loaded via hooks.py / doctype_js.
// Safe to call multiple times (Frappe re-runs on every refresh).

frappe.ui.form.on('Sales Invoice', {
    refresh(frm) {
        if (frm.is_new()) return;
        if (frm.doc.docstatus === 1 && !frm.doc.dgi_code_def) {
            frm.add_custom_button(__('Send to DGI'), () => {
                frappe.call({
                    method: 'dgi_compliance.overrides.sales_invoice.manual_certify',
                    args: { sales_invoice: frm.doc.name },
                    freeze: true,
                    freeze_message: __('Sending invoice to DGI e-DEF…'),
                    callback: (r) => {
                        frm.reload_doc();
                        if (r.message && r.message.status === 'ok') {
                            frappe.show_alert({ message: __('Invoice certified'), indicator: 'green' });
                        } else if (r.message) {
                            frappe.msgprint({ message: r.message.message || __('Failed'), indicator: 'red' });
                        }
                    },
                });
            }, __('DGI'));
        }

        if (frm.doc.dgi_code_def) {
            frm.add_custom_button(__('Refresh DGI Status'), () => {
                frappe.call({
                    method: 'dgi_compliance.api.invoice.status_request',
                    args: { sales_invoice: frm.doc.name },
                    freeze: true,
                    callback: () => frm.reload_doc(),
                });
            }, __('DGI'));
        }

        // Status indicator
        const status_to_color = {
            'Normalized': 'green',
            'Pending':    'orange',
            'Failed':     'red',
            'Cancelled':  'grey',
        };
        if (frm.doc.dgi_status) {
            frm.page.set_indicator(
                __('DGI: {0}', [frm.doc.dgi_status]),
                status_to_color[frm.doc.dgi_status] || 'blue',
            );
        }
    },
});

// DGI Settings form — manual reference-data refresh button.
frappe.ui.form.on('DGI Settings', {
    refresh(frm) {
        frm.add_custom_button(__('Refresh Reference Data'), () => {
            frappe.call({
                method: 'dgi_compliance.api.info.refresh_reference_data',
                freeze: true,
                callback: (r) => {
                    if (r.message) {
                        const lines = Object.entries(r.message)
                            .map(([k, v]) => `${k}: ${v} new`)
                            .join('<br>');
                        frappe.msgprint({
                            title: __('DGI lookups refreshed'),
                            message: lines || __('No new rows'),
                            indicator: 'green',
                        });
                    }
                    frm.reload_doc();
                },
            });
        });

        frm.add_custom_button(__('Ping DGI /status'), () => {
            frappe.call({
                method: 'dgi_compliance.api.info.check_pos_token_validity',
                freeze: true,
                callback: (r) => {
                    frappe.msgprint({
                        title: __('Health check'),
                        message: '<pre>' + JSON.stringify(r.message, null, 2) + '</pre>',
                    });
                },
            });
        });
    },
});
