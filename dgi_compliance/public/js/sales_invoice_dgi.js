// Desk client script -- adds the DGI buttons & status indicators.
// Loaded globally via hooks.py (app_include_js). Safe to call multiple
// times (Frappe re-runs handlers on every form refresh).

frappe.ui.form.on('Sales Invoice', {
    refresh(frm) {
        if (frm.is_new()) return;
        if (frm.doc.docstatus === 1 && !frm.doc.dgi_code_def) {
            frm.add_custom_button(__('Send to DGI'), () => {
                frappe.call({
                    method: 'dgi_compliance.overrides.sales_invoice.manual_certify',
                    args: { sales_invoice: frm.doc.name },
                    freeze: true,
                    freeze_message: __('Sending invoice to DGI e-DEF...'),
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

// DGI Settings form -- manual reference-data refresh button.
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

// DGI eMCF POS form -- setup status indicator + manual re-provision button.
// Provisioning normally runs automatically on save (see DGIeMCFPOS.on_update);
// this button lets an operator force it after fixing a token.
frappe.ui.form.on('DGI eMCF POS', {
    refresh(frm) {
        if (frm.is_new()) return;

        if (frm.doc.setup_complete) {
            frm.dashboard.set_headline_alert(
                __('Setup complete -- token validated and reference data loaded.'),
                'green',
            );
        } else {
            frm.dashboard.set_headline_alert(
                __('Setup pending -- fill POS Code, NIM, Active status and Token, then save. Provisioning runs automatically.'),
                'orange',
            );
        }

        frm.add_custom_button(__('Run DGI Setup Now'), () => {
            frappe.call({
                method: 'dgi_compliance.api.info.complete_pos_setup',
                args: { pos_code: frm.doc.name },
                freeze: true,
                freeze_message: __('Validating token & loading DGI reference data...'),
                callback: (r) => {
                    frm.reload_doc();
                    const m = r.message || {};
                    if (m.setup_complete) {
                        frappe.show_alert({ message: __('POS setup complete'), indicator: 'green' });
                    } else {
                        frappe.msgprint({
                            title: __('Setup not complete'),
                            message: m.health || __('Check the token and try again.'),
                            indicator: 'red',
                        });
                    }
                },
            });
        }, __('DGI'));
    },
});
