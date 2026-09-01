import frappe
import requests
from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
from frappe.utils import get_datetime, convert_utc_to_system_timezone
from PIL import Image
from io import BytesIO
from requests.auth import HTTPBasicAuth
import json
import hmac
import hashlib
import frappe
import io
import math
import socket
import pymupdf as fitz
from frappe import _
from frappe.utils.file_manager import save_file




def get_easypost_settings():

    settings = frappe.get_all(
        "Easypost Settings",
        filters={
            "enabled": 1
        },
        fields=["name"],
        limit=1
    )

    if not settings:
        frappe.throw("No enabled Easypost Settings found.")

    return frappe.get_doc(
        "Easypost Settings",
        settings[0].name
    )

EasyPostSettings = get_easypost_settings()
api_key = None

mode = EasyPostSettings.mode

if mode == "test":
    api_key = EasyPostSettings.test_key
else:
    api_key = EasyPostSettings.production_key

BASE_URL = EasyPostSettings.base_url
VERSION = EasyPostSettings.version
TARGET_DPI = EasyPostSettings.dpi or 300




@frappe.whitelist()
def create_easypost_shipment(doc=None, method=None, delivery_note=None):
    if not delivery_note :
        return 
    
    if not doc and delivery_note:
        dn = frappe.get_doc("Delivery Note", delivery_note)
    else:
        dn = frappe.get_doc("Delivery Note", doc.name)

    sales_order = None

    for item in dn.items:
        if item.against_sales_order:
            sales_order = item.against_sales_order
            break

    if not sales_order:
        frappe.throw("Delivery Note is not linked to a Sales Order.")

    so = frappe.get_doc("Sales Order", sales_order)

    shipping_address = frappe.get_doc("Address", so.shipping_address_name)

    billing_address = (
        frappe.get_doc("Address", so.customer_address)
        if so.customer_address else None
    )

    company_address = frappe.get_doc("Address", so.company_address)

    if not shipping_address:
        shipping_address = billing_address
        
    if len(dn.custom_shipment_parcel_dimensions) == 0:
        return

    
    parcel = dn.custom_shipment_parcel_dimensions[0]
    
    

    payload = {
        "shipment": {
            "to_address": {
                "name": so.customer_name,
                "street1": shipping_address.address_line1,
                "street2": shipping_address.address_line2 or "",
                "city": shipping_address.city,
                "state": shipping_address.state,
                "zip": shipping_address.pincode,
                "country": shipping_address.country,
                "phone": shipping_address.phone or "0000000000",
                "email": shipping_address.email_id or "user@example.com"
            },
            "from_address": {
                "name": so.company,
                "street1": company_address.address_line1,
                "street2": company_address.address_line2 or "",
                "city": company_address.city,
                "state": company_address.state,
                "zip": company_address.pincode,
                "country": company_address.country,
                "phone": company_address.phone or "",
                "email": company_address.email_id or ""
            },
            "parcel": {
                "length": parcel.length,
                "width": parcel.width,
                "height": parcel.height,
                "weight": parcel.weight
            }
        }
    }

    response = requests.post(
        f"{BASE_URL}/{VERSION}/shipments",
        auth=(api_key, ""),
        json=payload,
        timeout=60
    )

    if response.status_code >= 400:
        frappe.throw(response.text)

    return response.json()


@frappe.whitelist()
def buy_shipment(delivery_note, rate_id=None):

    if not delivery_note:
        return
    
    dn = frappe.get_doc("Delivery Note", delivery_note)

    selected_rate = next(
        (r for r in dn.custom_rate if r.create_label),
        None
    )

    if not selected_rate:
        frappe.throw("Please select a shipping rate.")

    insurance = dn.custom_insurance_cost or 0

    payload = {
        "rate": {
            "id": selected_rate.rate_id
        }
    }

    if insurance > 0:
        payload["insurance"] = str(insurance)

    response = requests.post(
        f"{BASE_URL}/{VERSION}/shipments/{selected_rate.shipment_id}/buy",
        auth=(api_key, ""),
        json=payload,
        timeout=60
    )

    if response.status_code >= 400:
        frappe.throw(response.text)

    shipment = response.json()

    postage_label = shipment.get("postage_label", {})
    tracker = shipment.get("tracker", {})

    return {
        "shipment_id": shipment.get("id"),
        "tracking_number": tracker.get("tracking_code"),
        "tracking_url": tracker.get("public_url"),
        "label_url": postage_label.get("label_url"),
        "tracking_status": tracker.get("status"),
        "tracking_status_details": tracker.get("status_detail")
    }
    
from frappe.utils import (
    get_datetime,
    convert_utc_to_system_timezone,
)


@frappe.whitelist(allow_guest=True)
def easypost_webhook():
    frappe.set_user("Administrator")

    raw_body = frappe.request.get_data()
    headers = frappe.request.headers
    frappe.log_error(
        title="EasyPost Webhook Received",
        message=f"Headers: {headers}\n\nBody: {raw_body.decode('utf-8')}"
    )
    signature = frappe.request.headers.get("X-Hmac-Signature")

    secret = frappe.conf.get("easypost_webhook_secret")
    if secret:
        expected = "hmac-sha256-hex=" + hmac.new(
            secret.encode(),
            raw_body,
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected, signature or ""):
            frappe.local.response.http_status_code = 401
            return {"error": "invalid signature"}

    try:
        event = json.loads(raw_body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        frappe.local.response.http_status_code = 400
        return {"error": "invalid json"}

    if not isinstance(event, dict):
        frappe.local.response.http_status_code = 400
        return {"error": "unexpected payload shape"}

    if event.get("description") == "tracker.updated":

        tracker = event.get("result", {})

        shipment_id = tracker.get("shipment_id")
        tracking_code = tracker.get("tracking_code")

        new_status = tracker.get("status")
        new_status_detail = tracker.get("status_detail")

        carrier = tracker.get("carrier")
        updated_at = tracker.get("updated_at")
        est_delivery_date = tracker.get("est_delivery_date")

        # Optional: make carrier names user friendly
        carrier_map = {
            "UPSDAP": "UPS",
            "USPS": "USPS",
            "FedExDefault": "FedEx",
            "DhlEcs": "DHL eCommerce",
            "CanadaPost": "Canada Post",
        }

        carrier = carrier_map.get(carrier, carrier)

        last_updated = None
        if updated_at:
            last_updated = convert_utc_to_system_timezone(
                get_datetime(updated_at)
            )

        estimated_delivery = None
        if est_delivery_date:
            estimated_delivery = convert_utc_to_system_timezone(
                get_datetime(est_delivery_date)
            )

        dn = frappe.db.get_value(
            "Delivery Note",
            {"custom_easypost_shipment_id": shipment_id},
            "name"
        )

        if not dn and tracking_code:
            dn = frappe.db.get_value(
                "Delivery Note",
                {"custom_tracking_number": tracking_code},
                "name"
            )

        if dn:
            frappe.db.set_value(
                "Delivery Note",
                dn,
                {
                    "custom_tracking_status": new_status,
                    "custom_tracking_status_details": new_status_detail,
                    "custom_last_updated_": last_updated,
                    "custom_carrier_name": carrier,
                    "custom_estimated_delivery_date": estimated_delivery
                }
            )
            frappe.db.commit()

        else:
            frappe.log_error(
                f"No Delivery Note found for shipment_id={shipment_id}, tracking_code={tracking_code}",
                "EasyPost Webhook"
            )

    frappe.local.response.http_status_code = 200
    return {"status": "ok"}
    

@frappe.whitelist()
def convert_png_to_bw(shipment_id, docname):

    import base64

    # --------------------------------------------
    # Get Delivery Note
    # --------------------------------------------
    doc = frappe.get_doc(
        "Delivery Note",
        docname
    )

    # --------------------------------------------
    # Get Shipment from EasyPost
    # --------------------------------------------
    response = requests.get(
        f"{BASE_URL}/{VERSION}/shipments/{shipment_id}",
        auth=HTTPBasicAuth(api_key, "")
    )

    if response.status_code != 200:
        frappe.throw(
            response.text
        )

    shipment = response.json()

    # --------------------------------------------
    # Get PNG Label URL
    # --------------------------------------------
    png_url = shipment[
        "postage_label"
    ]["label_url"]

    # --------------------------------------------
    # Download PNG
    # --------------------------------------------
    img_response = requests.get(
        png_url
    )

    img_response.raise_for_status()

    # --------------------------------------------
    # PNG → RAW BINARY ZPL (^GFB)
    # --------------------------------------------
    zpl_bytes = png_bytes_to_zpl(
        img_response.content,
        source_dpi=TARGET_DPI
    )

    # --------------------------------------------
    # Convert RAW binary ZPL → Base64
    #
    # IMPORTANT:
    # Base64 is ONLY for API / JSON transport.
    # Do NOT UTF-8 decode the ZPL.
    # --------------------------------------------
    zpl_base64 = base64.b64encode(
        zpl_bytes
    ).decode("ascii")

    # --------------------------------------------
    # Save RAW binary ZPL file
    # --------------------------------------------
    # file_doc = save_file(
    #     f"{shipment_id}.zpl",
    #     zpl_bytes,
    #     "Delivery Note",
    #     docname,
    #     is_private=0
    # )

    # --------------------------------------------
    # Update Delivery Note
    # --------------------------------------------
    # frappe.db.set_value(
    #     "Delivery Note",
    #     docname,
    #     {
    #         # "custom_zpl_file": file_doc.file_url,
    #         # "custom_zpl_file_url": file_doc.file_url
    #     }
    # )

    frappe.db.commit()

    # --------------------------------------------
    # Return Base64 ZPL for Printer Proxy
    # --------------------------------------------
    return {
        "success": True,
        "zpl_bytes": zpl_base64,
        "message": "ZPL file created and attached successfully."
    }

@frappe.whitelist()
def print_label(shipment_id, docname):

    settings = get_easypost_settings()

    if not settings.host_ip or not settings.port:
        return {
            "success": False,
            "print_status": "Printer not configured."
        }

    host = settings.host_ip
    port = int(settings.port or 6101)

    print_status = []

    # =====================================================
    # SHIPPING LABEL
    # =====================================================

    if settings.print_label:

        try:
            response = requests.get(
                f"{BASE_URL}/{VERSION}/shipments/{shipment_id}",
                auth=HTTPBasicAuth(api_key, "")
            )

            if response.status_code != 200:
                frappe.throw(response.text)

            shipment = response.json()

            png_url = shipment["postage_label"]["label_url"]

            # Download Shipping Label PNG
            img_response = requests.get(png_url)
            img_response.raise_for_status()

            # PNG -> ZPL
            shipping_zpl = png_bytes_to_zpl(
                img_response.content,
                source_dpi=TARGET_DPI
            )

            # Send to printer
            shipping_status = print_zpl(
                host=host,
                port=port,
                zpl_bytes=shipping_zpl,
                copies=1
            )

            # Save binary ZPL safely as Base64
            import base64

            shipping_zpl_base64 = base64.b64encode(
                shipping_zpl
            ).decode("ascii")

            frappe.db.set_value(
                "Delivery Note",
                docname,
                "custom_zpl_file_content",
                shipping_zpl_base64
            )

            print_status.append(
                f"Shipping Label: {shipping_status}"
            )

        except Exception:

            frappe.log_error(
                frappe.get_traceback(),
                "Shipping Label Print Failed"
            )

            print_status.append(
                "Shipping Label: Print failed."
            )

    # =====================================================
    # PACKING SLIP
    # =====================================================

    if settings.print_packing_slip:

        try:

            packing_slip_zpl = packing_slip_to_zpl(
                docname
            )

            packing_slip_status = print_zpl(
                host=host,
                port=port,
                zpl_bytes=packing_slip_zpl,
                copies=1
            )

            print_status.append(
                f"Packing Slip: {packing_slip_status}"
            )

        except Exception:

            frappe.log_error(
                frappe.get_traceback(),
                "Packing Slip Print Failed"
            )

            print_status.append(
                "Packing Slip: Print failed."
            )

    # =====================================================
    # NOTHING SELECTED
    # =====================================================

    if not print_status:

        return {
            "success": False,
            "print_status": (
                "Please enable Print Label or "
                "Print Packing Slip in Easy Post Settings."
            )
        }

    frappe.db.commit()

    return {
        "success": True,
        "print_status": "\n".join(print_status)
    }

# =========================== new conversion logic ==================================
def png_bytes_to_zpl(png_bytes: bytes, source_dpi: int = None) -> bytes:
    """
    Convert EasyPost PNG label to ZPL (^GFB binary format).

    ```
    USPS : 300 DPI (1200x1800)
    UPS  : 200 DPI (800x1400) -> scaled to 300 DPI
    """

    img = Image.open(io.BytesIO(png_bytes)).convert("L")

    # Auto-detect source DPI
    if source_dpi is None:
        source_dpi = 200 if img.width <= 850 else TARGET_DPI

    # Scale to 300 DPI if required
    if source_dpi != TARGET_DPI:
        scale = TARGET_DPI / source_dpi
        img = img.resize(
            (
                round(img.width * scale),
                round(img.height * scale),
            ),
            Image.LANCZOS,
        )

    # Convert to monochrome after scaling
    img = img.convert("1")

    w_px, h_px = img.size

    bytes_per_row = math.ceil(w_px / 8)
    total_bytes = bytes_per_row * h_px

    raw = bytearray()
    pixels = img.load()

    # Pack pixels into binary bytes
    for y in range(h_px):
        for x in range(0, w_px, 8):
            byte = 0

            for bit in range(8):
                xx = x + bit

                # PIL: 0 = black
                # ZPL binary: bit 1 = black
                if xx < w_px and pixels[xx, y] == 0:
                    byte |= (1 << (7 - bit))

            raw.append(byte)

    # Build ZPL with binary ^GFB image data
    zpl = (
        f"^XA\n"
        f"^CI28\n"
        f"^PW{w_px}\n"
        f"^LL{h_px}\n"
        f"^FO0,0\n"
    ).encode("ascii")

    # ^GFB = binary graphic data
    # Format: ^GFB,data_bytes,total_bytes,bytes_per_row,data
    zpl += f"^GFB,{len(raw)},{total_bytes},{bytes_per_row},".encode("ascii")

    # Append RAW binary pixel data directly
    zpl += bytes(raw)

    zpl += b"\n^FS\n^XZ\n"

    return bytes(zpl)

def print_zpl(host: str, port: int, zpl_bytes: bytes,
              copies: int = 1, timeout: int = 10) -> str:
    """
    Send ZPL bytes to a Zebra printer via TCP socket.

    Args:
        host:      Printer IP address
        port:      Printer port (6101 for Zebra default)
        zpl_bytes: ZPL content as bytes (from png_bytes_to_zpl or file)
        copies:    Number of copies to print
        timeout:   Socket timeout in seconds

    Returns:
        Status string describing what was sent

    Raises:
        OSError: If connection fails or printer unreachable
    """
    if isinstance(zpl_bytes, str):
        # Safety: if accidentally passed a string, encode with latin-1
        # (latin-1 is byte-transparent unlike UTF-8)
        zpl_bytes = zpl_bytes.encode("latin-1")

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect((host, port))
    for _ in range(copies):
        s.sendall(zpl_bytes)
    try:
        response = s.recv(1024)
    except Exception:
        response = b""
    s.close()

    return (f"Sent {copies} label(s) ({len(zpl_bytes):,} bytes) to {host}:{port}" +
            (f" — printer: {repr(response)}" if response else ""))


import fitz
from PIL import Image
from io import BytesIO


LABEL_WIDTH_INCH = 4
LABEL_HEIGHT_INCH = 6

TARGET_DPI = 300

LABEL_WIDTH_PX = LABEL_WIDTH_INCH * TARGET_DPI    # 1200
LABEL_HEIGHT_PX = LABEL_HEIGHT_INCH * TARGET_DPI  # 1800


from PIL import Image
from io import BytesIO
import fitz


def packing_slip_to_zpl(docname):
    """
    Generate Packing Slip PDF, render at high resolution,
    downsample to exact 4x6 label dimensions, then convert to ZPL.
    """

    settings = get_easypost_settings()

    print_format = settings.print_format_for_packing_slip

    if not print_format:
        frappe.throw(
            _("Print Format for Packing Slip is not configured.")
        )

    # -----------------------------------------
    # Find Packing Slip linked to Delivery Note
    # -----------------------------------------

    packing_slip_name = frappe.db.get_value(
        "Packing Slip",
        {"delivery_note": docname},
        "name"
    )

    if not packing_slip_name:
        frappe.throw(
            _("Packing Slip not found for Delivery Note {0}.")
            .format(docname)
        )

    # -----------------------------------------
    # Generate Packing Slip PDF
    # -----------------------------------------

    pdf_bytes = frappe.get_print(
        "Packing Slip",
        packing_slip_name,
        print_format=print_format,
        as_pdf=True,
        no_letterhead=1
    )

    if not pdf_bytes:
        frappe.throw(
            _("Unable to generate Packing Slip PDF.")
        )

    # -----------------------------------------
    # PDF -> High Resolution PNG -> ZPL
    # -----------------------------------------

    pdf = fitz.open(
        stream=pdf_bytes,
        filetype="pdf"
    )

    zpl_parts = []

    # Render at 2x final resolution
    RENDER_SCALE = 4

    HIGH_RES_WIDTH = LABEL_WIDTH_PX * RENDER_SCALE
    HIGH_RES_HEIGHT = LABEL_HEIGHT_PX * RENDER_SCALE

    try:

        for page in pdf:

            page_rect = page.rect

            # Render PDF at high resolution
            zoom_x = HIGH_RES_WIDTH / page_rect.width
            zoom_y = HIGH_RES_HEIGHT / page_rect.height

            matrix = fitz.Matrix(
                zoom_x,
                zoom_y
            )

            pix = page.get_pixmap(
                matrix=matrix,
                alpha=False
            )

            img = Image.open(
                BytesIO(pix.tobytes("png"))
            ).convert("L")

            print(
                "High resolution render:",
                img.size
            )

            # Downsample to exact printer resolution
            img = img.resize(
                (
                    LABEL_WIDTH_PX,
                    LABEL_HEIGHT_PX
                ),
                Image.Resampling.LANCZOS
            )

            print(
                "Final label size:",
                img.size
            )

            # Save PNG
            output = BytesIO()

            img.save(
                output,
                format="PNG",
                dpi=(TARGET_DPI, TARGET_DPI),
                optimize=True
            )

            png_bytes = output.getvalue()

            # Convert PNG -> ZPL
            zpl_bytes = png_bytes_to_zpl(
                png_bytes,
                source_dpi=TARGET_DPI
            )

            zpl_parts.append(zpl_bytes)

    finally:
        pdf.close()

    if not zpl_parts:
        frappe.throw(
            _("No pages found in Packing Slip.")
        )

    return b"\n".join(zpl_parts)


@frappe.whitelist()
def verify_address(address_name, doc_name, doctype):

    doc = frappe.get_doc(doctype, doc_name)
    address = frappe.get_doc("Address", address_name)
    
    if not address:
        frappe.throw(" Shipping Address not found.")

    payload = {
        "address": {
            "name": address.address_title,
            "street1": address.address_line1,
            "street2": address.address_line2,
            "city": address.city,
            "state": address.state,
            "zip": address.pincode,
            "country": address.country,
            "phone": address.phone,
            "email": address.email_id,
        }
    }

    payload["address"] = {
        k: v for k, v in payload["address"].items() if v
    }

    response = requests.post(
        f"{BASE_URL}/{VERSION}/addresses/create_and_verify",
        auth=HTTPBasicAuth(api_key, ""),
        json=payload,
        timeout=30
    )

    data = response.json()

    if response.status_code not in (200, 201):
        error = data.get("error", {})
        errors = error.get("errors", [])

        error_message = "<br>".join(
            f"• {err.get('message')}" for err in errors
        )

        frappe.throw(
            title="Address Verification Failed",
            msg=error_message or error.get("message")
        )

    # Mark verified
    doc.db_set("custom_is_address_verified", 1)



    steps = get_steps(doc)

    steps["address_verified"] = True

    save_steps(doc, steps)

    return {
        "verified": True,
        "steps": steps
    }
    
@frappe.whitelist()
def submit_sales_order(sales_order):

    so = frappe.get_doc("Sales Order", sales_order)

    steps = get_steps(so)

    # Already completed
    if steps.get("sales_order_submitted"):
        return {
            "success": True,
            "message": "Sales Order already submitted.",
            "steps": steps
        }

    # Address must be verified first
    if not steps.get("address_verified"):
        frappe.throw("Please verify the address before submitting the Sales Order.")

    # Already submitted in ERPNext but step not updated
    if so.docstatus == 1:
        steps["sales_order_submitted"] = True
        save_steps(so, steps)

        return {
            "success": True,
            "message": "Sales Order already submitted.",
            "steps": steps
        }

    # Submit Sales Order
    so.submit()
    so.reload()

    if so.docstatus != 1:
        frappe.throw("Sales Order submission failed.")

    steps["sales_order_submitted"] = True
    save_steps(so, steps)

    frappe.db.commit()

    return {
        "success": True,
        "message": "Sales Order submitted successfully.",
        "steps": steps
    }

@frappe.whitelist()
def create_delivery_note(sales_order):

    so = frappe.get_doc("Sales Order", sales_order)

    steps = get_steps(so)

    # Already created
    if steps.get("delivery_note"):
        return steps["delivery_note"]

    dn = make_delivery_note(sales_order)
    dn.insert(ignore_permissions=True)

    steps["delivery_note"] = dn.name

    save_steps(so, steps)

    frappe.db.commit()

    return dn.name

@frappe.whitelist()
def create_packing_slip(
    delivery_note,
    from_case_no=None,
    to_case_no=None,
    gross_weight=None,
    net_weight=None,
    box_weight=0
):
    dn = frappe.get_doc("Delivery Note", delivery_note)

    if not dn.items:
        frappe.throw("Delivery Note has no items.")

    so_name = dn.items[0].against_sales_order
    so = frappe.get_doc("Sales Order", so_name)

    steps = get_steps(so)

    # --------------------------------------------------
    # 1. Check Packing Slip stored in steps
    # --------------------------------------------------
    packing_slip_name = steps.get("packing_slip")

    if packing_slip_name and frappe.db.exists(
        "Packing Slip",
        packing_slip_name
    ):
        ps = frappe.get_doc(
            "Packing Slip",
            packing_slip_name
        )

        return {
            "packing_slip": ps.name,
            "status": "exists",
            "from_case_no": ps.from_case_no,
            "to_case_no": ps.to_case_no,
            "gross_weight": ps.gross_weight_pkg,
            "net_weight": ps.net_weight_pkg,
            "gross_weight_uom": ps.gross_weight_uom,
        }

    # --------------------------------------------------
    # 2. Check if Packing Slip already exists
    #    for this Delivery Note
    # --------------------------------------------------
    existing_packing_slip = frappe.db.get_value(
        "Packing Slip",
        {
            "delivery_note": delivery_note
        },
        "name"
    )

    if existing_packing_slip:

        ps = frappe.get_doc(
            "Packing Slip",
            existing_packing_slip
        )

        # Update stale/missing step reference
        steps["packing_slip"] = ps.name
        save_steps(so, steps)

        return {
            "packing_slip": ps.name,
            "status": "exists",
            "from_case_no": ps.from_case_no,
            "to_case_no": ps.to_case_no,
            "gross_weight": ps.gross_weight_pkg,
            "net_weight": ps.net_weight_pkg,
            "gross_weight_uom": ps.gross_weight_uom,
        }

    # --------------------------------------------------
    # 3. No Packing Slip exists → create a new one
    # --------------------------------------------------
    ps = frappe.new_doc("Packing Slip")

    ps.delivery_note = delivery_note

    next_case_no = ps.get_recommended_case_no()

    ps.gross_weight_pkg = gross_weight or 0
    ps.net_weight_pkg = net_weight or 0

    ps.from_case_no = (
        from_case_no or next_case_no
    )

    ps.to_case_no = (
        to_case_no or next_case_no
    )

    total_net_weight = 0

    for item in dn.items:

        remaining_qty = item.qty - (
            item.packed_qty or 0
        )

        if remaining_qty <= 0:
            continue

        item_doc = frappe.get_cached_doc(
            "Item",
            item.item_code
        )

        item_net_weight = (
            (item_doc.weight_per_unit or 0)
            * remaining_qty
        )

        total_net_weight += item_net_weight

        ps.append(
            "items",
            {
                "item_code": item.item_code,
                "item_name": item.item_name,
                "description": item.description,
                "qty": remaining_qty,
                "stock_uom": item.stock_uom,
                "dn_detail": item.name,
                "net_weight": item_net_weight
            }
        )

    ps.net_weight_pkg = total_net_weight

    ps.insert(ignore_permissions=True)

    # Save newly created Packing Slip reference
    steps["packing_slip"] = ps.name

    # Reset statuses because this is a new Packing Slip
    steps["packing_slip_submitted"] = False
    steps["completed"] = False

    save_steps(so, steps)

    return {
        "packing_slip": ps.name,
        "status": "created",
        "from_case_no": ps.from_case_no,
        "to_case_no": ps.to_case_no,
        "net_weight": ps.net_weight_pkg,
        "gross_weight_uom": ps.gross_weight_uom,
    }
    

@frappe.whitelist()
def complete_packing_slip(
    packing_slip,
    gross_weight,
    net_weight,
    gross_weight_uom,
    from_case_no=None,
    to_case_no=None
):

    ps = frappe.get_doc("Packing Slip", packing_slip)

    if ps.docstatus == 1:
        return ps.name

    ps.gross_weight_pkg = gross_weight
    ps.gross_weight_uom = gross_weight_uom
    ps.net_weight_pkg = net_weight
    
    if from_case_no:
        ps.from_case_no = from_case_no

    if to_case_no:
        ps.to_case_no = to_case_no

    ps.save(ignore_permissions=True)
    ps.submit()

    delivery_note = frappe.get_doc("Delivery Note", ps.delivery_note)
    so = frappe.get_doc("Sales Order", delivery_note.items[0].against_sales_order)

    steps = get_steps(so)
    steps["packing_slip_submitted"] = True
    steps["completed"] = True
    save_steps(so, steps)
    
    settings = get_easypost_settings()

    print_status = []

    # Skip printing if printer is not configured
    if not settings.host_ip:
        print_status.append("Printer not configured.")

    else:
        host = settings.host_ip
        port = int(settings.port or 6101)

        try:
            packing_slip_zpl = packing_slip_to_zpl(ps.name)

            if packing_slip_zpl:
                status = print_zpl(
                    host=host,
                    port=port,
                    zpl_bytes=packing_slip_zpl,
                    copies=1
                )

                print_status.append(
                    f"Packing Slip: {status}"
                )

            else:
                print_status.append(
                    "Packing Slip ZPL could not be generated. Printing skipped."
                )

        except Exception as e:
            frappe.log_error(
                frappe.get_traceback(),
                "Packing Slip Printing Error"
            )

            print_status.append(
                f"Packing Slip printing failed: {str(e)}"
            )

    frappe.db.commit()

    return {
        "success": True,
        "packing_slip": ps.name,
        "print_status": print_status
    }


def get_steps(doc):
    if not doc.custom_executed_steps:
        return {
            "address_verified": False,
            "sales_order_submitted": False,
            "delivery_note": None,
            "packing_slip": None,
            "completed": False
        }

    try:
        return json.loads(doc.custom_executed_steps)
    except Exception:
        return {
            "address_verified": False,
            "sales_order_submitted": False,
            "delivery_note": None,
            "packing_slip": None,
            "completed": False
        }


def save_steps(doc, steps):
    doc.db_set(
        "custom_executed_steps",
        json.dumps(steps),
        update_modified=False
    )

# import base64
# import math
# import io
# import socket
# import re

# import frappe


# def send_zpl(host: str, port: int, zpl_bytes: bytes):
#     s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
#     s.settimeout(10)
#     s.connect((host, port))
#     s.sendall(zpl_bytes)

#     try:
#         resp = s.recv(1024)
#     except Exception:
#         resp = b""

#     s.close()

#     return resp


# def png_bytes_to_zpl(png_bytes: bytes, source_dpi: int = None) -> bytes:
#     from PIL import Image

#     TARGET_DPI = 300

#     img = Image.open(io.BytesIO(png_bytes))

#     if source_dpi is None:
#         source_dpi = 200 if img.width <= 850 else 300

#     if source_dpi != TARGET_DPI:
#         scale = TARGET_DPI / source_dpi

#         img = img.resize(
#             (
#                 round(img.width * scale),
#                 round(img.height * scale)
#             ),
#             Image.LANCZOS
#         )

#     img = img.convert("1")

#     w_px, h_px = img.size

#     bytes_per_row = math.ceil(w_px / 8)
#     total_bytes = bytes_per_row * h_px

#     raw = bytearray()
#     pixels = img.load()

#     for y in range(h_px):
#         for x in range(0, w_px, 8):

#             byte = 0

#             for bit in range(8):
#                 if x + bit < w_px and pixels[x + bit, y] == 0:
#                     byte |= (1 << (7 - bit))

#             raw.append(byte)

#     zpl = f"^XA\n^PW{w_px}\n^LL{h_px}\n^FO0,0\n".encode("ascii")

#     zpl += (
#         f"^GFB,{len(raw)},{total_bytes},{bytes_per_row},"
#     ).encode("ascii")

#     zpl += bytes(raw)

#     zpl += b"\n^FS\n^XZ\n"
    
#     return zpl


# def autocrop_png(png_bytes: bytes, padding: int = 10) -> bytes:
#     """
#     Auto-crop a PNG to the bounding box of non-white content.
#     Adds a small padding around the content to avoid clipping.
#     Returns the cropped PNG as bytes.
#     """

#     from PIL import Image, ImageOps

#     img = Image.open(io.BytesIO(png_bytes)).convert("L")

#     # Invert so content is white on black for getbbox
#     inverted = ImageOps.invert(img)

#     bbox = inverted.getbbox()

#     if bbox is None:
#         return png_bytes

#     # Add padding, clamped to image bounds
#     w, h = img.size

#     left = max(0, bbox[0] - padding)
#     upper = max(0, bbox[1] - padding)

#     right = min(w, bbox[2] + padding)
#     lower = min(h, bbox[3] + padding)

#     cropped = img.crop((left, upper, right, lower))

#     buf = io.BytesIO()

#     cropped.save(buf, format="PNG")

#     return buf.getvalue()


# def pdf_to_png_bytes(pdf_bytes: bytes, dpi: int = 300) -> list[bytes]:
#     """
#     Convert PDF pages to PNG bytes at the given DPI.
#     """

#     try:
#         import fitz

#         doc = fitz.open(
#             stream=pdf_bytes,
#             filetype="pdf"
#         )

#         result = []

#         for page in doc:

#             mat = fitz.Matrix(
#                 dpi / 72,
#                 dpi / 72
#             )

#             pix = page.get_pixmap(
#                 matrix=mat,
#                 colorspace=fitz.csGRAY
#             )

#             result.append(
#                 pix.tobytes("png")
#             )

#         return result

#     except ImportError:

#         frappe.throw(
#             "PyMuPDF not installed. Run: pip install pymupdf"
#         )


# def detect_zpl_dpi(zpl_bytes: bytes) -> int:
#     """
#     Estimate ZPL DPI from ^PW (print width) command.
#     """

#     try:

#         text = zpl_bytes.decode(
#             "latin-1",
#             errors="ignore"
#         )

#         m = re.search(
#             r'\^PW(\d+)',
#             text,
#             re.IGNORECASE
#         )

#         if m:

#             pw = int(m.group(1))

#             # 4" label:
#             # 300dpi = 1200
#             # 200dpi = 800
#             # 203dpi = 812

#             if pw <= 850:
#                 return 200

#         return 300

#     except Exception:

#         return 300


# def rescale_zpl(
#     zpl_bytes: bytes,
#     source_dpi: int = 200,
#     target_dpi: int = 300
# ) -> bytes:
#     """
#     Rescale a ZPL file from source_dpi to target_dpi.
#     Extracts ^GFB binary image data, scales it,
#     and rebuilds the ZPL.
#     Falls back to original ZPL if ^GFB extraction fails.
#     """

#     from PIL import Image

#     text = zpl_bytes.decode(
#         "latin-1",
#         errors="ignore"
#     )

#     # Try to find ^GFB field and extract binary data
#     m = re.search(
#         r'\^GFB,(\d+),(\d+),(\d+),',
#         text
#     )

#     if m:

#         data_bytes = int(m.group(1))

#         # Find position of binary data
#         comma_pos = zpl_bytes.find(
#             b',',
#             zpl_bytes.find(b'^GFB')
#         )

#         comma_pos = zpl_bytes.find(
#             b',',
#             comma_pos + 1
#         )

#         comma_pos = zpl_bytes.find(
#             b',',
#             comma_pos + 1
#         )

#         comma_pos = zpl_bytes.find(
#             b',',
#             comma_pos + 1
#         )

#         raw_data = zpl_bytes[
#             comma_pos + 1:
#             comma_pos + 1 + data_bytes
#         ]

#         bytes_per_row = int(m.group(3))

#         w_px = bytes_per_row * 8

#         h_px = data_bytes // bytes_per_row

#         # Reconstruct image from raw bytes
#         img_raw = bytearray(data_bytes)

#         for i, byte in enumerate(raw_data):

#             # Invert for PIL
#             img_raw[i] = byte ^ 0xFF

#         img = Image.frombytes(
#             "1",
#             (w_px, h_px),
#             bytes(img_raw),
#             decoder_name="raw"
#         )

#         # Scale
#         scale = target_dpi / source_dpi

#         new_w = round(w_px * scale)
#         new_h = round(h_px * scale)

#         img = img.resize(
#             (new_w, new_h),
#             Image.LANCZOS
#         ).convert("1")

#         # Rebuild ZPL
#         new_bpr = math.ceil(new_w / 8)

#         new_total = new_bpr * new_h

#         new_raw = bytearray()

#         pixels = img.load()

#         for y in range(new_h):

#             for x in range(0, new_w, 8):

#                 byte = 0

#                 for bit in range(8):

#                     if (
#                         x + bit < new_w
#                         and pixels[x + bit, y] == 0
#                     ):
#                         byte |= (1 << (7 - bit))

#                 new_raw.append(byte)

#         # Replace dimensions and data
#         result = re.sub(
#             r'\^PW\d+',
#             f'^PW{new_w}',
#             text
#         )

#         result = re.sub(
#             r'\^LL\d+',
#             f'^LL{new_h}',
#             result
#         )

#         prefix = result[
#             :result.find('^GFB')
#         ].encode("latin-1")

#         suffix = result[
#             result.find(
#                 '^FS',
#                 result.find('^GFB')
#             ):
#         ].encode("latin-1")

#         rebuilt = prefix

#         rebuilt += (
#             f"^GFB,{len(new_raw)},"
#             f"{new_total},"
#             f"{new_bpr},"
#         ).encode("ascii")

#         rebuilt += bytes(new_raw)

#         rebuilt += suffix

#         return rebuilt

#     # Fallback
#     return zpl_bytes


# # ============================================================
# # HELPER: GET FILE CONTENT FROM FRAPPE FILE
# # ============================================================

# def get_file_bytes(file_url):

#     file_doc = frappe.get_doc(
#         "File",
#         {
#             "file_url": file_url
#         }
#     )

#     raw_bytes = file_doc.get_content()

#     filename = file_doc.file_name.lower()

#     return raw_bytes, filename


# # ============================================================
# # UPLOAD AND PRINT
# # ============================================================

# @frappe.whitelist()
# def upload_and_print(
#     file_url,
#     source_dpi=0,
#     copies=1,
#     host=None,
#     port=None
# ):
#     """
#     Accept a ZPL, PNG, or PDF label file,
#     convert to printable ZPL,
#     and send to printer.
#     """
#     doc = get_easypost_settings()

#     source_dpi = int(source_dpi or 0)
#     copies = int(copies or 1)

#     host = host or doc.host_ip
#     port = port or doc.port

#     raw_bytes, filename = get_file_bytes(file_url)

#     info = []

#     try:

#         # ====================================================
#         # ZPL
#         # ====================================================

#         if filename.endswith(".zpl"):

#             detected_dpi = (
#                 source_dpi
#                 if source_dpi > 0
#                 else detect_zpl_dpi(raw_bytes)
#             )

#             info.append(
#                 f"ZPL detected DPI: {detected_dpi}"
#             )

#             if detected_dpi < 300:

#                 info.append(
#                     f"Scaling from "
#                     f"{detected_dpi}dpi → 300dpi"
#                 )

#                 zpl_bytes = rescale_zpl(
#                     raw_bytes,
#                     source_dpi=detected_dpi,
#                     target_dpi=300
#                 )

#             else:

#                 zpl_bytes = raw_bytes

#         # ====================================================
#         # PNG
#         # ====================================================

#         elif filename.endswith(".png"):

#             from PIL import Image

#             detected_dpi = (
#                 source_dpi
#                 if source_dpi > 0
#                 else None
#             )

#             img = Image.open(
#                 io.BytesIO(raw_bytes)
#             )

#             if detected_dpi is None:

#                 detected_dpi = (
#                     200
#                     if img.width <= 850
#                     else 300
#                 )

#             info.append(
#                 f"PNG size: "
#                 f"{img.width}×{img.height}px, "
#                 f"detected DPI: {detected_dpi}"
#             )

#             cropped = autocrop_png(
#                 raw_bytes
#             )

#             img2 = Image.open(
#                 io.BytesIO(cropped)
#             )

#             if img2.size != img.size:

#                 info.append(
#                     f"Cropped to: "
#                     f"{img2.width}×{img2.height}px"
#                 )

#             zpl_bytes = png_bytes_to_zpl(
#                 cropped,
#                 source_dpi=detected_dpi
#             )

#         # ====================================================
#         # PDF
#         # ====================================================

#         elif filename.endswith(".pdf"):

#             info.append(
#                 "Converting PDF to PNG at 300dpi"
#             )

#             pages = pdf_to_png_bytes(
#                 raw_bytes,
#                 dpi=300
#             )

#             info.append(
#                 f"PDF has {len(pages)} page(s)"
#             )

#             zpl_bytes = b""

#             for i, page_png in enumerate(pages):

#                 cropped = autocrop_png(
#                     page_png
#                 )

#                 zpl_bytes += png_bytes_to_zpl(
#                     cropped,
#                     source_dpi=300
#                 )

#                 from PIL import Image

#                 img = Image.open(
#                     io.BytesIO(cropped)
#                 )

#                 info.append(
#                     f"Page {i + 1} "
#                     f"cropped to "
#                     f"{img.width}×{img.height}px "
#                     f"and converted"
#                 )

#         else:

#             frappe.throw(
#                 "Unsupported file type. "
#                 "Upload .zpl, .png, or .pdf"
#             )

#         # ====================================================
#         # PRINT
#         # ====================================================

#         info.append(
#             f"ZPL size: "
#             f"{len(zpl_bytes):,} bytes"
#         )

#         info.append(
#             f"Sending {copies} "
#             f"cop{'y' if copies == 1 else 'ies'} "
#             f"to {host}:{port}"
#         )

#         for _ in range(copies):

#             send_zpl(
#                 host,
#                 int(port),
#                 zpl_bytes
#             )

#         return {
#             "status": "ok",
#             "message": " | ".join(info),
#             "zpl_b64": base64.b64encode(
#                 zpl_bytes
#             ).decode(),
#         }

#     except Exception as e:

#         frappe.log_error(
#             frappe.get_traceback(),
#             "ZPL Upload and Print Error"
#         )

#         frappe.throw(
#             f"{type(e).__name__}: {e}"
#         )


# # ============================================================
# # PREVIEW LABEL
# # ============================================================

# @frappe.whitelist()
# def preview_label(
#     file_url,
#     source_dpi=0
# ):
#     """
#     Convert label file to PNG preview.
#     Returns base64 encoded PNG.
#     """

#     source_dpi = int(source_dpi or 0)

#     raw_bytes, filename = get_file_bytes(
#         file_url
#     )

#     info = []

#     try:

#         from PIL import Image

#         # ====================================================
#         # PNG
#         # ====================================================

#         if filename.endswith(".png"):

#             detected_dpi = (
#                 source_dpi
#                 if source_dpi > 0
#                 else None
#             )

#             img_orig = Image.open(
#                 io.BytesIO(raw_bytes)
#             )

#             if detected_dpi is None:

#                 detected_dpi = (
#                     200
#                     if img_orig.width <= 850
#                     else 300
#                 )

#             info.append(
#                 f"Original: "
#                 f"{img_orig.width}×{img_orig.height}px "
#                 f"@ {detected_dpi}dpi"
#             )

#             cropped = autocrop_png(
#                 raw_bytes
#             )

#             img = Image.open(
#                 io.BytesIO(cropped)
#             )

#             if img.size != img_orig.size:

#                 info.append(
#                     f"Cropped to: "
#                     f"{img.width}×{img.height}px"
#                 )

#             if detected_dpi != 300:

#                 scale = 300 / detected_dpi

#                 img = img.resize(
#                     (
#                         round(img.width * scale),
#                         round(img.height * scale)
#                     ),
#                     Image.LANCZOS
#                 )

#             pages = [img]

#         # ====================================================
#         # PDF
#         # ====================================================

#         elif filename.endswith(".pdf"):

#             info.append(
#                 "Rendering PDF at 300dpi"
#             )

#             page_pngs = pdf_to_png_bytes(
#                 raw_bytes,
#                 dpi=300
#             )

#             info.append(
#                 f"{len(page_pngs)} page(s)"
#             )

#             pages = []

#             for i, page_png in enumerate(page_pngs):

#                 cropped = autocrop_png(
#                     page_png
#                 )

#                 img = Image.open(
#                     io.BytesIO(cropped)
#                 )

#                 info.append(
#                     f"Page {i + 1}: "
#                     f"{img.width}×{img.height}px"
#                 )

#                 pages.append(img)

#         # ====================================================
#         # ZPL
#         # ====================================================

#         elif filename.endswith(".zpl"):

#             frappe.throw(
#                 "ZPL preview not supported — "
#                 "ZPL is a printer command language. "
#                 "Convert to PNG/PDF first, "
#                 "or print directly."
#             )

#         else:

#             frappe.throw(
#                 "Unsupported file type"
#             )

#         # ====================================================
#         # ENCODE PREVIEWS
#         # ====================================================

#         previews = []

#         for img in pages:

#             buf = io.BytesIO()

#             img.convert("RGB").save(
#                 buf,
#                 format="PNG"
#             )

#             previews.append(
#                 base64.b64encode(
#                     buf.getvalue()
#                 ).decode()
#             )

#         return {
#             "status": "ok",
#             "previews": previews,
#             "info": " | ".join(info),
#         }

#     except Exception as e:

#         frappe.log_error(
#             frappe.get_traceback(),
#             "ZPL Preview Error"
#         )

#         frappe.throw(
#             f"{type(e).__name__}: {e}"
#         )
