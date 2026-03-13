#!/usr/bin/env python3
"""
Stage 3: Line Item Normalization
=================================
Flattens all parsed documents into a single line-item table and normalizes:
  - Counterparty / vendor names
  - Item descriptions (yarn type, denier/filament, twist, etc.)
  - Units of measure
  - Part numbers / HSN codes

Preserves original extracted values alongside normalized ones for traceability.
"""

import re
from collections import defaultdict


# ── Vendor Name Normalization ─────────────────────────────────────────────

# Build canonical vendor list dynamically + some known overrides
VENDOR_OVERRIDES = {
    "RELIANCE INDUSTRIES LTD": "RELIANCE INDUSTRIES LTD",
    "RELIANCE INDUSTRIES": "RELIANCE INDUSTRIES LTD",
    "INTERNAL REQUISITION": "INTERNAL REQUISITION",
    "VALSON (INTERNAL)": "VALSON (INTERNAL)",
}


def normalize_vendor(name: str) -> str:
    """Normalize a vendor/counterparty name."""
    if not name:
        return ""
    
    name = name.strip()
    name = re.sub(r'\s+', ' ', name)
    name = name.upper()
    
    # Remove invoice date if accidentally captured
    name = re.sub(r'\s*INVOICE\s*DATE\s*:.*', '', name)
    
    # Remove duplicated names ("DEEP TEX DEEP TEX" → "DEEP TEX")
    words = name.split()
    if len(words) >= 4:
        half = len(words) // 2
        if words[:half] == words[half:2*half]:
            name = " ".join(words[:half])
    
    # Check known overrides
    if name in VENDOR_OVERRIDES:
        return VENDOR_OVERRIDES[name]
    
    for key, canonical in VENDOR_OVERRIDES.items():
        if key in name:
            return canonical
    
    return name


# ── Item Description Normalization ────────────────────────────────────────

def normalize_item_description(desc: str) -> str:
    """Normalize a yarn item description.
    
    Goals:
    1. Extract the core yarn specification (denier/filament/twist)
    2. Normalize yarn type abbreviations
    3. Identify material (polyester vs nylon)
    4. Identify processing state (grey/dyed/raw)
    5. Remove lot-specific details (lot numbers, color codes)
    """
    if not desc:
        return ""
    
    desc = desc.strip().upper()
    desc = re.sub(r'\s+', ' ', desc)
    
    # Extract denier/filament spec
    spec_match = re.match(r'(\d+(?:/\d+){1,3})', desc)
    if not spec_match:
        return desc  # Can't normalize without spec
    
    spec = spec_match.group(1)
    rest = desc[spec_match.end():].strip()
    
    # Remove leading/trailing punctuation and artifacts
    rest = re.sub(r'^[\s\-\.]+', '', rest)
    
    # Generic cleanup for leaking tax/total lines and common artifacts
    artifacts = [
        r'IGST\s+ON\s+RS\.[\d,.]+',
        r'CGST\s+ON\s+RS\.[\d,.]+',
        r'UGST\s+ON\s+RS\.[\d,.]+',
        r'ROUNDED\s+OFF',
        r'ROUNDED\s+OFF',
        r'DISCOUNT\s+ON\s+RS\.[\d,.]+',
        r'SA0\d+',
        r'DCN0\d+',
        r'DCB0\d+',
        r'TOTAL\s+INVOICE\s+VALUE',
        r'TOTAL\s+AMOUNT\s+PAYABLE',
        r'RUPEES\s*:',
    ]
    for art in artifacts:
        rest = re.sub(art, ' ', rest, flags=re.IGNORECASE)
    
    # Clean up double spaces again and any remaining single artifacts
    rest = rest.replace('ROUNDED OFF', '')
    rest = re.sub(r'IGST.*$', '', rest, flags=re.IGNORECASE)
    rest = re.sub(r'ROUNDED.*$', '', rest, flags=re.IGNORECASE)
    rest = re.sub(r'TOTAL.*$', '', rest, flags=re.IGNORECASE)
    
    rest = re.sub(r'\s+', ' ', rest).strip()
    material = ""
    if "NYLON" in rest:
        material = "NYLON"
        rest = rest.replace("NYLON", "").strip()
    
    # Identify full product type
    product_type = ""
    type_patterns = [
        (r'MICRO\s*DYE?D\s*YARN?', "MICRO DYED YARN"),
        (r'SUPER\s+CATLON\s+DYED\s+YARN?', "SUPER CATLON DYED YARN"),
        (r'SUPER\s+CATLON', "SUPER CATLON"),
        (r'CATEX\s+DYED\s+YARN?', "CATEX DYED YARN"),
        (r'CATEX', "CATEX"),
        (r'HB[\s\-]?TWT\.?\s*DYED\s+YARN?', "HB-TWT DYED YARN"),
        (r'POLY\.?\s*HB\s+TWT\.?\s*DYED\s+YARN?', "POLY HB TWT DYED YARN"),
        (r'MH[\s\-]LIM\s+HARD\s+DYED\s+YARN?', "MH-LIM HARD DYED YARN"),
        (r'ROYAL\s+WARP\s+DYED\s+YARN?', "ROYAL WARP DYED YARN"),
        (r'T[\s\-]?TWT\s+DYED\s+YARN?', "T-TWT DYED YARN"),
        (r'T[\s\-]?TWT\s+GREY\s+YARN?\s*(LIGHT)?', "T-TWT GREY YARN"),
        (r'TWT\s+DYED\s+YARN?', "TWT DYED YARN"),
        (r'TWT\s+GREY\s+YARN?', "TWT GREY YARN"),
        (r'T[\s\-]?\s*DYED\s+YARN?', "T-DYED YARN"),
        (r'Z\s+TWT\s+DYED\s+YARN?', "Z TWT DYED YARN"),
        (r'Z\s+TWT\s+NYLON\s+DYED\s+YARN?', "Z TWT DYED YARN"),
        (r'DYED\s+YARN?', "DYED YARN"),
        (r'GREY\s+YARN?\s*(LIGHT)?', "GREY YARN"),
        (r'SD\s+POY', "SD POY"),
        (r'FD\s+POY', "FD POY"),
        (r'BRT\s+POY', "BRT POY"),
        (r'POY\s+SD', "SD POY"),
        (r'POY\s+BRT', "BRT POY"),
        (r'POY', "POY"),
        (r'FDY', "FDY"),
        (r'DTY', "DTY"),
    ]
    
    for pattern, normalized_type in type_patterns:
        if re.search(pattern, rest):
            product_type = normalized_type
            rest = re.sub(pattern, '', rest).strip()
            break
    
    if not product_type:
        product_type = rest.strip()
    
    # Build normalized name
    parts = [spec]
    if material:
        parts.append(material)
    if product_type:
        parts.append(product_type)
    
    return " ".join(parts)


def extract_part_number(desc: str, hsn: str = "") -> str:
    """Generate a structured part number from the description.
    
    Format: SPEC-MATERIAL-TYPE
    e.g., "80/34/600-PES-TWT-GREY" or "111/2/80-NYL-TWT-DYED"
    """
    if not desc:
        return ""
    
    desc_upper = desc.upper()
    
    # Extract spec
    m = re.match(r'(\d+(?:/\d+){1,3})', desc_upper)
    spec = m.group(1).replace("/", "-") if m else ""
    
    if not spec:
        return ""
    
    # Material code
    material = "NYL" if "NYLON" in desc_upper else "PES"
    
    # Type code
    type_code = ""
    if "MICRO" in desc_upper:
        type_code = "MICRO"
    elif "SUPER CATLON" in desc_upper or "CATLON" in desc_upper:
        type_code = "CATLON"
    elif "CATEX" in desc_upper:
        type_code = "CATEX"
    elif "HB" in desc_upper and "TWT" in desc_upper:
        type_code = "HB-TWT"
    elif "ROYAL WARP" in desc_upper:
        type_code = "ROYAL-WARP"
    elif "MH-LIM" in desc_upper or "MH LIM" in desc_upper:
        type_code = "MH-LIM"
    elif "T-TWT" in desc_upper or "T TWT" in desc_upper:
        type_code = "T-TWT"
    elif "TWT" in desc_upper:
        type_code = "TWT"
    elif "POY" in desc_upper:
        if "SD" in desc_upper:
            type_code = "SD-POY"
        elif "FD" in desc_upper:
            type_code = "FD-POY"
        elif "BRT" in desc_upper:
            type_code = "BRT-POY"
        else:
            type_code = "POY"
    elif "FDY" in desc_upper:
        type_code = "FDY"
    elif "DTY" in desc_upper:
        type_code = "DTY"
    
    # Process code
    process = ""
    if "DYED" in desc_upper:
        process = "DYD"
    elif "GREY" in desc_upper:
        process = "GRY"
    elif "RAW" in desc_upper:
        process = "RAW"
    
    parts = [p for p in [spec, material, type_code, process] if p]
    return "-".join(parts)


def normalize_uom(uom: str) -> str:
    """Normalize unit of measure."""
    if not uom:
        return "KGS"
    uom = uom.upper().strip()
    mapping = {
        "KG": "KGS", "KGS": "KGS", "KILO": "KGS", "KILOGRAMS": "KGS",
        "MTR": "MTR", "MTRS": "MTR", "METERS": "MTR",
        "PCS": "PCS", "PIECES": "PCS",
        "NOS": "NOS", "NUMBERS": "NOS",
    }
    return mapping.get(uom, uom)


# ── Main Normalization ────────────────────────────────────────────────────

def normalize_all(parsed_docs: list) -> list:
    """Flatten parsed documents into normalized line items."""
    normalized_items = []
    
    for doc in parsed_docs:
        doc_type = doc.get("doc_type", "UNKNOWN")
        source_file = doc.get("source_file", "")
        doc_date = doc.get("doc_date", "")
        doc_number = doc.get("doc_number", "")
        counterparty = doc.get("counterparty", "")
        
        counterparty_norm = normalize_vendor(counterparty)
        
        for item in doc.get("line_items", []):
            original_desc = item.get("item_description_original", "")
            
            # For purchase requisitions, vendor is per-item
            item_vendor = item.get("vendor", "")
            effective_counterparty = normalize_vendor(item_vendor) if item_vendor else counterparty_norm
            
            normalized = {
                # Traceability
                "source_file": source_file,
                "doc_type": doc_type,
                "doc_date": doc_date,
                "doc_number": doc_number,
                "raw_line": item.get("raw_line", ""),
                
                # Counterparty
                "counterparty_original": counterparty if not item_vendor else item_vendor,
                "counterparty": effective_counterparty,
                
                # Item Description
                "item_description_original": original_desc,
                "item_description_normalized": normalize_item_description(original_desc),
                "part_number": extract_part_number(original_desc, item.get("hsn_code", "")),
                "hsn_code": item.get("hsn_code", ""),
                
                # Quantities & Prices
                "quantity": item.get("quantity", 0.0),
                "uom": normalize_uom(item.get("uom", "KGS")),
                "unit_price": item.get("unit_price", 0.0),
                "amount": item.get("amount", 0.0),
                
                # Extras
                "shade": item.get("shade", ""),
                "lot_number": item.get("lot_number", ""),
                "grade": item.get("grade", ""),
                "challan_no": item.get("challan_no", ""),
            }
            
            normalized_items.append(normalized)
    
    # Summary stats
    by_type = defaultdict(int)
    by_counterparty = defaultdict(int)
    unique_items = set()
    
    for item in normalized_items:
        by_type[item["doc_type"]] += 1
        if item["counterparty"]:
            by_counterparty[item["counterparty"]] += 1
        if item["item_description_normalized"]:
            unique_items.add(item["item_description_normalized"])
    
    print(f"   Total normalized line items: {len(normalized_items)}")
    print(f"   Unique normalized items: {len(unique_items)}")
    print(f"   By document type:")
    for dt, count in sorted(by_type.items(), key=lambda x: -x[1]):
        print(f"     {dt:30s} {count:4d} items")
    print(f"   Top counterparties:")
    for cp, count in sorted(by_counterparty.items(), key=lambda x: -x[1])[:10]:
        print(f"     {cp:40s} {count:4d} items")
    
    return normalized_items
