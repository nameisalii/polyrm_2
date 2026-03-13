#!/usr/bin/env python3
"""
Stage 1: Document Classification
=================================
Reads each PDF's first page and classifies it into a document type
based on keyword/header detection.

Document types observed in the dataset:
  - TAX_INVOICE        — Sales invoices issued by Valson to customers
  - DELIVERY_INSTRUCTION — Shipping/delivery instructions for orders
  - PURCHASE_ORDER     — POs issued by Valson to suppliers (e.g. Reliance)
  - GOODS_RECEIPT_NOTE — GRNs confirming receipt of purchased goods
  - PACKING_LIST       — Packing/production lists for goods
  - PURCHASE_REQUISITION — Internal purchase requisitions
  - UNKNOWN            — Could not classify (e.g., blank/scanned PDFs)
"""

import os
import pdfplumber


# Classification rules ordered by specificity
CLASSIFICATION_RULES = [
    ("GOODS_RECEIPT_NOTE",    ["GOODS RECEIPT NOTE", "GRN #"]),
    ("PURCHASE_REQUISITION",  ["PURCHASE REQUISITION"]),
    ("PURCHASE_ORDER",        ["PURCHASE ORDER"]),
    ("DELIVERY_INSTRUCTION",  ["DELIVERY INSTRUCTION"]),
    ("TAX_INVOICE",           ["TAX INVOICE", "INVOICE FOR GOODS"]),
    ("PACKING_LIST",          ["PACKING LIST"]),
    ("DELIVERY_CHALLAN",      ["DELIVERY CHALLAN"]),
    ("CREDIT_NOTE",           ["CREDIT NOTE"]),
    ("DEBIT_NOTE",            ["DEBIT NOTE"]),
]


def classify_document(pdf_path: str) -> dict:
    """Classify a single PDF and return metadata."""
    filename = os.path.basename(pdf_path)
    result = {
        "source_file": filename,
        "file_path": pdf_path,
        "doc_type": "UNKNOWN",
        "classification_confidence": 0.0,
        "matched_keywords": [],
        "num_pages": 0,
        "has_text": False,
    }

    try:
        with pdfplumber.open(pdf_path) as pdf:
            result["num_pages"] = len(pdf.pages)
            text = ""
            for page in pdf.pages[:2]:  # First 2 pages
                page_text = page.extract_text()
                if page_text:
                    text += page_text

            result["has_text"] = len(text.strip()) > 0

            if not text.strip():
                result["doc_type"] = "UNKNOWN"
                result["classification_confidence"] = 0.0
                return result

            text_upper = text.upper()

            for doc_type, keywords in CLASSIFICATION_RULES:
                matched = [kw for kw in keywords if kw in text_upper]
                if matched:
                    result["doc_type"] = doc_type
                    result["matched_keywords"] = matched
                    result["classification_confidence"] = min(1.0, len(matched) * 0.5 + 0.5)
                    break

    except Exception as e:
        result["error"] = str(e)

    return result


def classify_all(pdf_files: list) -> list:
    """Classify all PDFs and return a list of classification records."""
    results = []
    type_counts = {}

    for pdf_path in pdf_files:
        record = classify_document(pdf_path)
        results.append(record)
        dtype = record["doc_type"]
        type_counts[dtype] = type_counts.get(dtype, 0) + 1

    # Print distribution
    print("\n   Document type distribution:")
    for dtype, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"     {dtype:30s} {count:4d}")
    print()

    return results


if __name__ == "__main__":
    import sys
    docs_dir = sys.argv[1] if len(sys.argv) > 1 else "./documents"
    pdf_files = sorted([
        os.path.join(docs_dir, f)
        for f in os.listdir(docs_dir)
        if f.lower().endswith(".pdf")
    ])
    results = classify_all(pdf_files)
    import json
    print(json.dumps(results, indent=2))
