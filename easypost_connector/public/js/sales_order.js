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

        const weight_valid = await validate_item_weight(frm);

        if (!weight_valid) {
            frappe.validated = false;
            return;
        }

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
                    frappe.model.with_doctype("Stock Entry", () => {

                        let stock_entry = frappe.model.get_new_doc("Stock Entry");

                        stock_entry.stock_entry_type = "Material Receipt";

                        shortages.forEach(row => {

                            let item = frappe.model.add_child(
                                stock_entry,
                                "Stock Entry Detail",
                                "items"
                            );

                            item.item_code = row.item;
                            item.t_warehouse = row.warehouse;
                            item.qty = row.required;
                            item.conversion_factor = 1;

                        });

                        frappe.set_route(
                            "Form",
                            "Stock Entry",
                            stock_entry.name
                        );
                    });
                }
            }
        });

        return false;
    }

    return true;
}

async function validate_item_weight(frm) {
    const invalid_items = [];

    for (const item of (frm.doc.items || [])) {

        if (!item.item_code) {
            continue;
        }

        const response = await frappe.db.get_value(
            "Item",
            item.item_code,
            [
                "weight_per_unit",
                "weight_uom",
                "is_stock_item"
            ]
        );

        const weight_per_unit =
            response.message?.weight_per_unit;

        const weight_uom =
            response.message?.weight_uom;
        const is_stock_item =
            response.message?.is_stock_item == 1 ? "Stock Item" : "Service Item";

        if (!weight_per_unit || !weight_uom) {
            invalid_items.push({
                item_code: item.item_code,
                weight_per_unit,
                weight_uom,
                is_stock_item
            });
        }
    }

    if (!invalid_items.length) {
        return true;
    }

    let html = `
        <div style="max-height:300px; overflow:auto;">
            <table class="table table-bordered">
                <thead style="background:#fff3cd;">
                    <tr>
                        <th>Item</th>
                        <th>Type Of Item </th>
                        <th>Weight Per Unit</th>
                        <th>Weight UOM</th>
                        <th>Action</th>

                    </tr>
                </thead>
                <tbody>
    `;

    invalid_items.forEach(row => {
        html += `
            <tr>
                <td>${frappe.utils.escape_html(row.item_code)}</td>
                <td>${frappe.utils.escape_html(row.is_stock_item || "Not Set")}</td>

                <td style="color:red;">
                    ${row.weight_per_unit || "Not Set"}
                </td>

                <td style="color:red;">
                    ${frappe.utils.escape_html(
            row.weight_uom || "Not Set"
        )}
                </td>
                <td>
                    <a href="/app/item/${row.item_code}" class="btn btn-xs btn-primary">Go To Item</a>
                </td>
            </tr>
        `;
    });

    html += `
                </tbody>
            </table>

            <p style="color:#856404; font-weight:bold;">
                Please configure Weight Per Unit and Weight UOM
                for the above Item(s).
            </p>
        </div>
    `;

    frappe.msgprint({
        title: __("Missing Item Weight Configuration"),
        indicator: "orange",
        message: html
    });

    return false;
}