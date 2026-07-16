import frappe
import requests
from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
from frappe.utils import get_datetime, convert_utc_to_system_timezone


EasyPostSettings = frappe.get_doc("Easy Post Settings")
api_key = None

mode = EasyPostSettings.mode

if mode == "test":
    api_key = EasyPostSettings.test_key
else:
    api_key = EasyPostSettings.production_key

BASE_URL = EasyPostSettings.base_url
VERSION = EasyPostSettings.version

import frappe
import requests


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
    
# easypost_connector/api/api.py
import json
import hmac
import hashlib
import frappe


from frappe.utils import (
    get_datetime,
    convert_utc_to_system_timezone,
)


@frappe.whitelist(allow_guest=True)
def easypost_webhook():
    frappe.set_user("Administrator")

    raw_body = frappe.request.get_data()
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