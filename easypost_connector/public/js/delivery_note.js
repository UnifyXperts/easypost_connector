const FULFILLMENT_STEPS = [
    { key: "packing", label: "Packing Slip", button: "Create" },
    { key: "label", label: "Shipping Label", button: "Purchase" },
    // {   key: "zpl", label: "ZPL", button: "Convert"},
    { key: "print", label: "Print", button: "Download" }
];

// Maps each tracker step key to the async function that performs it,
// so retry_step() can actually call the right thing.
const STEP_HANDLERS = {
    packing: print_packingslip,
    label: buy_shipping_label_step,
    zpl: generate_zpl_step
};

function get_tracker_state(frm) {
    if (!frm._shipment_tracker) {
        frm._shipment_tracker = {};
    }
    return frm._shipment_tracker;
}

function init_tracker(frm) {
    const state = get_tracker_state(frm);
    state.status = {
        packing: frm.doc.custom_packing_slip_completed ? "success" : "pending",
        label: frm.doc.custom_label_created ? "success" : "pending",
        zpl: frm.doc.custom_zpl_file ? "success" : "pending",
        print: "pending"

    };
    render_tracker(frm);
}

function update_tracker(frm, step, status) {
    const state = get_tracker_state(frm);
    state.status[step] = status;
    render_tracker(frm);
}

function render_tracker(frm) {
    const state = get_tracker_state(frm);
    const status = state.status;
    const steps = FULFILLMENT_STEPS.map(step => ({
        ...step,

        done:
            step.key === "packing"
                ? frm.doc.custom_packing_slip_completed
                : step.key === "label"
                    ? frm.doc.custom_label_created
                    : step.key === "zpl"
                        ? !!frm.doc.custom_zpl_file
                        : false


    }));
    let html = `

<style>
.fulfillment-card{
    border:1px solid #dcdcdc;
    border-radius:12px;
    padding:20px;
    background:white;
}
.fulfillment-flow{
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:15px;
}
.flow-step{
    flex:1;
    text-align:center;
}
.flow-circle{
    width:55px;
    height:55px;
    border-radius:50%;
    display:flex;
    align-items:center;
    justify-content:center;
    margin:auto;
    font-size:22px;
    font-weight:bold;
}
.pending{
    background:#ececec;
}
.running{
    background:#fff5cc;
}
.success{
    background:#d4edda;
}
.failed{
    background:#f8d7da;
}
.flow-title{
    margin-top:10px; font-weight:600;
}
.flow-status{
    color:#666; font-size:12px; margin-bottom:12px;
}

.flow-arrow{
    font-size:28px; color:#999;
}

</style>

<div class="fulfillment-card">

<h4 style="margin-bottom:20px">
Shipment
</h4>

<div class="fulfillment-flow">

`;

    steps.forEach((step, index) => {
        let css = "pending";
        let icon = "○";
        let label = "Pending";

        // Special icon for Print step
        if (step.key === "print") {
            icon = "🖨️";
            label = "";
        }

        if (status[step.key] === "running" && step.key !== "print") {
            css = "running";
            icon = "⟳";
            label = "Running";
        }

        if (status[step.key] === "failed" && step.key !== "print") {
            css = "failed";
            icon = "✕";
            label = "Failed";
        }

        if ((step.done || status[step.key] === "success") && step.key !== "print") {
            css = "success";
            icon = "✓";
            label = "Completed";
        }
        html += `
<div class="flow-step">

    <div class="flow-circle ${css}">
        ${icon}
    </div>

    <div class="flow-title">
        ${step.label}
    </div>

    ${step.key !== "print"
                ? `<div class="flow-status">${label}</div>`
                : `<div class="flow-status">&nbsp;</div>`
            }
`;
        if (step.key === "packing") {
            html += `
<button class="btn btn-primary btn-sm print-packingslip-btn"
${step.done ? "disabled" : ""}>
Create
</button>

`;
        }
        if (step.key === "label") {
            html += `
<button class="btn btn-primary btn-sm generate-label-btn"
${!frm.doc.custom_packing_slip_completed || frm.doc.custom_label_created ? "disabled" : ""}>
Purchase
</button>
`;
        }
        if (step.key === "zpl") {

            html += `
<button class="btn btn-primary btn-sm download-label-btn"
>
${frm.doc.custom_zpl_file ? "Purchase" : "Purchase"}

</button>
`;
        }
        if (step.key === "print") {
            html += `
<button class="btn btn-success btn-sm print-label-btn"
${!frm.doc.custom_label_created ? "disabled" : ""}>
Reprint
</button>

`;
        }
        html += "</div>";
        if (index !== steps.length - 1) {
            html += `<div class="flow-arrow">➜</div>`;
        }
    });
    html += "</div></div>";
    const wrapper = frm.fields_dict.custom_shipping_section.$wrapper;
    wrapper.html(html);
    wrapper.find(".print-packingslip-btn").click(async () => {

        await run_step(
            frm,
            "packing",
            async () => {

                // Step 1: Create and complete packing slip
                const packing_slip = await print_packingslip(frm);

                if (!packing_slip) {
                    return false;
                }

                // Step 2: Get printer configuration
                const config = await get_printer_configuration();

                const copies = config.default_number_of_copies || 1;

                // Step 3: Build packing slip printer URL
                const proxy_url =
                    `http://${config.printer_network_host}:${config.port_for_packing_slip}`;


                if (config.print_packing_slip) {

                    try {

                        frappe.dom.freeze(
                            __("Printing Packing Slip...")
                        );

                        await print_document(
                            frm.doc,
                            "Packing Slip",
                            null,
                            proxy_url,
                            config.api_key,
                            config.printer,
                            copies
                        );

                        frappe.show_alert({
                            message: __("Packing Slip sent to printer."),
                            indicator: "green"
                        });

                    } finally {

                        frappe.dom.unfreeze();
                    }

                    return true;

                } else {

                    frappe.show_alert({
                        message: __(
                            "Packing Slip printing is not enabled in Easypost Settings."
                        ),
                        indicator: "orange"
                    });

                    // Packing slip creation was successful,
                    // only auto-printing is disabled.
                    return true;
                }
            }
        );

    });

    wrapper.find(".generate-label-btn").click(async () => {

        console.log("========== LABEL BUTTON CLICKED ==========");

        await run_step(
            frm,
            "label",
            async () => {

                // ==========================================
                // STEP 1: PURCHASE SHIPPING LABEL
                // ==========================================

                console.log("STEP 1: Purchasing Shipping Label...");

                const result = await buy_shipping_label_step(frm);

                console.log("STEP 1 RESULT:", result);

                if (!result) {
                    console.log("STOPPED: Label purchase returned false");
                    return false;
                }

                console.log("STEP 1 COMPLETED");


                // ==========================================
                // STEP 2: GET PRINTER CONFIGURATION
                // ==========================================

                console.log("STEP 2: Getting printer configuration...");

                const config = await get_printer_configuration();
                const copies = config.default_number_of_copies || 1;
                console.log("PRINTER CONFIG:", config);


                // ==========================================
                // STEP 3: CHECK AUTO PRINT
                // ==========================================

                console.log(
                    "PRINT LABEL CONFIG VALUE:",
                    config.print_label,
                    "TYPE:",
                    typeof config.print_label
                );

                if (!config.print_label) {

                    console.log("AUTO PRINT DISABLED");

                    frappe.show_alert({
                        message: __(
                            "Shipping Label created successfully. Auto printing is disabled."
                        ),
                        indicator: "orange"
                    });

                    return true;
                }

                console.log("AUTO PRINT ENABLED");


                // ==========================================
                // STEP 4: BUILD PRINTER URL
                // ==========================================

                const proxy_url =
                    `http://${config.printer_network_host}:${config.port_for_shipping_label}`;

                console.log("PRINTER URL:", proxy_url);


                // ==========================================
                // STEP 5: GENERATE ZPL
                // ==========================================

                console.log("STEP 5: Generating ZPL...");

                const r = await frappe.call({
                    method: "easypost_connector.api.api.convert_png_to_bw",
                    args: {
                        shipment_id: frm.doc.custom_easypost_shipment_id,
                        docname: frm.doc.name
                    }
                });

                console.log("ZPL API RESPONSE:", r);

                const zpl_result = r.message;

                console.log("ZPL RESULT:", zpl_result);

                if (
                    !zpl_result?.success ||
                    !zpl_result?.zpl_bytes
                ) {
                    throw new Error(
                        zpl_result?.message ||
                        __("Unable to generate shipping label.")
                    );
                }

                console.log("ZPL GENERATED SUCCESSFULLY");
                console.log(
                    "ZPL LENGTH:",
                    zpl_result.zpl_bytes.length
                );


                // ==========================================
                // STEP 6: PRINT SHIPPING LABEL
                // ==========================================

                console.log("STEP 6: STARTING PRINT");

                frappe.dom.freeze(
                    __("Printing Shipping Label...")
                );

                try {

                    console.log(
                        "CALLING print_document NOW..."
                    );

                    const print_result = await print_document(
                        frm.doc,
                        "Shipping Label",
                        zpl_result.zpl_bytes,
                        proxy_url,
                        config.api_key,
                        config.printer,
                        copies
                    );

                    console.log(
                        "PRINT DOCUMENT RESULT:",
                        print_result
                    );

                    frappe.show_alert({
                        message: __(
                            "Shipping Label sent to printer."
                        ),
                        indicator: "green"
                    });

                    console.log("PRINT COMPLETED SUCCESSFULLY");

                    return true;

                } finally {

                    console.log("UNFREEZING SCREEN");

                    frappe.dom.unfreeze();
                }
            }
        );

        console.log("========== RUN STEP COMPLETED ==========");
    });

    wrapper.find(".print-label-btn").click(() => {
        print_selected_documents_v2(frm)
    });
}
/**
 * Runs a tracked step with consistent running/success/failed handling
 * and error display, instead of each caller reimplementing this.
 */

async function run_step(frm, step_key, action_fn) {
    update_tracker(frm, step_key, "running");
    try {
        await action_fn();
        await frm.reload_doc();
        update_tracker(frm, step_key, "success");
    } catch (e) {
        update_tracker(frm, step_key, "failed");
        frappe.msgprint({
            title: __("Step Failed"),
            indicator: "red",
            message: (e && e.message) ? e.message : __("An unknown error occurred.")
        });
    }
}

async function retry_step(frm, step) {
    const handler = STEP_HANDLERS[step];
    if (!handler) {
        frappe.msgprint(__("Nothing to retry for this step."));
        return;
    }
    await run_step(frm, step, () => handler(frm));
}

async function start_shipment(frm) {
    frm.set_value("custom_show_progress", 1);
    await frm.save();
}

async function get_printer_configuration() {

    // Get enabled Easypost Settings record
    const settings_list = await frappe.db.get_list(
        "Easypost Settings",
        {
            filters: {
                enabled: 1
            },
            fields: ["name"],
            limit: 1
        }
    );

    if (!settings_list.length) {
        throw new Error(
            "Enabled Easypost Settings not found."
        );
    }

    // Fetch complete document with ALL fields
    const config = await frappe.db.get_doc(
        "Easypost Settings",
        settings_list[0].name
    );

    console.log(
        "Complete Printer Configuration:",
        config
    );

    return config;
}



async function print_packingslip(frm) {
    if (frm.doc.custom_packing_slip_created) {
        frappe.msgprint({
            title: __("Packing Slip Already Exists"),
            indicator: "orange",
            message: __(
                "To regenerate it, please delete the existing Packing Slip and try again."
            )
        });
        return false;
    }

    const r = await frappe.call({
        method: "easypost_connector.api.api.create_packing_slip",
        args: {
            delivery_note: frm.doc.name
        }
    });

    const data = r.message;

    const box_weight = (
        frm.doc.custom_shipment_parcel_dimensions || []
    ).reduce((total, row) => {
        return total + (flt(row.weight) * flt(row.count || 1));
    }, 0);

    const box_uoms = await Promise.all(
        (frm.doc.custom_packing_box_details || []).map(async row => {
            const item = await frappe.db.get_doc(
                "Item",
                row.packing_box
            );

            return item.weight_uom;
        })
    );

    const box_uom = box_uoms[0] || null;

    return new Promise((resolve, reject) => {
        let completed = false;

        const d = new frappe.ui.Dialog({
            title: __("Complete Packing Slip"),

            fields: [
                {
                    fieldname: "box_weight",
                    label: __("Parcel Weight"),
                    fieldtype: "Float",
                    default: box_weight || null,
                    reqd: 1
                },
                {
                    fieldname: "gross_weight_uom",
                    label: __("Gross Weight UOM"),
                    fieldtype: "Link",
                    options: "UOM",
                    default: box_uom,
                    reqd: 1
                },
                {
                    fieldname: "from_package_no",
                    label: __("From Package No"),
                    fieldtype: "Data",
                    default: data.from_case_no
                },
                {
                    fieldname: "to_package_no",
                    label: __("To Package No"),
                    fieldtype: "Data",
                    default: data.to_case_no
                }
            ],

            primary_action_label: __("Complete"),

            primary_action: async (values) => {
                try {
                    await frappe.call({
                        method:
                            "easypost_connector.api.api.complete_packing_slip",
                        args: {
                            packing_slip: data.packing_slip,
                            net_weight: data.net_weight,
                            gross_weight:
                                flt(data.net_weight) +
                                flt(values.box_weight),
                            gross_weight_uom:
                                values.gross_weight_uom,
                            from_case_no:
                                values.from_package_no,
                            to_case_no:
                                values.to_package_no
                        }
                    });

                    completed = true;

                    await frm.set_value(
                        "custom_packing_slip_created",
                        1
                    );

                    await frm.set_value(
                        "custom_packing_slip_completed",
                        1
                    );

                    await frm.save();

                    d.hide();

                    resolve(data.packing_slip);

                } catch (e) {
                    console.error(e);
                    reject(e);
                }
            }
        });

        d.onhide = async () => {
            if (!completed) {
                await frm.set_value(
                    "custom_show_progress",
                    1
                );

                resolve(false);
            }
        };

        d.show();
    });
}

async function buy_shipping_label_step(frm) {
    if (frm.doc.custom_label_created) {
        frappe.msgprint({
            title: __("Shipping Label Already Exists"),
            indicator: "orange",
            message: __(
                "Tracking Number: {0}<br>" +
                "To regenerate the label, please delete the existing label and try again.",
                [frm.doc.custom_tracking_number]
            )
        });
        return;
    }

    // Find the selected shipping rate
    const selected_rate = (frm.doc.custom_rate || []).find(
        row => row.create_label
    );

    if (!selected_rate) {
        throw new Error(
            __("Please select a shipping rate before generating the label.")
        );
    }

    // Generate shipping label using the selected rate
    const r = await frappe.call({
        method: "easypost_connector.api.api.buy_shipment",
        args: {
            delivery_note: frm.doc.name,
            rate_id: selected_rate.rate_id
        }
    });

    const data = r.message;

    // Update shipment details
    await frm.set_value(
        "custom_tracking_number",
        data.tracking_number
    );

    await frm.set_value(
        "custom_tracking_url",
        data.tracking_url
    );

    await frm.set_value(
        "custom_label_url",
        data.label_url
    );

    await frm.set_value(
        "custom_easypost_shipment_id",
        data.shipment_id
    );

    await frm.set_value(
        "custom_tracking_status",
        data.tracking_status
    );

    await frm.set_value(
        "custom_tracking_status_details",
        data.tracking_status_details
    );

    // Mark label as created
    await frm.set_value(
        "custom_label_created",
        1
    );

    // -----------------------------------------
    // Keep ONLY the selected rate
    // -----------------------------------------
    (frm.doc.custom_rate || []).forEach(rate => {
        if (rate.name !== selected_rate.name) {
            frappe.model.clear_doc(
                "Carrier Delivery Rate Table",
                rate.name
            );
        }
    });

    frm.refresh_field("custom_rate");


    // Save Delivery Note
    await frm.save();
    await generate_zpl_step(frm)

    return true



}

async function generate_zpl_step(frm) {
    try {
        if (!frm.doc.custom_zpl_file) {
            await frappe.call({
                method: "easypost_connector.api.api.convert_png_to_bw",
                args: {
                    shipment_id: frm.doc.custom_easypost_shipment_id,
                    docname: frm.doc.name
                },
                freeze: true,
                freeze_message: __("Generating ZPL, please wait...")
            });

            await frm.reload_doc();
        }



        if (frm.is_dirty()) {
            await frm.save();
        }

        update_tracker(frm, "zpl", "success");
    } catch (e) {
        update_tracker(frm, "zpl", "failed");
        frappe.msgprint({
            title: __("Step Failed"),
            indicator: "red",
            message: (e && e.message) ? e.message : __("An unknown error occurred.")
        });
    }
}

async function fetch_shipping_rates(frm) {

    if (frm.__fetching_rates) {
        return;
    }

    frm.__fetching_rates = true;

    try {
        const r = await frappe.call({
            method: "easypost_connector.api.api.create_easypost_shipment",
            args: {
                delivery_note: frm.doc.name
            },
            freeze: true,
            freeze_message: "Fetching Shipping rate......."
        });

        const shipment = r.message;

        // Clear existing rates
        frm.clear_table("custom_rate");

        // Add new rates
        (shipment.rates || [])
            .sort((a, b) => parseFloat(a.rate) - parseFloat(b.rate))
            .forEach(rate => {
                const row = frm.add_child("custom_rate");

                row.carrier = rate.carrier;
                row.service = rate.service;
                row.rate = rate.rate;
                row.currency = rate.currency;
                row.days = rate.delivery_days;
                row.rate_id = rate.id;
                row.shipment_id = shipment.id;
            });
        await frm.save();

        // Render carrier errors
        const messages = shipment.messages || [];

        const html = `
            <table class="table table-bordered" 
                   style="border: 1px solid #dc3545;">
        
                <thead style="background: #f8d7da; color: #842029;">
                    <tr>
                        <th>Carrier</th>
                        <th>Issue</th>
                        <th>Details</th>
                    </tr>
                </thead>
        
                <tbody>
                    ${messages.length
                ? messages.map(msg => `
                                <tr style="background: #fff5f5;">
                                    <td>
                                        <b style="color: #dc3545;">
                                            ${msg.carrier || "Unknown Carrier"}
                                        </b>
                                    </td>
        
                                    <td style="color: #dc3545;">
                                        ${formatType(msg.type)}
                                    </td>
        
                                    <td style="color: #dc3545;">
                                        ${formatCarrierMessage(msg)}
                                    </td>
                                </tr>
                            `).join("")
                : `
                                <tr>
                                    <td colspan="3" class="text-muted text-center">
                                        No carrier issues found.
                                    </td>
                                </tr>
                            `
            }
                </tbody>
            </table>
        `;

        // Render HTML field
        frm.fields_dict.custom_error_in_fetching_rate_.$wrapper.html(html);

        // Refresh child table
        frm.refresh_field("custom_rate");

        // Save document

        frappe.show_alert({
            message: __("Shipping rates updated."),
            indicator: "green"
        });

    } catch (e) {
        console.error("Shipping rate fetch failed:", e);

        frappe.msgprint({
            title: __("Failed to Fetch Shipping Rates"),
            indicator: "red",
            message: e?.message || __("An unknown error occurred.")
        });

    } finally {
        frm.__fetching_rates = false;
        frappe.hide_progress();
    }
}

const formatCarrierMessage = (msg) => {
    let message = msg.message || "-";

    message = message
        .replace(/length \(([\d.]+)"\)/i, "Length ($1 inches)")
        .replace(/girth \(([\d.]+)"\)/i, "Girth ($1 inches)")
        .replace(/merchant_id/i, "Merchant ID")
        .replace(/origin.*outside canada/i, "The shipment origin must be in Canada")
        .replace(/no matching rates found/i, "No shipping rates are available");

    return message;
};

const formatType = (type) => {
    if (!type) return "-";

    return type
        .replace(/_/g, " ")
        .replace(/\b\w/g, char => char.toUpperCase());
};


let shipping_timer = null;

function debounce_fetch(frm) {

    clearTimeout(shipping_timer);
    shipping_timer = setTimeout(() => {
        fetch_shipping_rates(frm);
    }, 100);
}

let dimension_change_timer = null;
let changed_dimensions = new Set();

function dimension_changed(frm, field) {
    if (!frm.doc.custom_initial_fetch) {
        return;
    }

    const label =
        frappe.meta.get_docfield("Shipment Parcel", field)?.label || field;

    const confirmed = window.confirm(
        __(
            "You have changed {0}. Do you want to recalculate the shipping rate?",
            [label]
        )
    );

    if (confirmed) {
        debounce_fetch(frm);
    }
}

async function fetch_dimensions(frm, cdt, cdn) {
    const row = locals[cdt][cdn];

    if (!row.packing_box) return;

    // 1. Get Packing Box Item
    const box = await frappe.db.get_doc("Item", row.packing_box);

    const warehouse = box.item_defaults?.[0]?.default_warehouse;

    if (!warehouse) {
        frappe.throw(
            __("Default Warehouse is not set for Packing Box: {0}", [
                row.packing_box
            ])
        );
    }

    // 2. Check available packing box stock
    const available_qty = await frappe.db.get_value(
        "Bin",
        {
            item_code: row.packing_box,
            warehouse: warehouse
        },
        "actual_qty"
    );

    const stock = flt(
        available_qty.message?.actual_qty || 0
    );

    const required_qty = flt(row.quantity || 1);

    if (stock < required_qty) {
        frappe.throw(
            __(
                "Only {0} box(es) available in warehouse {1}. Required: {2}",
                [stock, warehouse, required_qty]
            )
        );
    }

    // 3. Net item weight
    const net_item_weight =
        flt(frm.doc.total_net_weight || 0) * required_qty;

    // 4. Packing box weight
    // Weight Per Unit is directly on Item
    const box_weight =
        flt(box.weight_per_unit || 0) * required_qty;

    // 5. Total shipment weight
    const total_weight =
        net_item_weight + box_weight;

    // 6. Box dimensions
    const length =
        flt(box.custom_length || 0);

    const width =
        flt(box.custom_width || 0);

    const height =
        flt(box.custom_height || 0);

    // 7. Weight UOM
    const weight_uom =
        box.weight_uom || "";

    const confirmed = await new Promise(resolve => {
        frappe.confirm(
            `
            <div>
                <p>
                    <b>Weight & Dimension Confirmation</b>
                </p>

                <p>
                    <b>Packing Box:</b>
                    ${row.packing_box}
                </p>

                <p>
                    <b>Packing Box Dimensions:</b>
                    ${length.toFixed(2)}
                    ×
                    ${width.toFixed(2)}
                    ×
                    ${height.toFixed(2)}
                </p>

                <p>
                    <b>Net Item Weight:</b>
                    ${net_item_weight.toFixed(2)}
                </p>

                <p>
                    <b>Packing Box Weight:</b>
                    ${box_weight.toFixed(2)}
                    ${weight_uom ? ` ${weight_uom}` : ""}
                </p>

                <hr>

                <p>
                    <b>Total Shipment Weight:</b>
                    ${total_weight.toFixed(2)}
                </p>

                <p>
                    <b>Shipment Dimensions:</b>
                    ${length.toFixed(2)}
                    ×
                    ${width.toFixed(2)}
                    ×
                    ${height.toFixed(2)}
                </p>

                <p>
                    This will be the weight and dimensions
                    of your shipment.
                    Are you okay with that?
                </p>
            </div>
            `,
            () => resolve(true),
            () => resolve(false)
        );
    });

    // 9. User rejected
    if (!confirmed) {
        await frappe.model.set_value(
            cdt,
            cdn,
            "packing_box",
            null
        );

        return;
    }

    // 10. Clear existing parcel dimensions
    frm.clear_table(
        "custom_shipment_parcel_dimensions"
    );

    // 11. Add one parcel for each required box
    for (let i = 0; i < required_qty; i++) {
        const parcel = frm.add_child(
            "custom_shipment_parcel_dimensions"
        );

        parcel.length = length;
        parcel.width = width;
        parcel.height = height;

        // Packing box weight per unit
        parcel.weight =
            flt(total_weight || 0);

        parcel.count = 1;
    }

    frm.refresh_field(
        "custom_shipment_parcel_dimensions"
    );

    // 12. Set total shipment weight
    if (frm.fields_dict.custom_total_shipment_weight) {
        await frm.set_value(
            "custom_total_shipment_weight",
            total_weight
        );
    }

    // 13. Inform user
    frappe.msgprint({
        title: __("Packing Box Confirmed"),
        indicator: "green",
        message: __(
            "Packing box details have been confirmed successfully.<br><br>" +
            "Please <b>Save the Delivery Note</b> to fetch shipping rates."
        )
    });
}

async function show_download_dialog(frm) {
    const d = new frappe.ui.Dialog({
        title: __("Download / Print Documents"),
        fields: [
            { fieldtype: "Check", fieldname: "packing_slip", label: __("Packing Slip"), default: 1 },
            { fieldtype: "Check", fieldname: "shipping_label", label: __("Shipping Label"), default: 1 }
        ],
        primary_action_label: __("Print Selected"),
        primary_action: async function (values) {
            if (!values.packing_slip && !values.shipping_label) {
                frappe.msgprint(__("Please select at least one document."));
                return;
            }

            if (values.packing_slip) {
                const r = await frappe.db.get_value("Packing Slip", { delivery_note: frm.doc.name }, "name");

                const settings_list = await frappe.db.get_list(
                    "Easypost Settings",
                    {
                        filters: {
                            enabled: 1
                        },
                        fields: ["*"]
                    }
                );

                alert(JSON.stringify(settings_list))
                const print_format =
                    settings_list[0]?.print_format_for_packing_slip || "Standard";

                if (r.message && r.message.name) {
                    const url = frappe.urllib.get_full_url(
                        `/printview?doctype=Packing Slip` +
                        `&name=${encodeURIComponent(r.message.name)}` +
                        `&trigger_print=1` +
                        `&format=${encodeURIComponent(print_format || "Standard")}` +
                        `&no_letterhead=1` +
                        `&letterhead=No Letterhead`
                    );
                    window.open(url, "_blank");
                } else {
                    frappe.msgprint(__("Packing Slip not found."));
                }
            }

            if (values.shipping_label) {
                if (frm.doc.custom_label_url) {
                    window.open(frm.doc.custom_label_url, "_blank");
                } else {
                    frappe.msgprint(__("Shipping Label not found."));
                }
            }

            d.hide();
        }
    });

    d.show();
}

async function print_selected_documents_v2(frm) {

    const settings_list = await frappe.db.get_list(
        "Easypost Settings",
        {
            filters: {
                enabled: 1
            },
            fields: [
                "name",
                "print_label",
                "print_packing_slip",
                "printer_network_host",
                "api_key",
                "port_for_shipping_label",
                "port_for_packing_slip",
                "printer",
                "default_number_of_copies"
            ],
            limit: 1
        }
    );

    if (!settings_list.length) {
        frappe.msgprint({
            title: __("Easypost Settings Not Found"),
            indicator: "orange",
            message: __("Please enable an Easypost Settings record.")
        });
        return;
    }

    const e_config = settings_list[0];

    console.log("Printer Configuration:", e_config);

    const printer_host = e_config.printer_network_host;
    const api_key = e_config.api_key;
    const port_for_shipping_label = e_config.port_for_shipping_label;
    const port_for_packing_slip = e_config.port_for_packing_slip;
    const printer = e_config.printer;
    const copies = e_config.default_number_of_copies || 1;

    // ==========================================
    // VALIDATION
    // ==========================================

    if (!printer_host) {
        frappe.throw(__("Printer Network Host is not configured."));
    }

    if (!printer) {
        frappe.throw(__("Printer is not configured."));
    }


    const to_be_printed = [];

    if (e_config.print_label) {
        to_be_printed.push(__("Shipping Label"));
    }

    if (e_config.print_packing_slip) {
        to_be_printed.push(__("Packing Slip"));
    }

    if (!to_be_printed.length) {
        frappe.msgprint({
            title: __("Nothing to Print"),
            indicator: "orange",
            message: __(
                "Please enable at least one print option in Easypost Settings."
            )
        });
        return;
    }


    try {

        frappe.dom.freeze(
            __("Preparing {0}...", [
                to_be_printed.join(", ")
            ])
        );


        // ==========================================
        // BUILD ENDPOINTS
        // ==========================================

        const proxy_url_for_shipping_label =
            `http://${printer_host}:${port_for_shipping_label}`;

        const proxy_url_for_packing_slip =
            `http://${printer_host}:${port_for_packing_slip}`;


        console.log(
            "Shipping Label URL:",
            proxy_url_for_shipping_label
        );

        console.log(
            "Packing Slip URL:",
            proxy_url_for_packing_slip
        );


        // ==========================================
        // PRINT SHIPPING LABEL
        // ==========================================

        if (e_config.print_label) {

            const r = await frappe.call({
                method: "easypost_connector.api.api.convert_png_to_bw",
                args: {
                    shipment_id:
                        frm.doc.custom_easypost_shipment_id,
                    docname: frm.doc.name
                }
            });

            const result = r.message;

            if (!result?.success || !result?.zpl_bytes) {
                throw new Error(
                    result?.message ||
                    __("Unable to generate shipping label.")
                );
            }


            await print_document(
                frm.doc,
                "Shipping Label",
                result.zpl_bytes,
                proxy_url_for_shipping_label,
                api_key,
                printer,
                copies
            );
        }


        // ==========================================
        // PRINT PACKING SLIP
        // ==========================================

        if (e_config.print_packing_slip) {

            await print_document(
                frm.doc,
                "Packing Slip",
                null,
                proxy_url_for_packing_slip,
                api_key,
                printer,
                copies
            );
        }


        frappe.show_alert({
            message: __(
                `${to_be_printed.join(" and ")} sent to printer successfully.`
            ),
            indicator: "green"
        });

    } catch (error) {

        console.error("Printing Error:", error);

        frappe.msgprint({
            title: __("Printing Failed"),
            indicator: "red",
            message: error.message || String(error)
        });

    } finally {

        frappe.dom.unfreeze();
    }
}

async function print_document(
    document,
    document_name,
    byte_data,
    proxy_url,
    api_key,
    printer,
    copies = 1
) {

    try {

        let endpoint;
        let body;

        if (document_name === "Shipping Label") {

            if (!byte_data) {
                throw new Error(
                    "No shipping label data provided."
                );
            }

            endpoint = `${proxy_url}/print`;

            body = {
                printer,
                zpl: byte_data,
                copies
            };

        } else if (document_name === "Packing Slip") {

            endpoint = `${proxy_url}/api/print`;

            body = {
                erp_url: window.location.origin,
                order_names: [document.name],
                copies,
                printer
            };

        } else {

            throw new Error(
                `Unsupported document type: ${document_name}`
            );
        }

        const response = await fetch(endpoint, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...(api_key && {
                    "X-API-Key": api_key
                })
            },
            body: JSON.stringify(body)
        });

        const responseText = await response.text();


        if (!response.ok) {
            throw new Error(
                `Print service returned HTTP ${response.status}: ${responseText}`
            );
        }


        let data;

        try {
            data = JSON.parse(responseText);
        } catch {
            data = { message: responseText };
        }


        if (data.success === false) {
            throw new Error(
                data.error ||
                data.message ||
                "Printer returned an unknown error."
            );
        }



        return data;

    } catch (error) {

        console.error(
            "PRINT DOCUMENT ERROR:",
            error
        );

        throw error;
    }
}



frappe.ui.form.on("Delivery Note", {


    custom_preview_label: function (frm) {

        if (!frm.doc.custom_label_url) {
            frappe.msgprint("No label URL found.");
            return;
        }

        frm.fields_dict.custom_label_preview.$wrapper.html(`
    < div style = "
padding: 15px;
text - align: center;
background: #f5f5f5;
">
    < h4 > Label Preview</h4 >

        <img
            src="${frm.doc.custom_label_url}"
            style="
                    max-width: 100%;
                    max-height: 700px;
                    height: auto;
                    border: 1px solid #ccc;
                    background: white;
                "
        />
        </div >
    `);
    }
    ,
    refresh: async function (frm) {

        // ==================================
        // Get Enabled Easypost Settings
        // ==================================
        let settings_list = [];

        try {

            settings_list = await frappe.db.get_list(
                "Easypost Settings",
                {
                    filters: {
                        enabled: 1
                    },
                    fields: ["name"],
                    limit: 1
                }
            );

        } catch (error) {

            console.error(
                "Error fetching Easypost Settings:",
                error
            );
        }


        // ==================================
        // Apply Packing Box Item Group Filter
        // ==================================
        if (settings_list.length) {

            try {

                const settings = await frappe.db.get_doc(
                    "Easypost Settings",
                    settings_list[0].name
                );

                const groups = (
                    settings.packing_box_item_group || []
                ).map(row => row.item_group);


                if (
                    frm.fields_dict.custom_packing_box_details &&
                    frm.fields_dict.custom_packing_box_details.grid
                ) {

                    frm.fields_dict
                        .custom_packing_box_details
                        .grid
                        .get_field("packing_box")
                        .get_query = function () {

                            return {
                                filters: {
                                    item_group: ["in", groups]
                                }
                            };
                        };

                    frm.refresh_field(
                        "custom_packing_box_details"
                    );
                }

            } catch (error) {

                console.error(
                    "Error loading Easypost Settings:",
                    error
                );
            }

        } else {

            console.warn(
                "No enabled Easypost Settings found."
            );
        }


        // ============================
        // Initialize Tracking
        // ============================
        init_tracker(frm);


        // ============================
        // Process Shipment Button
        // ============================
        if (!frm.doc.custom_label_created && !frm.is_new()) {

            frm.add_custom_button(
                __("Process Shipment"),
                async function () {

                    await frm.set_value(
                        "custom_show_progress",
                        1
                    );

                    await frm.save();
                }
            );
        }
    },

    // ============================
    // After Save
    // ============================
    after_save: async function (frm) {

        if (frm.doc.docstatus !== 0) return;

        if (
            frm.doc.custom_initial_fetch &&
            frm.doc.custom_show_progress
        ) {
            return;
        }

        if (frm.__initial_fetch_running) {
            return;
        }

        frm.__initial_fetch_running = true;

        try {

            await fetch_shipping_rates(frm);

            await frm.set_value(
                "custom_initial_fetch",
                1
            );

            await frm.save();

        } finally {

            frm.__initial_fetch_running = false;
        }
    },

});

frappe.ui.form.on("Packaging Box Details", {
    packing_box: function (frm, cdt, cdn) { fetch_dimensions(frm, cdt, cdn); },
    quantity: function (frm, cdt, cdn) { fetch_dimensions(frm, cdt, cdn); }
});

frappe.ui.form.on("Shipment Parcel", {
    length(frm, cdt, cdn) {
        dimension_changed(frm, "length");
    },

    width(frm, cdt, cdn) {
        dimension_changed(frm, "width");
    },

    height(frm, cdt, cdn) {
        dimension_changed(frm, "height");
    },

    weight(frm, cdt, cdn) {
        dimension_changed(frm, "weight");
    }
});

frappe.ui.form.on("Carrier Delivery Rate Table", {
    create_label: async function (frm, cdt, cdn) {
        const row = frappe.get_doc(cdt, cdn);

        if (!row.create_label) {
            return;
        }

        if (!frm.doc.custom_show_progress) {
            frappe.msgprint(
                __("Please click <b>Process Shipment</b> first, then select a shipping rate.")
            );

            await frappe.model.set_value(
                cdt,
                cdn,
                "create_label",
                0
            );

            return;
        }

    }
});




