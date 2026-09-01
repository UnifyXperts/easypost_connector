function update_progress(percent, message) {
    frappe.show_progress(
        __("Order Fulfillment"),
        percent,
        100,
        __(message)
    );
}

frappe.ui.form.on("Sales Order", {
    refresh(frm) {

        if (
            !frm.is_new() &&
            frm.doc.docstatus === 0 &&
            !frm.doc.custom_is_address_verified
        ) {

            frm.add_custom_button(
                __("Verify Address"),
                () => verify_and_submit(frm),
            );

        }

    },


    custom_marketplace(frm) {

        if ((frm.doc.custom_marketplace || "")
            .toLowerCase()
            .includes("erpnext")) {

            frm.set_value(
                "naming_series",
                "ERP.0001.####"
            );
        }

    },

    before_submit: async function (frm) {


        const valid = await validate_item_stock(frm);

        if (!valid) {
            frappe.validated = false;
            return;
        }

        // All items available
        // await new Promise((resolve) => {
        //     frappe.msgprint({
        //         title: __("Stock Available"),
        //         indicator: "green",
        //         message: __("All items are in stock. Click OK to proceed with submitting the Delivery Note."),
        //         primary_action: {
        //             label: __("OK"),
        //             action() {
        //                 resolve();
        //             }
        //         }
        //     });
        // });

        frappe.validated = true;
    }

});




async function verify_and_submit(frm) {

    if (!frm.doc.shipping_address_name) {
        frappe.msgprint(__("Please Set Shipping Address First."));
        return;
    }

    try {

        update_progress(20, "Verifying Address...");

        await frappe.call({
            method: "easypost_connector.api.api.verify_address",
            args: {
                address_name: frm.doc.shipping_address_name,
                doc_name: frm.doc.name,
                doctype: "Sales Order"
            }
        });

        update_progress(100, "Address Verified");

        await frm.reload_doc();

        frappe.hide_progress();

        // Opens the standard ERPNext submit confirmation dialog

        // frm.save("Submit");

    } catch (e) {

        frappe.hide_progress();

        frappe.msgprint({
            title: __("Error"),
            indicator: "red",
            message: e.message || __("Something went wrong.")
        });

    }

}

async function validate_item_stock(frm) {
    const shortages = [];

    for (const item of (frm.doc.items || [])) {

        if (!item.item_code || !item.warehouse) {
            continue;
        }

        const item_doc = await frappe.db.get_value(
            "Item",
            item.item_code,
            "is_stock_item"
        );

        if (!item_doc.message?.is_stock_item) {
            continue;
        }

        const stock = await frappe.db.get_value(
            "Bin",
            {
                item_code: item.item_code,
                warehouse: item.warehouse
            },
            "actual_qty"
        );

        const available = flt(
            stock.message?.actual_qty || 0
        );

        const required = flt(
            item.stock_qty || item.qty || 0
        );

        if (available < required) {
            shortages.push({
                item: item.item_code,
                warehouse: item.warehouse,
                available: available,
                required: required
            });
        }
    }

    // Insufficient stock found
    if (shortages.length > 0) {

        let html = `
            <div style="max-height:300px; overflow:auto;">
                <table class="table table-bordered">
                    <thead style="background:#f8d7da;">
                        <tr>
                            <th>Item</th>
                            <th>Warehouse</th>
                            <th>Required</th>
                            <th>Available</th>
                        </tr>
                    </thead>
                    <tbody>
        `;

        shortages.forEach(row => {
            html += `
                <tr>
                    <td>
                        ${frappe.utils.escape_html(row.item)}
                    </td>

                    <td>
                        ${frappe.utils.escape_html(row.warehouse)}
                    </td>

                    <td>
                        ${row.required}
                    </td>

                    <td style="color:red; font-weight:bold;">
                        ${row.available}
                    </td>
                </tr>
            `;
        });

        html += `
                    </tbody>
                </table>

                <p style="color:red; font-weight:bold; margin-top:15px;">
                    Please create stock for the above item(s)
                    before submitting.
                </p>
            </div>
        `;

        frappe.msgprint({
            title: __("Insufficient Stock"),
            indicator: "red",
            message: html,

            primary_action: {
                label: __("Create Stock Entry"),

                action() {
                    frappe.new_doc(
                        "Stock Entry",
                        {
                            stock_entry_type: "Material Receipt"
                        }
                    );
                }
            }
        });

        return false;
    }

    return true;
}