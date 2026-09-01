diff --git a/easypost_connector/api/api.py b/easypost_connector/api/api.py
index 9bc7353..aef8ade 100644
--- a/easypost_connector/api/api.py
+++ b/easypost_connector/api/api.py
@@ -12,10 +12,33 @@ import frappe
 import io
 import math
 import socket
+import pymupdf as fitz
+from frappe import _
 from frappe.utils.file_manager import save_file
 
 
-EasyPostSettings = frappe.get_doc("Easy Post Settings")
+
+
+def get_easypost_settings():
+
+    settings = frappe.get_all(
+        "Easypost Settings",
+        filters={
+            "enabled": 1
+        },
+        fields=["name"],
+        limit=1
+    )
+
+    if not settings:
+        frappe.throw("No enabled Easypost Settings found.")
+
+    return frappe.get_doc(
+        "Easypost Settings",
+        settings[0].name
+    )
+
+EasyPostSettings = get_easypost_settings()
 api_key = None
 
 mode = EasyPostSettings.mode
@@ -27,7 +50,7 @@ else:
 
 BASE_URL = EasyPostSettings.base_url
 VERSION = EasyPostSettings.version
-TARGET_DPI = 300  
+TARGET_DPI = EasyPostSettings.dpi or 300
 
 
 
@@ -121,7 +144,7 @@ def create_easypost_shipment(doc=None, method=None, delivery_note=None):
 
 
 @frappe.whitelist()
-def buy_shipment(delivery_note):
+def buy_shipment(delivery_note, rate_id=None):
 
     if not delivery_note:
         return
@@ -286,8 +309,11 @@ def easypost_webhook():
 
 @frappe.whitelist()
 def convert_png_to_bw(shipment_id, docname):
-    doc=frappe.get_doc("Delivery Note", docname)
-    # Get Shipment
+
+    import base64
+
+    doc = frappe.get_doc("Delivery Note", docname)
+
     response = requests.get(
         f"{BASE_URL}/{VERSION}/shipments/{shipment_id}",
         auth=HTTPBasicAuth(api_key, "")
@@ -300,99 +326,178 @@ def convert_png_to_bw(shipment_id, docname):
 
     png_url = shipment["postage_label"]["label_url"]
 
-    # Download PNG
     img_response = requests.get(png_url)
     img_response.raise_for_status()
 
-    # Convert PNG -> ZPL
     zpl_bytes = png_bytes_to_zpl(
         img_response.content,
-        source_dpi=300
+        source_dpi=TARGET_DPI
     )
 
-    EasyPostSettings = frappe.get_single("Easy Post Settings")
+    # Safe storage for binary ZPL
+    zpl_base64 = base64.b64encode(
+        zpl_bytes
+    ).decode("ascii")
 
-    zpl_path = f"/tmp/{shipment_id}.zpl"
+    frappe.log_error(
+        "ZPL Content:\n" + zpl_bytes.decode("utf-8", errors="replace"),
+        "ZPL Content"
+    )
 
-    with open(zpl_path, "wb") as f:
-        f.write(zpl_bytes)
+    frappe.log_error(
+        "ZPL Base64 Length: " + str(len(zpl_base64)),
+        "ZPL Base64 Info"
+    )
+    # Save actual ZPL file
+    file_doc = save_file(
+        f"{shipment_id}.zpl",
+        zpl_bytes,
+        "Delivery Note",
+        docname,
+        is_private=0
+    )
 
-    # Attach to Delivery Note
-    with open(zpl_path, "rb") as f:
-        file_doc = save_file(
-            f"{shipment_id}.zpl",
-            f.read(),
-            "Delivery Note",
-            docname,
-            is_private=0
-        )
-    doc.custom_zpl_file = file_doc.file_url
-    
     frappe.db.set_value(
-    "Delivery Note",
-    docname,
-    {
-        "custom_zpl_file": file_doc.file_url,
-        "custom_zpl_file_url": file_doc.file_url
-    }
-)
+        "Delivery Note",
+        docname,
+        {
+            "custom_zpl_file": file_doc.file_url,
+            "custom_zpl_file_url": file_doc.file_url,
+            "custom_zpl_file_content": zpl_base64
+        }
+    )
 
     frappe.db.commit()
 
     return {
         "success": True,
         "zpl_url": file_doc.file_url,
-        "message": "ZPL file created and attached to Delivery Note."}
+        "zpl_base64": zpl_base64,
+        "message": "ZPL file created and attached successfully."
+    }
 
 @frappe.whitelist()
 def print_label(shipment_id, docname):
-    print_status = "Not printed"
-    response = requests.get(
-        f"{BASE_URL}/{VERSION}/shipments/{shipment_id}",
-        auth=HTTPBasicAuth(api_key, "")
-    )
 
-    if response.status_code != 200:
-        frappe.throw(response.text)
+    settings = get_easypost_settings()
 
-    shipment = response.json()
+    if not settings.host_ip or not settings.port:
+        return {
+            "success": False,
+            "print_status": "Printer not configured."
+        }
 
-    png_url = shipment["postage_label"]["label_url"]
+    host = settings.host_ip
+    port = int(settings.port or 6101)
 
-    # Download PNG
-    img_response = requests.get(png_url)
-    img_response.raise_for_status()
+    print_status = []
 
-    # Convert PNG -> ZPL
-    zpl_bytes = png_bytes_to_zpl(
-        img_response.content,
-        source_dpi=300
-    )
+    # =====================================================
+    # SHIPPING LABEL
+    # =====================================================
+
+    if settings.print_label:
+
+        response = requests.get(
+            f"{BASE_URL}/{VERSION}/shipments/{shipment_id}",
+            auth=HTTPBasicAuth(api_key, "")
+        )
+
+        if response.status_code != 200:
+            frappe.throw(response.text)
+
+        shipment = response.json()
+
+        png_url = shipment["postage_label"]["label_url"]
+
+        # Download Shipping Label PNG
+        img_response = requests.get(png_url)
+        img_response.raise_for_status()
+
+        # PNG -> ZPL
+        shipping_zpl = png_bytes_to_zpl(
+            img_response.content,
+            source_dpi=TARGET_DPI
+        )
 
-    EasyPostSettings = frappe.get_single("Easy Post Settings")
+        try:
+            # status = print_zpl(
+            #     host=host,
+            #     port=port,
+            #     zpl_bytes=shipping_zpl,
+            #     copies=1
+            # )
+            frappe.get_doc("Delivery Note", docname).custom_zpl_file_content  = shipping_zpl.decode("latin-1")
+            print_status.append(
+                f"Shipping Label: {status}"
+            )
+
+        except Exception:
+            frappe.log_error(
+                frappe.get_traceback(),
+                "Shipping Label Print Failed"
+            )
+
+            print_status.append(
+                "Shipping Label: Print failed."
+            )
+
+    # =====================================================
+    # PACKING SLIP
+    # =====================================================
+
+    if settings.print_packing_slip:
 
-    if EasyPostSettings.host_ip and EasyPostSettings.port:
         try:
-            print_status = print_zpl(
-                host=EasyPostSettings.host_ip,
-                port=int(EasyPostSettings.port or 6101),
-                zpl_bytes=zpl_bytes,
+
+            packing_slip_zpl = packing_slip_to_zpl(
+                docname
+            )
+
+            status = print_zpl(
+                host=host,
+                port=port,
+                zpl_bytes=packing_slip_zpl,
                 copies=1
             )
+
+            print_status.append(
+                f"Packing Slip: {status}"
+            )
+
         except Exception:
-            frappe.log_error(frappe.get_traceback(), "Zebra Print Failed")
-    else:
-        print_status = "Printer not configured."
 
-    
+            frappe.log_error(
+                frappe.get_traceback(),
+                "Packing Slip Print Failed"
+            )
+
+            print_status.append(
+                "Packing Slip: Print failed."
+            )
+
+    # =====================================================
+    # NOTHING SELECTED
+    # =====================================================
+
+    if not print_status:
+
+        return {
+            "success": False,
+            "print_status": (
+                "Please enable Print Label or "
+                "Print Packing Slip in Easy Post Settings."
+            )
+        }
+
     return {
         "success": True,
-        "print_status": print_status
+        "print_status": "\n".join(print_status)
     }
-
+    
 def png_bytes_to_zpl(png_bytes: bytes, source_dpi: int = None) -> bytes:
     """
-    Convert EasyPost PNG label to ZPL (^GFB binary).
+    Convert EasyPost PNG label to ZPL (^GFA ASCII-hex).
 
     USPS : 300 DPI (1200x1800)
     UPS  : 200 DPI (800x1400) -> scaled to 300 DPI
@@ -402,7 +507,7 @@ def png_bytes_to_zpl(png_bytes: bytes, source_dpi: int = None) -> bytes:
 
     # Auto-detect source DPI
     if source_dpi is None:
-        source_dpi = 200 if img.width <= 850 else 300
+        source_dpi = 200 if img.width <= 850 else TARGET_DPI
 
     # Scale to 300 DPI if required
     if source_dpi != TARGET_DPI:
@@ -437,6 +542,8 @@ def png_bytes_to_zpl(png_bytes: bytes, source_dpi: int = None) -> bytes:
                     byte |= (1 << (7 - bit))
 
             raw.append(byte)
+            
+    hex_data = raw.hex().upper().encode("ascii")
 
     zpl = (
         f"^XA\n"
@@ -446,14 +553,14 @@ def png_bytes_to_zpl(png_bytes: bytes, source_dpi: int = None) -> bytes:
         f"^FO0,0\n"
     ).encode("ascii")
 
-    zpl += f"^GFB,{len(raw)},{total_bytes},{bytes_per_row},".encode("ascii")
-    zpl += raw
+    # ---- CHANGED: ^GFB -> ^GFA, and field length is now len(hex_data)
+    #      (the hex TEXT length), not len(raw) (the binary byte count) ----
+    zpl += f"^GFA,{len(hex_data)},{total_bytes},{bytes_per_row},".encode("ascii")
+    zpl += hex_data
     zpl += b"\n^FS\n^XZ\n"
 
     return bytes(zpl)
 
-
-
 def print_zpl(host: str, port: int, zpl_bytes: bytes,
               copies: int = 1, timeout: int = 10) -> str:
     """
@@ -491,11 +598,95 @@ def print_zpl(host: str, port: int, zpl_bytes: bytes,
     return (f"Sent {copies} label(s) ({len(zpl_bytes):,} bytes) to {host}:{port}" +
             (f" — printer: {repr(response)}" if response else ""))
 
-import requests
-from requests.auth import HTTPBasicAuth
+def packing_slip_to_zpl(docname):
+    """
+    Render Packing Slip using the Print Format configured
+    in Easy Post Settings, convert the PDF to PNG, then PNG to ZPL.
+    """
 
-import frappe
-from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
+    settings = get_easypost_settings()
+
+    print_format = settings.print_format_for_packing_slip
+
+    if not print_format:
+        frappe.throw(
+            _("Print Format for Packing Slip is not configured.")
+        )
+
+    # Find Packing Slip linked to Delivery Note
+    packing_slip_name = frappe.db.get_value(
+        "Packing Slip",
+        {"delivery_note": docname},
+        "name"
+    )
+
+    if not packing_slip_name:
+        frappe.throw(
+            _("Packing Slip not found for Delivery Note {0}.")
+            .format(docname)
+        )
+
+    # -----------------------------------------
+    # 1. Generate Packing Slip PDF
+    # -----------------------------------------
+    pdf_bytes = frappe.get_print(
+        "Packing Slip",
+        packing_slip_name,
+        print_format=print_format,
+        as_pdf=True,
+        no_letterhead=1
+    )
+
+    if not pdf_bytes:
+        frappe.throw(
+            _("Unable to generate Packing Slip PDF.")
+        )
+
+    # -----------------------------------------
+    # 2. PDF -> PNG -> ZPL
+    # -----------------------------------------
+    pdf = fitz.open(
+        stream=pdf_bytes,
+        filetype="pdf"
+    )
+
+    zpl_parts = []
+
+    # Render every page
+    for page in pdf:
+
+        # 300 DPI
+        zoom = TARGET_DPI / 72
+
+        matrix = fitz.Matrix(
+            zoom,
+            zoom
+        )
+
+        pix = page.get_pixmap(
+            matrix=matrix,
+            alpha=False
+        )
+
+        png_bytes = pix.tobytes("png")
+
+        # PNG -> ZPL
+        zpl_bytes = png_bytes_to_zpl(
+            png_bytes,
+            source_dpi=TARGET_DPI
+        )
+
+        zpl_parts.append(zpl_bytes)
+
+    pdf.close()
+
+    if not zpl_parts:
+        frappe.throw(
+            _("No pages found in Packing Slip.")
+        )
+
+    # Combine pages
+    return b"\n".join(zpl_parts)
 
 
 @frappe.whitelist()
@@ -503,6 +694,9 @@ def verify_address(address_name, doc_name, doctype):
 
     doc = frappe.get_doc(doctype, doc_name)
     address = frappe.get_doc("Address", address_name)
+    
+    if not address:
+        frappe.throw(" Shipping Address not found.")
 
     payload = {
         "address": {
@@ -641,13 +835,60 @@ def create_packing_slip(
 ):
     dn = frappe.get_doc("Delivery Note", delivery_note)
 
+    if not dn.items:
+        frappe.throw("Delivery Note has no items.")
+
     so_name = dn.items[0].against_sales_order
     so = frappe.get_doc("Sales Order", so_name)
 
     steps = get_steps(so)
 
-    if steps.get("packing_slip"):
-        ps = frappe.get_doc("Packing Slip", steps["packing_slip"])
+    # --------------------------------------------------
+    # 1. Check Packing Slip stored in steps
+    # --------------------------------------------------
+    packing_slip_name = steps.get("packing_slip")
+
+    if packing_slip_name and frappe.db.exists(
+        "Packing Slip",
+        packing_slip_name
+    ):
+        ps = frappe.get_doc(
+            "Packing Slip",
+            packing_slip_name
+        )
+
+        return {
+            "packing_slip": ps.name,
+            "status": "exists",
+            "from_case_no": ps.from_case_no,
+            "to_case_no": ps.to_case_no,
+            "gross_weight": ps.gross_weight_pkg,
+            "net_weight": ps.net_weight_pkg,
+            "gross_weight_uom": ps.gross_weight_uom,
+        }
+
+    # --------------------------------------------------
+    # 2. Check if Packing Slip already exists
+    #    for this Delivery Note
+    # --------------------------------------------------
+    existing_packing_slip = frappe.db.get_value(
+        "Packing Slip",
+        {
+            "delivery_note": delivery_note
+        },
+        "name"
+    )
+
+    if existing_packing_slip:
+
+        ps = frappe.get_doc(
+            "Packing Slip",
+            existing_packing_slip
+        )
+
+        # Update stale/missing step reference
+        steps["packing_slip"] = ps.name
+        save_steps(so, steps)
 
         return {
             "packing_slip": ps.name,
@@ -659,49 +900,73 @@ def create_packing_slip(
             "gross_weight_uom": ps.gross_weight_uom,
         }
 
+    # --------------------------------------------------
+    # 3. No Packing Slip exists → create a new one
+    # --------------------------------------------------
     ps = frappe.new_doc("Packing Slip")
+
     ps.delivery_note = delivery_note
 
     next_case_no = ps.get_recommended_case_no()
 
     ps.gross_weight_pkg = gross_weight or 0
     ps.net_weight_pkg = net_weight or 0
-    ps.from_case_no = from_case_no or next_case_no
-    ps.to_case_no = to_case_no or next_case_no
+
+    ps.from_case_no = (
+        from_case_no or next_case_no
+    )
+
+    ps.to_case_no = (
+        to_case_no or next_case_no
+    )
 
     total_net_weight = 0
 
-    # Only ONE loop
     for item in dn.items:
 
-        remaining_qty = item.qty - (item.packed_qty or 0)
+        remaining_qty = item.qty - (
+            item.packed_qty or 0
+        )
 
         if remaining_qty <= 0:
             continue
 
-        item_doc = frappe.get_cached_doc("Item", item.item_code)
+        item_doc = frappe.get_cached_doc(
+            "Item",
+            item.item_code
+        )
 
         item_net_weight = (
-            (item_doc.weight_per_unit or 0) * remaining_qty
+            (item_doc.weight_per_unit or 0)
+            * remaining_qty
         )
 
         total_net_weight += item_net_weight
 
-        ps.append("items", {
-            "item_code": item.item_code,
-            "item_name": item.item_name,
-            "description": item.description,
-            "qty": remaining_qty,
-            "stock_uom": item.stock_uom,
-            "dn_detail": item.name,
-            "net_weight": item_net_weight
-        })
+        ps.append(
+            "items",
+            {
+                "item_code": item.item_code,
+                "item_name": item.item_name,
+                "description": item.description,
+                "qty": remaining_qty,
+                "stock_uom": item.stock_uom,
+                "dn_detail": item.name,
+                "net_weight": item_net_weight
+            }
+        )
 
     ps.net_weight_pkg = total_net_weight
 
     ps.insert(ignore_permissions=True)
 
+    # Save newly created Packing Slip reference
     steps["packing_slip"] = ps.name
+
+    # Reset statuses because this is a new Packing Slip
+    steps["packing_slip_submitted"] = False
+    steps["completed"] = False
+
     save_steps(so, steps)
 
     return {
@@ -713,6 +978,7 @@ def create_packing_slip(
         "gross_weight_uom": ps.gross_weight_uom,
     }
     
+
 @frappe.whitelist()
 def complete_packing_slip(
     packing_slip,
@@ -748,10 +1014,56 @@ def complete_packing_slip(
     steps["packing_slip_submitted"] = True
     steps["completed"] = True
     save_steps(so, steps)
+    
+    settings = get_easypost_settings()
+
+    print_status = []
+
+    # Skip printing if printer is not configured
+    if not settings.host_ip:
+        print_status.append("Printer not configured.")
+
+    else:
+        host = settings.host_ip
+        port = int(settings.port or 6101)
+
+        try:
+            packing_slip_zpl = packing_slip_to_zpl(ps.name)
+
+            if packing_slip_zpl:
+                status = print_zpl(
+                    host=host,
+                    port=port,
+                    zpl_bytes=packing_slip_zpl,
+                    copies=1
+                )
+
+                print_status.append(
+                    f"Packing Slip: {status}"
+                )
+
+            else:
+                print_status.append(
+                    "Packing Slip ZPL could not be generated. Printing skipped."
+                )
+
+        except Exception as e:
+            frappe.log_error(
+                frappe.get_traceback(),
+                "Packing Slip Printing Error"
+            )
+
+            print_status.append(
+                f"Packing Slip printing failed: {str(e)}"
+            )
 
     frappe.db.commit()
 
-    return ps.name
+    return {
+        "success": True,
+        "packing_slip": ps.name,
+        "print_status": print_status
+    }
 
 
 def get_steps(doc):
@@ -781,4 +1093,912 @@ def save_steps(doc, steps):
         "custom_executed_steps",
         json.dumps(steps),
         update_modified=False
-    )
\ No newline at end of file
+    )
+    
+    
+@frappe.whitelist()
+def test_printer_connection(host="127.0.0.1", port=6101):
+    try:
+        with socket.create_connection(
+            (host, int(port)),
+            timeout=3
+        ):
+            return {
+                "connected": True,
+                "message": f"Printer is reachable at {host}:{port}"
+            }
+
+    except (socket.timeout, ConnectionRefusedError, OSError) as e:
+        return {
+            "connected": False,
+            "message": f"Printer is not reachable at {host}:{port}",
+            "error": str(e)
+        }
+        
+        
+import base64
+import math
+import io
+import socket
+import re
+
+import frappe
+
+
+def send_zpl(host: str, port: int, zpl_bytes: bytes):
+    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
+    s.settimeout(10)
+    s.connect((host, port))
+    s.sendall(zpl_bytes)
+
+    try:
+        resp = s.recv(1024)
+    except Exception:
+        resp = b""
+
+    s.close()
+
+    return resp
+
+
+def png_bytes_to_zpl(png_bytes: bytes, source_dpi: int = None) -> bytes:
+    from PIL import Image
+
+    TARGET_DPI = 300
+
+    img = Image.open(io.BytesIO(png_bytes))
+
+    if source_dpi is None:
+        source_dpi = 200 if img.width <= 850 else 300
+
+    if source_dpi != TARGET_DPI:
+        scale = TARGET_DPI / source_dpi
+
+        img = img.resize(
+            (
+                round(img.width * scale),
+                round(img.height * scale)
+            ),
+            Image.LANCZOS
+        )
+
+    img = img.convert("1")
+
+    w_px, h_px = img.size
+
+    bytes_per_row = math.ceil(w_px / 8)
+    total_bytes = bytes_per_row * h_px
+
+    raw = bytearray()
+    pixels = img.load()
+
+    for y in range(h_px):
+        for x in range(0, w_px, 8):
+
+            byte = 0
+
+            for bit in range(8):
+                if x + bit < w_px and pixels[x + bit, y] == 0:
+                    byte |= (1 << (7 - bit))
+
+            raw.append(byte)
+
+    zpl = f"^XA\n^PW{w_px}\n^LL{h_px}\n^FO0,0\n".encode("ascii")
+
+    zpl += (
+        f"^GFB,{len(raw)},{total_bytes},{bytes_per_row},"
+    ).encode("ascii")
+
+    zpl += bytes(raw)
+
+    zpl += b"\n^FS\n^XZ\n"
+    
+    return zpl
+
+
+def autocrop_png(png_bytes: bytes, padding: int = 10) -> bytes:
+    """
+    Auto-crop a PNG to the bounding box of non-white content.
+    Adds a small padding around the content to avoid clipping.
+    Returns the cropped PNG as bytes.
+    """
+
+    from PIL import Image, ImageOps
+
+    img = Image.open(io.BytesIO(png_bytes)).convert("L")
+
+    # Invert so content is white on black for getbbox
+    inverted = ImageOps.invert(img)
+
+    bbox = inverted.getbbox()
+
+    if bbox is None:
+        return png_bytes
+
+    # Add padding, clamped to image bounds
+    w, h = img.size
+
+    left = max(0, bbox[0] - padding)
+    upper = max(0, bbox[1] - padding)
+
+    right = min(w, bbox[2] + padding)
+    lower = min(h, bbox[3] + padding)
+
+    cropped = img.crop((left, upper, right, lower))
+
+    buf = io.BytesIO()
+
+    cropped.save(buf, format="PNG")
+
+    return buf.getvalue()
+
+
+def pdf_to_png_bytes(pdf_bytes: bytes, dpi: int = 300) -> list[bytes]:
+    """
+    Convert PDF pages to PNG bytes at the given DPI.
+    """
+
+    try:
+        import fitz
+
+        doc = fitz.open(
+            stream=pdf_bytes,
+            filetype="pdf"
+        )
+
+        result = []
+
+        for page in doc:
+
+            mat = fitz.Matrix(
+                dpi / 72,
+                dpi / 72
+            )
+
+            pix = page.get_pixmap(
+                matrix=mat,
+                colorspace=fitz.csGRAY
+            )
+
+            result.append(
+                pix.tobytes("png")
+            )
+
+        return result
+
+    except ImportError:
+
+        frappe.throw(
+            "PyMuPDF not installed. Run: pip install pymupdf"
+        )
+
+
+def detect_zpl_dpi(zpl_bytes: bytes) -> int:
+    """
+    Estimate ZPL DPI from ^PW (print width) command.
+    """
+
+    try:
+
+        text = zpl_bytes.decode(
+            "latin-1",
+            errors="ignore"
+        )
+
+        m = re.search(
+            r'\^PW(\d+)',
+            text,
+            re.IGNORECASE
+        )
+
+        if m:
+
+            pw = int(m.group(1))
+
+            # 4" label:
+            # 300dpi = 1200
+            # 200dpi = 800
+            # 203dpi = 812
+
+            if pw <= 850:
+                return 200
+
+        return 300
+
+    except Exception:
+
+        return 300
+
+
+def rescale_zpl(
+    zpl_bytes: bytes,
+    source_dpi: int = 200,
+    target_dpi: int = 300
+) -> bytes:
+    """
+    Rescale a ZPL file from source_dpi to target_dpi.
+    Extracts ^GFB binary image data, scales it,
+    and rebuilds the ZPL.
+    Falls back to original ZPL if ^GFB extraction fails.
+    """
+
+    from PIL import Image
+
+    text = zpl_bytes.decode(
+        "latin-1",
+        errors="ignore"
+    )
+
+    # Try to find ^GFB field and extract binary data
+    m = re.search(
+        r'\^GFB,(\d+),(\d+),(\d+),',
+        text
+    )
+
+    if m:
+
+        data_bytes = int(m.group(1))
+
+        # Find position of binary data
+        comma_pos = zpl_bytes.find(
+            b',',
+            zpl_bytes.find(b'^GFB')
+        )
+
+        comma_pos = zpl_bytes.find(
+            b',',
+            comma_pos + 1
+        )
+
+        comma_pos = zpl_bytes.find(
+            b',',
+            comma_pos + 1
+        )
+
+        comma_pos = zpl_bytes.find(
+            b',',
+            comma_pos + 1
+        )
+
+        raw_data = zpl_bytes[
+            comma_pos + 1:
+            comma_pos + 1 + data_bytes
+        ]
+
+        bytes_per_row = int(m.group(3))
+
+        w_px = bytes_per_row * 8
+
+        h_px = data_bytes // bytes_per_row
+
+        # Reconstruct image from raw bytes
+        img_raw = bytearray(data_bytes)
+
+        for i, byte in enumerate(raw_data):
+
+            # Invert for PIL
+            img_raw[i] = byte ^ 0xFF
+
+        img = Image.frombytes(
+            "1",
+            (w_px, h_px),
+            bytes(img_raw),
+            decoder_name="raw"
+        )
+
+        # Scale
+        scale = target_dpi / source_dpi
+
+        new_w = round(w_px * scale)
+        new_h = round(h_px * scale)
+
+        img = img.resize(
+            (new_w, new_h),
+            Image.LANCZOS
+        ).convert("1")
+
+        # Rebuild ZPL
+        new_bpr = math.ceil(new_w / 8)
+
+        new_total = new_bpr * new_h
+
+        new_raw = bytearray()
+
+        pixels = img.load()
+
+        for y in range(new_h):
+
+            for x in range(0, new_w, 8):
+
+                byte = 0
+
+                for bit in range(8):
+
+                    if (
+                        x + bit < new_w
+                        and pixels[x + bit, y] == 0
+                    ):
+                        byte |= (1 << (7 - bit))
+
+                new_raw.append(byte)
+
+        # Replace dimensions and data
+        result = re.sub(
+            r'\^PW\d+',
+            f'^PW{new_w}',
+            text
+        )
+
+        result = re.sub(
+            r'\^LL\d+',
+            f'^LL{new_h}',
+            result
+        )
+
+        prefix = result[
+            :result.find('^GFB')
+        ].encode("latin-1")
+
+        suffix = result[
+            result.find(
+                '^FS',
+                result.find('^GFB')
+            ):
+        ].encode("latin-1")
+
+        rebuilt = prefix
+
+        rebuilt += (
+            f"^GFB,{len(new_raw)},"
+            f"{new_total},"
+            f"{new_bpr},"
+        ).encode("ascii")
+
+        rebuilt += bytes(new_raw)
+
+        rebuilt += suffix
+
+        return rebuilt
+
+    # Fallback
+    return zpl_bytes
+
+
+# ============================================================
+# HELPER: GET FILE CONTENT FROM FRAPPE FILE
+# ============================================================
+
+def get_file_bytes(file_url):
+
+    file_doc = frappe.get_doc(
+        "File",
+        {
+            "file_url": file_url
+        }
+    )
+
+    raw_bytes = file_doc.get_content()
+
+    filename = file_doc.file_name.lower()
+
+    return raw_bytes, filename
+
+
+# ============================================================
+# UPLOAD AND PRINT
+# ============================================================
+
+@frappe.whitelist()
+def upload_and_print(
+    file_url,
+    source_dpi=0,
+    copies=1,
+    host=None,
+    port=None
+):
+    """
+    Accept a ZPL, PNG, or PDF label file,
+    convert to printable ZPL,
+    and send to printer.
+    """
+    doc = get_easypost_settings()
+
+    source_dpi = int(source_dpi or 0)
+    copies = int(copies or 1)
+
+    host = host or doc.host_ip
+    port = port or doc.port
+
+    raw_bytes, filename = get_file_bytes(file_url)
+
+    info = []
+
+    try:
+
+        # ====================================================
+        # ZPL
+        # ====================================================
+
+        if filename.endswith(".zpl"):
+
+            detected_dpi = (
+                source_dpi
+                if source_dpi > 0
+                else detect_zpl_dpi(raw_bytes)
+            )
+
+            info.append(
+                f"ZPL detected DPI: {detected_dpi}"
+            )
+
+            if detected_dpi < 300:
+
+                info.append(
+                    f"Scaling from "
+                    f"{detected_dpi}dpi → 300dpi"
+                )
+
+                zpl_bytes = rescale_zpl(
+                    raw_bytes,
+                    source_dpi=detected_dpi,
+                    target_dpi=300
+                )
+
+            else:
+
+                zpl_bytes = raw_bytes
+
+        # ====================================================
+        # PNG
+        # ====================================================
+
+        elif filename.endswith(".png"):
+
+            from PIL import Image
+
+            detected_dpi = (
+                source_dpi
+                if source_dpi > 0
+                else None
+            )
+
+            img = Image.open(
+                io.BytesIO(raw_bytes)
+            )
+
+            if detected_dpi is None:
+
+                detected_dpi = (
+                    200
+                    if img.width <= 850
+                    else 300
+                )
+
+            info.append(
+                f"PNG size: "
+                f"{img.width}×{img.height}px, "
+                f"detected DPI: {detected_dpi}"
+            )
+
+            cropped = autocrop_png(
+                raw_bytes
+            )
+
+            img2 = Image.open(
+                io.BytesIO(cropped)
+            )
+
+            if img2.size != img.size:
+
+                info.append(
+                    f"Cropped to: "
+                    f"{img2.width}×{img2.height}px"
+                )
+
+            zpl_bytes = png_bytes_to_zpl(
+                cropped,
+                source_dpi=detected_dpi
+            )
+
+        # ====================================================
+        # PDF
+        # ====================================================
+
+        elif filename.endswith(".pdf"):
+
+            info.append(
+                "Converting PDF to PNG at 300dpi"
+            )
+
+            pages = pdf_to_png_bytes(
+                raw_bytes,
+                dpi=300
+            )
+
+            info.append(
+                f"PDF has {len(pages)} page(s)"
+            )
+
+            zpl_bytes = b""
+
+            for i, page_png in enumerate(pages):
+
+                cropped = autocrop_png(
+                    page_png
+                )
+
+                zpl_bytes += png_bytes_to_zpl(
+                    cropped,
+                    source_dpi=300
+                )
+
+                from PIL import Image
+
+                img = Image.open(
+                    io.BytesIO(cropped)
+                )
+
+                info.append(
+                    f"Page {i + 1} "
+                    f"cropped to "
+                    f"{img.width}×{img.height}px "
+                    f"and converted"
+                )
+
+        else:
+
+            frappe.throw(
+                "Unsupported file type. "
+                "Upload .zpl, .png, or .pdf"
+            )
+
+        # ====================================================
+        # PRINT
+        # ====================================================
+
+        info.append(
+            f"ZPL size: "
+            f"{len(zpl_bytes):,} bytes"
+        )
+
+        info.append(
+            f"Sending {copies} "
+            f"cop{'y' if copies == 1 else 'ies'} "
+            f"to {host}:{port}"
+        )
+
+        for _ in range(copies):
+
+            send_zpl(
+                host,
+                int(port),
+                zpl_bytes
+            )
+
+        return {
+            "status": "ok",
+            "message": " | ".join(info),
+            "zpl_b64": base64.b64encode(
+                zpl_bytes
+            ).decode(),
+        }
+
+    except Exception as e:
+
+        frappe.log_error(
+            frappe.get_traceback(),
+            "ZPL Upload and Print Error"
+        )
+
+        frappe.throw(
+            f"{type(e).__name__}: {e}"
+        )
+
+
+# ============================================================
+# PREVIEW LABEL
+# ============================================================
+
+@frappe.whitelist()
+def preview_label(
+    file_url,
+    source_dpi=0
+):
+    """
+    Convert label file to PNG preview.
+    Returns base64 encoded PNG.
+    """
+
+    source_dpi = int(source_dpi or 0)
+
+    raw_bytes, filename = get_file_bytes(
+        file_url
+    )
+
+    info = []
+
+    try:
+
+        from PIL import Image
+
+        # ====================================================
+        # PNG
+        # ====================================================
+
+        if filename.endswith(".png"):
+
+            detected_dpi = (
+                source_dpi
+                if source_dpi > 0
+                else None
+            )
+
+            img_orig = Image.open(
+                io.BytesIO(raw_bytes)
+            )
+
+            if detected_dpi is None:
+
+                detected_dpi = (
+                    200
+                    if img_orig.width <= 850
+                    else 300
+                )
+
+            info.append(
+                f"Original: "
+                f"{img_orig.width}×{img_orig.height}px "
+                f"@ {detected_dpi}dpi"
+            )
+
+            cropped = autocrop_png(
+                raw_bytes
+            )
+
+            img = Image.open(
+                io.BytesIO(cropped)
+            )
+
+            if img.size != img_orig.size:
+
+                info.append(
+                    f"Cropped to: "
+                    f"{img.width}×{img.height}px"
+                )
+
+            if detected_dpi != 300:
+
+                scale = 300 / detected_dpi
+
+                img = img.resize(
+                    (
+                        round(img.width * scale),
+                        round(img.height * scale)
+                    ),
+                    Image.LANCZOS
+                )
+
+            pages = [img]
+
+        # ====================================================
+        # PDF
+        # ====================================================
+
+        elif filename.endswith(".pdf"):
+
+            info.append(
+                "Rendering PDF at 300dpi"
+            )
+
+            page_pngs = pdf_to_png_bytes(
+                raw_bytes,
+                dpi=300
+            )
+
+            info.append(
+                f"{len(page_pngs)} page(s)"
+            )
+
+            pages = []
+
+            for i, page_png in enumerate(page_pngs):
+
+                cropped = autocrop_png(
+                    page_png
+                )
+
+                img = Image.open(
+                    io.BytesIO(cropped)
+                )
+
+                info.append(
+                    f"Page {i + 1}: "
+                    f"{img.width}×{img.height}px"
+                )
+
+                pages.append(img)
+
+        # ====================================================
+        # ZPL
+        # ====================================================
+
+        elif filename.endswith(".zpl"):
+
+            frappe.throw(
+                "ZPL preview not supported — "
+                "ZPL is a printer command language. "
+                "Convert to PNG/PDF first, "
+                "or print directly."
+            )
+
+        else:
+
+            frappe.throw(
+                "Unsupported file type"
+            )
+
+        # ====================================================
+        # ENCODE PREVIEWS
+        # ====================================================
+
+        previews = []
+
+        for img in pages:
+
+            buf = io.BytesIO()
+
+            img.convert("RGB").save(
+                buf,
+                format="PNG"
+            )
+
+            previews.append(
+                base64.b64encode(
+                    buf.getvalue()
+                ).decode()
+            )
+
+        return {
+            "status": "ok",
+            "previews": previews,
+            "info": " | ".join(info),
+        }
+
+    except Exception as e:
+
+        frappe.log_error(
+            frappe.get_traceback(),
+            "ZPL Preview Error"
+        )
+
+        frappe.throw(
+            f"{type(e).__name__}: {e}"
+        )
+
+
+# ============================================================
+# CONVERT ONLY
+# ============================================================
+
+@frappe.whitelist()
+def convert_only(
+    file_url,
+    source_dpi=0
+):
+    """
+    Convert label file to ZPL
+    without printing.
+    Returns base64 encoded ZPL.
+    """
+
+    source_dpi = int(source_dpi or 0)
+
+    raw_bytes, filename = get_file_bytes(
+        file_url
+    )
+
+    try:
+
+        # ====================================================
+        # ZPL
+        # ====================================================
+
+        if filename.endswith(".zpl"):
+
+            detected_dpi = (
+                source_dpi
+                if source_dpi > 0
+                else detect_zpl_dpi(raw_bytes)
+            )
+
+            zpl_bytes = (
+                rescale_zpl(
+                    raw_bytes,
+                    detected_dpi,
+                    300
+                )
+                if detected_dpi < 300
+                else raw_bytes
+            )
+
+        # ====================================================
+        # PNG
+        # ====================================================
+
+        elif filename.endswith(".png"):
+
+            detected_dpi = (
+                source_dpi
+                if source_dpi > 0
+                else None
+            )
+
+            cropped = autocrop_png(
+                raw_bytes
+            )
+
+            zpl_bytes = png_bytes_to_zpl(
+                cropped,
+                source_dpi=detected_dpi
+            )
+
+        # ====================================================
+        # PDF
+        # ====================================================
+
+        elif filename.endswith(".pdf"):
+
+            pages = pdf_to_png_bytes(
+                raw_bytes,
+                dpi=300
+            )
+
+            zpl_bytes = b"".join(
+                png_bytes_to_zpl(
+                    autocrop_png(page),
+                    source_dpi=300
+                )
+                for page in pages
+            )
+
+        else:
+
+            frappe.throw(
+                "Unsupported file type"
+            )
+
+        return {
+            "status": "ok",
+
+            "zpl_b64": base64.b64encode(
+                zpl_bytes
+            ).decode(),
+
+            "size": len(zpl_bytes),
+        }
+
+    except Exception as e:
+
+        frappe.log_error(
+            frappe.get_traceback(),
+            "ZPL Convert Error"
+        )
+
+        frappe.throw(
+            f"{type(e).__name__}: {e}"
+        )
+        
+        
+def send_encoded_label_data(zpl_content):
+    """
+    Convert Latin-1 encoded ZPL string back to raw bytes
+    and send it directly to a network thermal printer.
+    """
+
+    if not zpl_content:
+        frappe.throw("ZPL label content is empty.")
+
+    # Convert stored string back to original bytes
+    zpl_bytes = zpl_content.encode("latin-1")
+    
+    return  zpl_bytes
\ No newline at end of file
