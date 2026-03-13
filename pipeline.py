#!/usr/bin/env python3
"""
Polymr Technical Assessment — Document Processing & Item Normalization Pipeline
================================================================================
Main pipeline orchestrator. Runs all stages:
  1. Classify documents by type
  2. Parse PDFs → extract structured data (header + line items)
  3. Normalize line items (descriptions, vendors, part numbers)
  4. Build price history across time and vendors
  5. Suggest item matches across documents (fuzzy + rule-based)

Usage:
    python3 pipeline.py [--documents-dir ./documents] [--output-dir ./output]
"""

import argparse
import json
import os
import sys
import time

from stage1_classify import classify_all
from stage2_parse import parse_all
from stage3_normalize import normalize_all
from stage4_price_history import build_price_history
from stage5_item_matching import find_item_matches


def main():
    parser = argparse.ArgumentParser(description="Polymr Document Processing Pipeline")
    parser.add_argument("--documents-dir", default="./documents", help="Path to PDF folder")
    parser.add_argument("--output-dir", default="./output", help="Path for structured outputs")
    args = parser.parse_args()

    docs_dir = os.path.abspath(args.documents_dir)
    out_dir = os.path.abspath(args.output_dir)
    os.makedirs(out_dir, exist_ok=True)

    pdf_files = sorted([
        os.path.join(docs_dir, f)
        for f in os.listdir(docs_dir)
        if f.lower().endswith(".pdf")
    ])
    print(f"\n{'='*70}")
    print(f"  Polymr Document Processing Pipeline")
    print(f"  Documents: {len(pdf_files)} PDFs in {docs_dir}")
    print(f"  Output:    {out_dir}")
    print(f"{'='*70}\n")

    # ── Stage 1: Classify ─────────────────────────────────────────────────
    t0 = time.time()
    print("▶ Stage 1: Classifying documents...")
    classified = classify_all(pdf_files)
    _save(classified, out_dir, "01_classified_documents.json")
    _print_summary("Classification", classified, time.time() - t0)

    # ── Stage 2: Parse ────────────────────────────────────────────────────
    t0 = time.time()
    print("▶ Stage 2: Parsing documents & extracting line items...")
    parsed = parse_all(pdf_files, classified)
    _save(parsed, out_dir, "02_parsed_documents.json")
    _print_summary("Parsing", parsed, time.time() - t0)

    # ── Stage 3: Normalize ────────────────────────────────────────────────
    t0 = time.time()
    print("▶ Stage 3: Normalizing line items...")
    normalized = normalize_all(parsed)
    _save(normalized, out_dir, "03_normalized_line_items.json")
    _print_summary("Normalization", normalized, time.time() - t0)

    # ── Stage 4: Price History ────────────────────────────────────────────
    t0 = time.time()
    print("▶ Stage 4: Building price history...")
    price_history = build_price_history(normalized)
    _save(price_history, out_dir, "04_price_history.json")
    _print_summary("Price History", price_history, time.time() - t0)

    # ── Stage 5: Item Matching ────────────────────────────────────────────
    t0 = time.time()
    print("▶ Stage 5: Suggesting item matches...")
    matches = find_item_matches(normalized)
    _save(matches, out_dir, "05_item_matches.json")
    _print_summary("Item Matching", matches, time.time() - t0)

    # ── Summary CSV outputs ───────────────────────────────────────────────
    print("▶ Generating CSV summaries...")
    _export_csvs(normalized, price_history, matches, out_dir)

    print(f"\n{'='*70}")
    print(f"  Pipeline complete. All outputs in: {out_dir}")
    print(f"{'='*70}\n")


# ── Helpers ────────────────────────────────────────────────────────────────

def _save(data, out_dir, filename):
    path = os.path.join(out_dir, filename)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"   ✓ Saved {filename} ({os.path.getsize(path):,} bytes)")


def _print_summary(stage, data, elapsed):
    if isinstance(data, list):
        count = len(data)
    elif isinstance(data, dict):
        count = sum(len(v) if isinstance(v, list) else 1 for v in data.values())
    else:
        count = "?"
    print(f"   ⏱ {stage} done in {elapsed:.1f}s — {count} records\n")


def _export_csvs(normalized, price_history, matches, out_dir):
    """Export key data as CSVs for easy viewing."""
    import csv

    # ── Line items CSV ────────────────────────────────────────────────
    li_path = os.path.join(out_dir, "line_items.csv")
    fields = [
        "source_file", "doc_type", "doc_date", "doc_number",
        "counterparty", "item_description_original", "item_description_normalized",
        "part_number", "hsn_code", "quantity", "uom", "unit_price",
        "amount", "shade", "lot_number"
    ]
    with open(li_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for item in normalized:
            w.writerow(item)
    print(f"   ✓ Saved line_items.csv")

    # ── Price history CSV ─────────────────────────────────────────────
    ph_path = os.path.join(out_dir, "price_history.csv")
    ph_fields = [
        "normalized_item", "counterparty", "doc_type", "doc_date",
        "unit_price", "quantity", "uom", "source_file", "doc_number"
    ]
    with open(ph_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ph_fields, extrasaction="ignore")
        w.writeheader()
        for item_key, records in price_history.items():
            for rec in records:
                row = {**rec, "normalized_item": item_key}
                w.writerow(row)
    print(f"   ✓ Saved price_history.csv")

    # ── Item matches CSV ──────────────────────────────────────────────
    im_path = os.path.join(out_dir, "item_matches.csv")
    im_fields = [
        "item_a", "item_b", "confidence", "match_type", "signals"
    ]
    with open(im_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=im_fields, extrasaction="ignore")
        w.writeheader()
        for m in matches:
            row = {
                "item_a": m.get("item_a", ""),
                "item_b": m.get("item_b", ""),
                "confidence": m.get("confidence", ""),
                "match_type": m.get("match_type", ""),
                "signals": json.dumps(m.get("signals", []))
            }
            w.writerow(row)
    print(f"   ✓ Saved item_matches.csv")


if __name__ == "__main__":
    main()
