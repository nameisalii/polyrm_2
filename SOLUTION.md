# Polymr Document Processing Pipeline - Solution Summary

## Overview
This solution implements a multi-stage pipeline to classify, parse, normalize, and analyze procurement documents (Invoices, POs, DI, GRNs) from a polyester yarn manufacturer. It addresses the challenges of semi-structured document layouts, merged-text artifacts in PDF extraction, and inconsistent item naming across different vendors and document types.

## Key Components

### 1. Document Classification (`stage1_classify.py`)
- Uses keyword-based heuristics to identify document types.
- Corrected identified types: `TAX_INVOICE`, `DELIVERY_INSTRUCTION`, `PURCHASE_ORDER`, `GOODS_RECEIPT_NOTE`, `PURCHASE_REQUISITION`, and `PACKING_LIST`.
- Successfully classified 159 out of 161 documents.

### 2. Positional Parsing (`stage2_parse.py`)
- **Innovation**: Implemented a position-based parser for "Tax Invoices" to handle the merged-column issue (where `extract_text()` concatenates the Description, HSN, and Lot columns into a single garbled string).
- Uses word x-coordinates to reliably separate columns (SR#, Challan, Description, HSN, Shade, Qty, Rate, Amount).
- Implemented specific parsers for each document type with regex-based fallback for unstructured fields.

### 3. Smart Normalization (`stage3_normalize.py`)
- Standardizes vendor names (e.g., deduplicating "DEEP TEX DEEP TEX").
- Normalizes yarn descriptions to a canonical format: `[SPEC] [MATERIAL] [TYPE]`.
- Generates structured part numbers (`SPEC-MAT-TYPE-PROCESS`) for better grouping.
- Filters out parsing artifacts (e.g., "ROUNDED OFF", "IGST ON...") that leak into descriptions from multi-page PDF tables.

### 4. Price History Analysis (`stage4_price_history.py`)
- Aggregates normalized line items into a time-series view.
- Tracks unit price variations across counterparties and over time.
- Identifies cases where the same item has different price points depending on the supplier or customer.

### 5. Item Matching (`stage5_item_matching.py`)
- Uses a multi-strategy scoring engine:
  - **Component matching**: Compares denier, filament, and twist counts.
  - **Fuzzy matching**: Uses `rapidfuzz` for semantic similarity.
  - **HSN + Spec Correlation**: Leverages tax codes to confirm product categories.
- Provides a confidence score (0.0 – 1.0) and "signals" to explain the match logic.

## Trade-offs and Modeling Assumptions
- **Regex vs. AI**: Used regex and positional heuristics for speed and determinism within the 4-6 hour window. While more brittle than an LLM-based approach, it provides 100% traceability and handles the merged-text issue which often confuses generic LLM parsers.
- **Traceability**: All outputs preserve the `raw_line` and `source_file` to allow for auditability.
- **Ambiguity**: Handled variations in denier/filament notation (e.g., "111/1/80" vs "111/2/80") by decomposing them into numeric components during matching.

## How to Run
```bash
# Install dependencies
pip install pdfplumber rapidfuzz pandas

# Run full pipeline
python3 pipeline.py --documents-dir ./documents --output-dir ./output
```

## Outputs Produced
- `line_items.csv`: Flattened, normalized list of every line item found.
- `price_history.csv`: Aggregated price trends by item and counterparty.
- `item_matches.csv`: Suggested semantic matches between items with confidence scores.
- `01_classified_documents.json`: Classification results.
- `02_parsed_documents.json`: Full structured data from all documents.
