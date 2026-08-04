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
from frappe.utils.file_manager import save_file


EasyPostSettings = frappe.get_doc("Easy Post Settings")
api_key = None

mode = EasyPostSettings.mode

if mode == "test":
    api_key = EasyPostSettings.test_key
else:
    api_key = EasyPostSettings.production_key

BASE_URL = EasyPostSettings.base_url
VERSION = EasyPostSettings.version
TARGET_DPI = 300  




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
def buy_shipment(delivery_note):

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
                    "custom_estimated_delivery_date": estimated_delivery,
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
    doc=frappe.get_doc("Delivery Note", docname)
    # Get Shipment
    response = requests.get(
        f"{BASE_URL}/{VERSION}/shipments/{shipment_id}",
        auth=HTTPBasicAuth(api_key, "")
    )

    if response.status_code != 200:
        frappe.throw(response.text)

    shipment = response.json()

    png_url = shipment["postage_label"]["label_url"]

    # Download PNG
    img_response = requests.get(png_url)
    img_response.raise_for_status()

    # Convert PNG -> ZPL
    zpl_bytes = png_bytes_to_zpl(
        img_response.content,
        source_dpi=300
    )

    EasyPostSettings = frappe.get_single("Easy Post Settings")

    zpl_path = f"/tmp/{shipment_id}.zpl"

    with open(zpl_path, "wb") as f:
        f.write(zpl_bytes)

    # Attach to Delivery Note
    with open(zpl_path, "rb") as f:
        file_doc = save_file(
            f"{shipment_id}.zpl",
            f.read(),
            "Delivery Note",
            docname,
            is_private=0
        )
    doc.custom_zpl_file = file_doc.file_url
    
    frappe.db.set_value(
    "Delivery Note",
    docname,
    {
        "custom_zpl_file": file_doc.file_url,
        "custom_zpl_file_url": file_doc.file_url
    }
)

    frappe.db.commit()

    return {
        "success": True,
        "zpl_url": file_doc.file_url,
        "message": "ZPL file created and attached to Delivery Note."}

@frappe.whitelist()
def print_label(shipment_id, docname):
    print_status = "Not printed"
    response = requests.get(
        f"{BASE_URL}/{VERSION}/shipments/{shipment_id}",
        auth=HTTPBasicAuth(api_key, "")
    )

    if response.status_code != 200:
        frappe.throw(response.text)

    shipment = response.json()

    png_url = shipment["postage_label"]["label_url"]

    # Download PNG
    img_response = requests.get(png_url)
    img_response.raise_for_status()

    # Convert PNG -> ZPL
    zpl_bytes = png_bytes_to_zpl(
        img_response.content,
        source_dpi=300
    )

    EasyPostSettings = frappe.get_single("Easy Post Settings")

    if EasyPostSettings.host_ip and EasyPostSettings.port:
        try:
            print_status = print_zpl(
                host=EasyPostSettings.host_ip,
                port=int(EasyPostSettings.port or 6101),
                zpl_bytes=zpl_bytes,
                copies=1
            )
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Zebra Print Failed")
    else:
        print_status = "Printer not configured."

    
    return {
        "success": True,
        "print_status": print_status
    }

def png_bytes_to_zpl(png_bytes: bytes, source_dpi: int = None) -> bytes:
    """
    Convert EasyPost PNG label to ZPL (^GFB binary).

    USPS : 300 DPI (1200x1800)
    UPS  : 200 DPI (800x1400) -> scaled to 300 DPI
    """

    img = Image.open(io.BytesIO(png_bytes)).convert("L")

    # Auto-detect source DPI
    if source_dpi is None:
        source_dpi = 200 if img.width <= 850 else 300

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

    for y in range(h_px):
        for x in range(0, w_px, 8):
            byte = 0

            for bit in range(8):
                xx = x + bit

                if xx < w_px and pixels[xx, y] == 0:
                    byte |= (1 << (7 - bit))

            raw.append(byte)

    zpl = (
        f"^XA\n"
        f"^CI28\n"
        f"^PW{w_px}\n"
        f"^LL{h_px}\n"
        f"^FO0,0\n"
    ).encode("ascii")

    zpl += f"^GFB,{len(raw)},{total_bytes},{bytes_per_row},".encode("ascii")
    zpl += raw
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

import requests
from requests.auth import HTTPBasicAuth

import frappe
from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note


@frappe.whitelist()
def verify_address(address_name, doc_name, doctype):

    doc = frappe.get_doc(doctype, doc_name)
    address = frappe.get_doc("Address", address_name)

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

    so_name = dn.items[0].against_sales_order
    so = frappe.get_doc("Sales Order", so_name)

    steps = get_steps(so)

    if steps.get("packing_slip"):
        ps = frappe.get_doc("Packing Slip", steps["packing_slip"])

        return {
            "packing_slip": ps.name,
            "status": "exists",
            "from_case_no": ps.from_case_no,
            "to_case_no": ps.to_case_no,
            "gross_weight": ps.gross_weight_pkg,
            "net_weight": ps.net_weight_pkg,
            "gross_weight_uom": ps.gross_weight_uom,
        }

    ps = frappe.new_doc("Packing Slip")
    ps.delivery_note = delivery_note

    next_case_no = ps.get_recommended_case_no()

    ps.gross_weight_pkg = gross_weight or 0
    ps.net_weight_pkg = net_weight or 0
    ps.from_case_no = from_case_no or next_case_no
    ps.to_case_no = to_case_no or next_case_no

    total_net_weight = 0

    for item in dn.items:

        remaining_qty = item.qty - (item.packed_qty or 0)

        if remaining_qty <= 0:
            continue

        item_doc = frappe.get_cached_doc("Item", item.item_code)

        total_net_weight = 0

        for item in dn.items:

            remaining_qty = item.qty - (item.packed_qty or 0)

            if remaining_qty <= 0:
                continue

            item_doc = frappe.get_cached_doc("Item", item.item_code)

            item_net_weight = (item_doc.weight_per_unit or 0) * remaining_qty

            total_net_weight += item_net_weight

            ps.append("items", {
                "item_code": item.item_code,
                "item_name": item.item_name,
                "description": item.description,
                "qty": remaining_qty,
                "stock_uom": item.stock_uom,
                "dn_detail": item.name,
                "net_weight": item_net_weight
            })

        ps.net_weight_pkg = total_net_weight

    ps.insert(ignore_permissions=True)

    steps["packing_slip"] = ps.name
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

    frappe.db.commit()

    return ps.name


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