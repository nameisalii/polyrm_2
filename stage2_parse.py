#!/usr/bin/env python3
"""
Stage 2: Document Parsing & Line Item Extraction
=================================================
Parses each PDF based on its classified type and extracts:
  - Document header metadata (date, number, counterparty, etc.)
  - Line items (description, quantity, unit price, amount, HSN, etc.)

Each document type has its own parser tailored to its layout.
All parsers preserve original extracted text alongside parsed fields
for traceability.
"""

import os
import re
import pdfplumber
from datetime import datetime


# ── Date parsing ──────────────────────────────────────────────────────────

DATE_PATTERNS = [
    (r'(\d{2}-[A-Za-z]{3}-\d{4})', "%d-%b-%Y"),     # 04-Jan-2026
    (r'(\d{2}-[A-Za-z]{3}-\d{2})\b', "%d-%b-%y"),    # 04-Jan-26
    (r'(\d{2}-\d{2}-\d{4})', "%d-%m-%Y"),             # 04-01-2026
    (r'(\d{2}/\d{2}/\d{4})', "%d/%m/%Y"),             # 04/01/2026
    (r'(\d{2}-[A-Z]{3}-\d{4})', "%d-%b-%Y"),          # 04-JAN-2026
    (r'(\d{2}-[A-Z]{3}-\d{2})\b', "%d-%b-%y"),        # 10-OCT-2025
]


def parse_date(text: str) -> str:
    """Try to parse a date string; return ISO format or original."""
    if not text:
        return ""
    text = text.strip()
    for pattern, fmt in DATE_PATTERNS:
        m = re.search(pattern, text)
        if m:
            try:
                dt = datetime.strptime(m.group(1), fmt)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue
    return text


def safe_float(val) -> float:
    """Convert string to float, handling commas and whitespace."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    val = str(val).strip().replace(",", "").replace(" ", "")
    val = val.rstrip(".")
    if not val or val == "-":
        return 0.0
    try:
        return float(val)
    except ValueError:
        return 0.0


# ── Invoice Parser ────────────────────────────────────────────────────────

# Known HSN codes in this dataset
HSN_CODES = [
    "54025100", "54024600", "54024700", "54025210", "54023300",
    "54026900", "54011010", "54021910", "56012100",
]


def parse_invoice(pdf_path: str) -> dict:
    """Parse a Valson Tax Invoice PDF using word-position-based extraction."""
    with pdfplumber.open(pdf_path) as pdf:
        full_text = ""
        all_words = []
        for page in pdf.pages:
            full_text += (page.extract_text() or "") + "\n"
            page_words = page.extract_words()
            all_words.extend(page_words)

    doc = {
        "source_file": os.path.basename(pdf_path),
        "doc_type": "TAX_INVOICE",
        "raw_text_length": len(full_text),
    }

    # Invoice number
    m = re.search(r'Invoice\s*No\.?\s*:?\s*([A-Z0-9]+)', full_text)
    doc["doc_number"] = m.group(1) if m else ""

    # Invoice date
    m = re.search(r'Invoice\s*Date\s*:?\s*(.+?)(?:\n|Place)', full_text)
    doc["doc_date"] = parse_date(m.group(1)) if m else ""

    # Recipient — extract using word positions
    # The name appears on the line just below "Invoice No. : XXXX"
    lines = full_text.split("\n")
    counterparty = ""
    for i, line in enumerate(lines):
        if re.search(r'Invoice\s*No\.\s*:', line):
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                parts = re.split(r'\s{3,}', next_line)
                if parts:
                    name = parts[0].strip()
                    name = re.sub(r'\s*Invoice\s*Date.*', '', name)
                    if name and len(name) > 2:
                        counterparty = name
                        break
    
    # Clean up counterparty: remove duplicated name
    if counterparty:
        words = counterparty.split()
        half = len(words) // 2
        if half > 0 and words[:half] == words[half:2*half]:
            counterparty = " ".join(words[:half])
        counterparty = re.sub(r'\s*Invoice\s*Date\s*:.*', '', counterparty)
    
    doc["counterparty"] = counterparty

    # Buyer's Order No 
    m = re.search(r"Buyer's\s*Order\s*No\.?\s*:?\s*(PO\s*\S+)", full_text)
    doc["buyer_order_ref"] = m.group(1).strip() if m else ""

    # Order date
    m = re.search(r'Order\s*Date\s*:?\s*(\d{2}-\w{3}-\d{4})', full_text)
    doc["order_date"] = parse_date(m.group(1)) if m else ""

    # Broker
    m = re.search(r'Broker\s*:\s*(.+?)(?:\s{2,}|Tax)', full_text)
    doc["broker"] = m.group(1).strip() if m else ""

    # Due date
    m = re.search(r'Due\s*Date\s*:\s*(.+?)(?:\s*\(|$)', full_text)
    doc["due_date"] = parse_date(m.group(1)) if m else ""

    # Extract line items using word positions
    doc["line_items"] = _extract_invoice_line_items_positional(all_words)

    # Total amounts
    m = re.search(r'TOTAL\s*AMOUNT\s*PAYABLE\s+([\d,.]+)', full_text)
    doc["total_payable"] = safe_float(m.group(1)) if m else 0.0

    # Discounts
    m = re.search(r'DISCOUNT\s+on\s+Rs\.\S+\s+(\S+)\s+(\S+)', full_text)
    if m:
        doc["discount_pct"] = safe_float(m.group(1))
        doc["discount_amt"] = safe_float(m.group(2))
    
    return doc


def _extract_invoice_line_items_positional(all_words: list) -> list:
    """Extract invoice line items using word positions.
    
    Column layout (consistent across all invoices):
      x = 20-39:   SR #
      x = 42-89:   Challan No
      x = 92-207:  Description of Goods/Services  
      x = 209-298: HSN/SAC + Lot No (merged/garbled column)
      x = 312-390: Shade/Design
      x = 390-410: CTN (cartons)
      x = 414-470: Quantity + UOM  
      x = 485-512: Rate
      x = 534-570: Amount
    
    By using word x-coordinates, we avoid the text-merging problem entirely.
    """
    # Column x-boundaries (mid-points between columns)
    COL_BOUNDARIES = {
        'sr':          (0, 40),
        'challan':     (40, 90),
        'description': (90, 208),
        'hsn_lot':     (208, 310),
        'shade':       (310, 392),
        'ctn':         (392, 413),
        'qty_uom':     (413, 480),
        'rate':        (480, 535),
        'amount':      (535, 600),
    }
    
    if not all_words:
        return []
    
    # Group words by line (approximate y-coordinate)
    word_lines = {}
    for w in all_words:
        y_key = round(w['top'] / 8) * 8  # Group within ~8px
        if y_key not in word_lines:
            word_lines[y_key] = []
        word_lines[y_key].append(w)
    
    items = []
    in_items = False
    
    for y_key in sorted(word_lines.keys()):
        line_words = sorted(word_lines[y_key], key=lambda w: w['x0'])
        line_text = " ".join(w['text'] for w in line_words)
        
        # Detect header row
        if 'Rate' in line_text and 'Amount' in line_text and ('Challan' in line_text or 'SR' in line_text):
            in_items = True
            continue
        
        if not in_items:
            continue
        
        # Detect end of items (TOTAL line or dashes)
        if 'TOTAL' in line_text or '--------' in line_text:
            in_items = False
            continue
        
        # Assign words to columns based on x-position
        columns = {col: [] for col in COL_BOUNDARIES}
        for w in line_words:
            x_center = (w['x0'] + w['x1']) / 2
            for col_name, (x_min, x_max) in COL_BOUNDARIES.items():
                if x_min <= x_center < x_max:
                    columns[col_name].append(w['text'])
                    break
        
        # Get column values
        sr_text = " ".join(columns['sr']).strip()
        challan_text = " ".join(columns['challan']).strip()
        desc_text = " ".join(columns['description']).strip()
        hsn_lot_text = " ".join(columns['hsn_lot']).strip()
        shade_text = " ".join(columns['shade']).strip()
        ctn_text = " ".join(columns['ctn']).strip()
        qty_uom_text = " ".join(columns['qty_uom']).strip()
        rate_text = " ".join(columns['rate']).strip()
        amount_text = " ".join(columns['amount']).strip()
        
        # Validate: must have SR# (a number) and a challan number
        if not re.match(r'\d+$', sr_text):
            continue
        if not re.match(r'(DC[BN]|SA)\d+', challan_text):
            continue
        
        # Parse description: clean up any remaining artifacts
        description = desc_text
        # Fix partial YARN endings that got truncated
        description = re.sub(r'\bYAR$', 'YARN', description)
        description = re.sub(r'\bYA$', 'YARN', description) 
        description = re.sub(r'\bDYE5D\b', 'DYED', description)
        description = re.sub(r'\bY54A0R2N', 'YARN', description)
        description = re.sub(r'\bYA5R4N\b', 'YARN', description)
        description = re.sub(r'\bYAR5N\b', 'YARN', description)
        description = re.sub(r'\bY$', 'YARN', description)
        
        # Remove leaking text from total/tax lines on multi-page invoices
        description = re.sub(r'\s*IGST\s+ON\s+RS\.[\d,.]+\s*', ' ', description)
        description = re.sub(r'\s*UGST\s+ON\s+RS\.[\d,.]+\s*', ' ', description)
        description = re.sub(r'\s*CGST\s+ON\s+RS\.[\d,.]+\s*', ' ', description)
        description = re.sub(r'\s*ROUNDED\s+OFF\s*', ' ', description)
        description = re.sub(r'\s*DISCOUNT\s+ON\s+RS\.[\d,.]+\s*', ' ', description)
        description = re.sub(r'\s*SP\.DISCOUNT\s+ON\s+RS\.[\d,.]+\s*', ' ', description)
        description = re.sub(r'\s*TOTAL\s+INVOICE\s+VALUE\s*', ' ', description)
        description = re.sub(r'\s*TWT\.DYED\d+\s*$', ' TWT DYED YARN', description)
        description = re.sub(r'\s*POLY\.HB\s+TWT\.DYED\d+\s*$', ' POLY HB TWT DYED YARN', description)
        description = re.sub(r'\s+', ' ', description).strip()
        
        # Parse HSN code from the hsn_lot column
        hsn_code = ""
        lot_number = ""
        
        # The hsn_lot column often contains garbled text like "YA5R4N024700VP154156"
        # or clean text like "54025210VP154003"
        # Extract 8-digit HSN code
        for hsn_prefix in ['5402', '5401', '5601', '5403', '5404']:
            for hsn_candidate in HSN_CODES:
                if hsn_candidate in hsn_lot_text:
                    hsn_code = hsn_candidate
                    break
            if hsn_code:
                break
        
        if not hsn_code:
            # Try to find 8-digit HSN in the garbled text
            # Pattern: digits interleaved with letters from "YARN"
            # e.g., "YA5R4N024700VP154156" contains "54024700"
            # Try all known HSN codes
            for hsn_candidate in HSN_CODES:
                # Check if all digits of HSN appear in order in the text
                text_digits = ''.join(c for c in hsn_lot_text if c.isdigit())
                if hsn_candidate in text_digits:
                    hsn_code = hsn_candidate
                    break
        
        if not hsn_code:
            # Last resort: find any 8 consecutive digits starting with 54
            digits_only = ''.join(c for c in hsn_lot_text if c.isdigit())
            hsn_match = re.search(r'(54\d{6})', digits_only)
            if hsn_match:
                hsn_code = hsn_match.group(1)
            else:
                hsn_match = re.search(r'(56\d{6})', digits_only)
                if hsn_match:
                    hsn_code = hsn_match.group(1)
        
        # Extract lot number from hsn_lot (typically VP/NL followed by digits)
        lot_match = re.search(r'(VP\d{5,}|NL\d{5,})', hsn_lot_text)
        if lot_match:
            lot_number = lot_match.group(1)
        
        # Parse shade: clean brackets and garbled text
        shade = shade_text
        # Remove brackets
        shade = shade.replace('[', '').replace(']', '').strip()
        # Clean up garbled shade text (numbers mixed with color names)
        # e.g., "RGSSY4 [SHARDA]" → "SHARDA", "DIRVNY7239 [BLACK]" → "BLACK"
        shade_bracket = re.search(r'\[([^\]]+)\]', shade_text)
        if shade_bracket:
            shade = shade_bracket.group(1).strip()
        else:
            # If shade has lot-like prefix, remove it
            shade_parts = shade.split()
            if shade_parts and re.match(r'[A-Z]{2,}\d+', shade_parts[0]):
                shade = " ".join(shade_parts[1:]) if len(shade_parts) > 1 else ""
        
        # Parse quantity and UOM
        quantity = 0.0
        uom = "KGS"
        
        qty_nums = re.findall(r'([\d,]+\.?\d*)', qty_uom_text)
        if qty_nums:
            quantity = safe_float(qty_nums[0])
        if 'MTR' in qty_uom_text:
            uom = "MTR"
        elif 'PCS' in qty_uom_text:
            uom = "PCS"
        
        # Parse rate
        rate = safe_float(rate_text)
        
        # Parse amount
        amount = safe_float(amount_text)
        
        # If quantity is 0 but we have rate and amount, calculate it
        if quantity == 0 and rate > 0 and amount > 0:
            quantity = round(amount / rate, 3)
        
        items.append({
            "sr_no": sr_text,
            "challan_no": challan_text,
            "item_description_original": description.strip(),
            "hsn_code": hsn_code,
            "lot_number": lot_number,
            "shade": shade,
            "quantity": quantity,
            "uom": uom,
            "unit_price": rate,
            "amount": amount,
            "raw_line": line_text,
        })
    
    return items


# ── Delivery Instruction Parser ───────────────────────────────────────────

def parse_delivery_instruction(pdf_path: str) -> dict:
    """Parse a Delivery Instruction PDF."""
    with pdfplumber.open(pdf_path) as pdf:
        full_text = ""
        for page in pdf.pages:
            full_text += (page.extract_text() or "") + "\n"

    doc = {
        "source_file": os.path.basename(pdf_path),
        "doc_type": "DELIVERY_INSTRUCTION",
        "raw_text_length": len(full_text),
    }

    # DI number
    m = re.search(r'DI#\s*:?\s*(\S+)', full_text)
    doc["doc_number"] = m.group(1) if m else ""

    # DI date
    m = re.search(r'DI\s*Date\s*:?\s*(\S+)', full_text)
    doc["doc_date"] = parse_date(m.group(1)) if m else ""

    # Recipient (M/S. ...)
    m = re.search(r'M/S\.?\s*(.+?)(?:\s{2,}|DI#)', full_text)
    doc["counterparty"] = m.group(1).strip() if m else ""

    # Location
    m = re.search(r'Location\s*:?\s*(.+?)(?:\s{2,}|\n)', full_text)
    doc["location"] = m.group(1).strip() if m else ""

    # Agent
    m = re.search(r'Agent\s*:?\s*(.+?)(?:\s{2,}|\n)', full_text)
    doc["agent"] = m.group(1).strip() if m else ""

    # Type (DTA/LOCAL, etc.)
    m = re.search(r'Type\s*:\s*(.+?)(?:\s{2,}|\n)', full_text)
    doc["transaction_type"] = m.group(1).strip() if m else ""

    # Discounts
    m = re.search(r'Sp\.\s*Disc\.?\s*\n?\s*([\d.]+)', full_text)
    doc["special_discount_pct"] = safe_float(m.group(1)) if m else 0.0

    # Extract line items
    doc["line_items"] = _extract_di_line_items(full_text)

    return doc


def _extract_di_line_items(text: str) -> list:
    """Extract line items from Delivery Instruction text."""
    items = []
    lines = text.split("\n")
    in_items = False
    
    for i, line in enumerate(lines):
        if re.search(r'ITEM\s+NAME.*Qty.*RATE.*Amount', line, re.IGNORECASE):
            in_items = True
            continue
        
        if not in_items:
            continue
        
        if 'ITEM TOTAL' in line.upper() or 'RUPEES:' in line.upper():
            in_items = False
            continue
        
        # Skip continuation lines (PO refs, etc.)
        if re.match(r'^\s*\.\d+', line) or re.match(r'^\s*PO\s', line):
            continue
        if not line.strip():
            continue
        
        # Match: description followed by qty, rate, amount
        m = re.match(
            r'\s*(.+?)\s+'
            r'(\d[\d,]*\.?\d*)\s+'
            r'(\d[\d,]*\.?\d*)\s+'
            r'(\d[\d,]*\.?\d*)',
            line
        )
        
        if m:
            desc = m.group(1).strip()
            qty = safe_float(m.group(2))
            rate = safe_float(m.group(3))
            amount = safe_float(m.group(4))
            
            if desc.upper() in ("ITEM NAME", "TOTAL", ""):
                continue
            
            # Get party PO reference
            rest = line[m.end():].strip()
            po_ref = rest.split()[0] if rest.split() else ""
            
            items.append({
                "item_description_original": desc,
                "quantity": qty,
                "uom": "KGS",
                "unit_price": rate,
                "amount": amount,
                "party_po_ref": po_ref,
                "raw_line": line.strip(),
            })
    
    return items


# ── Purchase Order Parser ─────────────────────────────────────────────────

def parse_purchase_order(pdf_path: str) -> dict:
    """Parse a Purchase Order PDF."""
    with pdfplumber.open(pdf_path) as pdf:
        full_text = ""
        for page in pdf.pages:
            full_text += (page.extract_text() or "") + "\n"

    doc = {
        "source_file": os.path.basename(pdf_path),
        "doc_type": "PURCHASE_ORDER",
        "raw_text_length": len(full_text),
    }

    # PO number
    m = re.search(r'PO#\s*:?\s*(\S+)', full_text)
    doc["doc_number"] = m.group(1) if m else ""

    # PO date
    m = re.search(r'PO\s*Date\s*:?\s*(\S+)', full_text)
    doc["doc_date"] = parse_date(m.group(1)) if m else ""

    # Supplier (M/S. ...)
    m = re.search(r'M/S\.?\s*(.+?)(?:\s{2,}|\n)', full_text)
    doc["counterparty"] = m.group(1).strip() if m else ""

    # Payment terms
    m = re.search(r'Payment\s*:?\s*(.+?)(?:\n)', full_text)
    doc["payment_terms"] = m.group(1).strip() if m else ""

    # Order by
    m = re.search(r'Order\s*by\s*:?\s*(.+?)(?:\n)', full_text)
    doc["ordered_by"] = m.group(1).strip() if m else ""

    # Extract line items
    doc["line_items"] = _extract_po_line_items(full_text)

    # Remarks
    m = re.search(r'Remarks\s*:\s*(.+?)(?:\n)', full_text)
    doc["remarks"] = m.group(1).strip() if m else ""

    return doc


def _extract_po_line_items(text: str) -> list:
    """Extract line items from Purchase Order text.
    
    Format:
    # - ITEM NAME  HSN  IndentNo  DelDate  Quantity  UOM Rate  AMT  Disc  NetAmt
    1 - 110/36 SD POY  54024600  POY/00359  20-01-26  12000.000 KGS 95.75  1149000.00 ...
    """
    items = []
    lines = text.split("\n")
    in_items = False
    
    for i, line in enumerate(lines):
        if re.search(r'ITEM\s+NAME.*HSN.*Quantity.*Rate', line, re.IGNORECASE):
            in_items = True
            continue
        
        if not in_items:
            continue
        
        if 'Sub Total' in line or 'RUPEES:' in line or re.match(r'\s*IGST\b', line) or re.match(r'\s*CGST\b', line) or 'Total...' in line:
            in_items = False
            continue
        
        # Pattern: starts with "# -" 
        m = re.match(r'\s*(\d+)\s*-\s*(.+)', line)
        if not m:
            continue
        
        sr_no = m.group(1)
        rest = m.group(2)
        
        # Find HSN code (8 digits starting with 54)
        hsn_match = re.search(r'(54\d{6}|56\d{6})', rest)
        if hsn_match:
            item_name = rest[:hsn_match.start()].strip()
            after_hsn = rest[hsn_match.end():].strip()
            hsn_code = hsn_match.group(1)
            
            # After HSN: IndentNo DelDate Quantity UOM Rate Amount Disc NetAmt
            # Find all numbers
            numbers = re.findall(r'([\d,]+\.?\d*)', after_hsn)
            
            # UOM
            uom = "KGS"
            if "KGS" in after_hsn:
                uom = "KGS"
            
            # Try to find quantity (large decimal), rate, amount
            qty = 0.0
            rate = 0.0
            amount = 0.0
            
            # Find "KGS" position
            kgs_match = re.search(r'KGS', after_hsn)
            if kgs_match:
                before_kgs = after_hsn[:kgs_match.start()]
                after_kgs = after_hsn[kgs_match.end():].strip()
                
                before_nums = re.findall(r'([\d,]+\.?\d+)', before_kgs)
                after_nums = re.findall(r'([\d,]+\.?\d+)', after_kgs)
                
                if before_nums:
                    qty = safe_float(before_nums[-1])
                if len(after_nums) >= 2:
                    rate = safe_float(after_nums[0])
                    amount = safe_float(after_nums[1])
                elif len(after_nums) == 1:
                    rate = safe_float(after_nums[0])
                    amount = qty * rate
            else:
                # No KGS found, use positional
                if len(numbers) >= 3:
                    qty = safe_float(numbers[-3])
                    rate = safe_float(numbers[-2])
                    amount = safe_float(numbers[-1])
            
            # Indent number
            indent_match = re.search(r'(POY/\d+/\d+-\d+|DPY/\d+/\d+-\d+)', after_hsn)
            indent_no = indent_match.group(1) if indent_match else ""
            
            items.append({
                "sr_no": sr_no,
                "item_description_original": item_name,
                "hsn_code": hsn_code,
                "indent_no": indent_no,
                "quantity": qty,
                "uom": uom,
                "unit_price": rate,
                "amount": amount,
                "raw_line": line.strip(),
            })
    
    return items


# ── GRN Parser ────────────────────────────────────────────────────────────

def parse_grn(pdf_path: str) -> dict:
    """Parse a Goods Receipt Note PDF."""
    with pdfplumber.open(pdf_path) as pdf:
        full_text = ""
        for page in pdf.pages:
            full_text += (page.extract_text() or "") + "\n"

    doc = {
        "source_file": os.path.basename(pdf_path),
        "doc_type": "GOODS_RECEIPT_NOTE",
        "raw_text_length": len(full_text),
    }

    # GRN number
    m = re.search(r'GRN\s*#\s*:?\s*(\S+)', full_text)
    doc["doc_number"] = m.group(1) if m else ""

    # GRN date
    m = re.search(r'GRN\s*Date\s*:?\s*(\S+)', full_text)
    doc["doc_date"] = parse_date(m.group(1)) if m else ""

    # Supplier (M/S. ...)
    m = re.search(r'M/S\.?\s*(.+?)(?:\s{2,}|GRN)', full_text)
    doc["counterparty"] = m.group(1).strip() if m else ""

    # Invoice reference
    m = re.search(r'Invoice\s*#\s*:?\s*(\S+)', full_text)
    doc["supplier_invoice_no"] = m.group(1) if m else ""

    m = re.search(r'Inv\.\s*Date\s*:?\s*(\S+)', full_text)
    doc["supplier_invoice_date"] = parse_date(m.group(1)) if m else ""

    # PO reference (from table data, not header)
    m = re.search(r'PO#\s*(\S+)\s*dt\.', full_text)
    doc["po_reference"] = m.group(1) if m else ""

    # Transport
    m = re.search(r'Transport\s+(.+?)(?:\s+LR#)', full_text)
    doc["transporter"] = m.group(1).strip() if m else ""

    # Extract line items
    doc["line_items"] = _extract_grn_line_items(full_text)

    return doc


def _extract_grn_line_items(text: str) -> list:
    """Extract line items from GRN.
    
    Format:
    # Product Shade Grade HSN Merge# Box/Plt Cops Quantity UOM Rate Amount % ...
    1 126/34 POY IST 54024600 1022990 6 216 4033.400 KGS 96.50 389223.10 5.00 ...
    """
    items = []
    lines = text.split("\n")
    in_items = False
    
    for i, line in enumerate(lines):
        if re.search(r'#\s+Product.*Shade.*Grade.*HSN.*Rate.*Amount', line, re.IGNORECASE):
            in_items = True
            continue
        
        if not in_items:
            continue
        
        if 'PO#' in line or 'Pallet Code' in line:
            continue
        if 'Sub Total' in line or 'Grand Total' in line or 'RUPEES:' in line or 'Total...' in line:
            in_items = False
            continue
        
        # Match: starts with number, then product name
        m = re.match(r'\s*(\d+)\s+(.+)', line)
        if not m:
            continue
        
        sr = m.group(1)
        rest = m.group(2)
        
        # Skip BU CHARGES lines
        if 'CHARGES' in rest.upper():
            continue
        
        # Find HSN code
        hsn_match = re.search(r'(54\d{6}|56\d{6})', rest)
        if not hsn_match:
            continue
        
        product_info = rest[:hsn_match.start()].strip()
        hsn = hsn_match.group(1)
        after_hsn = rest[hsn_match.end():].strip()
        
        # Product info: "126/34 POY IST" or "162/34 SD POY IST"
        # Split into product name and grade
        prod_parts = product_info.split()
        grade = ""
        product_name = product_info
        if prod_parts and prod_parts[-1] in ("IST", "IND", "2ND"):
            grade = prod_parts[-1]
            product_name = " ".join(prod_parts[:-1])
        
        # Find KGS and extract qty, rate, amount
        kgs_match = re.search(r'KGS', after_hsn)
        qty = 0.0
        rate = 0.0
        amount = 0.0
        
        if kgs_match:
            before_kgs = after_hsn[:kgs_match.start()]
            after_kgs = after_hsn[kgs_match.end():].strip()
            
            before_nums = re.findall(r'([\d,]+\.?\d+)', before_kgs)
            after_nums = re.findall(r'([\d,]+\.?\d+)', after_kgs)
            
            # Qty is the last number before KGS
            if before_nums:
                qty = safe_float(before_nums[-1])
            
            # Rate and amount are first two numbers after KGS
            if len(after_nums) >= 2:
                rate = safe_float(after_nums[0])
                amount = safe_float(after_nums[1])
        
        items.append({
            "sr_no": sr,
            "item_description_original": product_name,
            "grade": grade,
            "hsn_code": hsn,
            "quantity": qty,
            "uom": "KGS",
            "unit_price": rate,
            "amount": amount,
            "raw_line": line.strip(),
        })
    
    return items


# ── Purchase Requisition Parser ───────────────────────────────────────────

def parse_purchase_requisition(pdf_path: str) -> dict:
    """Parse a Purchase Requisition PDF."""
    with pdfplumber.open(pdf_path) as pdf:
        full_text = ""
        for page in pdf.pages:
            full_text += (page.extract_text() or "") + "\n"

    doc = {
        "source_file": os.path.basename(pdf_path),
        "doc_type": "PURCHASE_REQUISITION",
        "raw_text_length": len(full_text),
    }

    m = re.search(r'Voucher\s*#\s*:?\s*(\S+)', full_text)
    doc["doc_number"] = m.group(1) if m else ""

    m = re.search(r'Voucher\s*Date\s*:?\s*(\S+)', full_text)
    doc["doc_date"] = parse_date(m.group(1)) if m else ""

    doc["counterparty"] = "INTERNAL REQUISITION"
    doc["line_items"] = _extract_pr_line_items(full_text)

    return doc


def _extract_pr_line_items(text: str) -> list:
    """Extract line items from Purchase Requisition.
    
    Format: 
    ITEM NAME  QUANTITY UOM RATE VENDOR DEL DATE Stock Min.Stock Last Month Cons.
    250/34 POY 16000.000 KGS 88.55 RELIANCE INDUSTRIES LTD 25-Nov-2025 ...
    
    Note: There's a LAST PURCHASE PRICE line after each item that starts with a number.
    """
    items = []
    lines = text.split("\n")
    in_items = False
    
    for i, line in enumerate(lines):
        if re.search(r'ITEM\s+NAME.*QUANTITY.*UOM.*RATE.*VENDOR', line, re.IGNORECASE):
            in_items = True
            continue
        if re.search(r'LAST\s+PURCHASE\s+PRICE', line, re.IGNORECASE):
            continue
        
        if not in_items:
            continue
        
        # Skip "last purchase price" lines (just a number + description)
        if re.match(r'^\s*[\d.]+\s+RATE\s+', line, re.IGNORECASE):
            continue
        
        if 'Sub Total' in line or 'RUPEES:' in line or 'Total...' in line or re.match(r'\s*Remarks\s*:', line):
            in_items = False
            continue
        
        # Match: "ItemName Qty UOM Rate VendorName Date ..."
        m = re.match(
            r'\s*(.+?)\s+'
            r'(\d[\d,]*\.?\d*)\s+'
            r'(KGS|MTR|PCS|NOS)\s+'
            r'(\d[\d,]*\.?\d*)\s+'
            r'(.+?)\s+'
            r'(\d{2}-\w{3}-\d{4})',
            line
        )
        
        if m:
            items.append({
                "item_description_original": m.group(1).strip(),
                "quantity": safe_float(m.group(2)),
                "uom": m.group(3),
                "unit_price": safe_float(m.group(4)),
                "vendor": m.group(5).strip(),
                "delivery_date": parse_date(m.group(6)),
                "raw_line": line.strip(),
            })
    
    return items


# ── Packing List Parser ──────────────────────────────────────────────────

def parse_packing_list(pdf_path: str) -> dict:
    """Parse a Packing List PDF."""
    with pdfplumber.open(pdf_path) as pdf:
        full_text = ""
        for page in pdf.pages:
            full_text += (page.extract_text() or "") + "\n"

    doc = {
        "source_file": os.path.basename(pdf_path),
        "doc_type": "PACKING_LIST",
        "raw_text_length": len(full_text),
    }

    m = re.search(r'(\d{2}-\w{3}-\d{4})\s+TO\s+(\d{2}-\w{3}-\d{4})', full_text)
    if m:
        doc["doc_date"] = parse_date(m.group(1))
        doc["date_range_end"] = parse_date(m.group(2))
    else:
        doc["doc_date"] = ""

    doc["doc_number"] = ""
    doc["counterparty"] = "VALSON (INTERNAL)"

    m = re.search(r'(TWISTING|DYEING|TEXTURISING)', full_text)
    doc["department"] = m.group(1) if m else ""
    doc["line_items"] = []  # Packing lists are internal production records, not useful for price history

    return doc


# ── Generic/Unknown Parser ────────────────────────────────────────────────

def parse_unknown(pdf_path: str) -> dict:
    """Parse an unclassified document."""
    with pdfplumber.open(pdf_path) as pdf:
        full_text = ""
        for page in pdf.pages:
            full_text += (page.extract_text() or "") + "\n"

    return {
        "source_file": os.path.basename(pdf_path),
        "doc_type": "UNKNOWN",
        "raw_text_length": len(full_text),
        "doc_number": "",
        "doc_date": "",
        "counterparty": "",
        "line_items": [],
    }


# ── Dispatch ──────────────────────────────────────────────────────────────

PARSERS = {
    "TAX_INVOICE": parse_invoice,
    "DELIVERY_INSTRUCTION": parse_delivery_instruction,
    "PURCHASE_ORDER": parse_purchase_order,
    "GOODS_RECEIPT_NOTE": parse_grn,
    "PURCHASE_REQUISITION": parse_purchase_requisition,
    "PACKING_LIST": parse_packing_list,
    "DELIVERY_CHALLAN": parse_grn,
    "UNKNOWN": parse_unknown,
}


def parse_all(pdf_files: list, classified: list) -> list:
    """Parse all documents using the appropriate parser for each type."""
    class_map = {c["source_file"]: c for c in classified}
    
    results = []
    errors = []
    total_items = 0
    items_by_type = {}
    
    for pdf_path in pdf_files:
        filename = os.path.basename(pdf_path)
        classification = class_map.get(filename, {"doc_type": "UNKNOWN"})
        doc_type = classification["doc_type"]
        
        parser = PARSERS.get(doc_type, parse_unknown)
        
        try:
            doc = parser(pdf_path)
            n_items = len(doc.get("line_items", []))
            total_items += n_items
            items_by_type[doc_type] = items_by_type.get(doc_type, 0) + n_items
            results.append(doc)
        except Exception as e:
            errors.append({"file": filename, "error": str(e)})
            results.append({
                "source_file": filename,
                "doc_type": doc_type,
                "error": str(e),
                "line_items": [],
            })
    
    print(f"   Parsed {len(results)} documents, extracted {total_items} total line items")
    for dt in sorted(items_by_type):
        print(f"     {dt:30s} {items_by_type[dt]:4d} items")
    if errors:
        print(f"   ⚠ {len(errors)} parsing errors:")
        for e in errors[:5]:
            print(f"     - {e['file']}: {e['error']}")
    
    return results


if __name__ == "__main__":
    import sys, json
    from stage1_classify import classify_all
    
    docs_dir = sys.argv[1] if len(sys.argv) > 1 else "./documents"
    pdf_files = sorted([
        os.path.join(docs_dir, f) for f in os.listdir(docs_dir)
        if f.lower().endswith(".pdf")
    ])
    classified = classify_all(pdf_files)
    parsed = parse_all(pdf_files, classified)
    
    # Print a few parsed examples
    for doc in parsed[:5]:
        if doc.get("line_items"):
            print(f"\n{doc['source_file']} ({doc['doc_type']}):")
            print(f"  Date: {doc.get('doc_date')} | #{doc.get('doc_number')} | To: {doc.get('counterparty')}")
            for item in doc["line_items"][:3]:
                print(f"  - {item['item_description_original']} | Qty: {item['quantity']} | Rate: {item['unit_price']} | Amt: {item['amount']}")
