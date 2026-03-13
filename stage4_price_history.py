#!/usr/bin/env python3
"""
Stage 4: Price History
=======================
Constructs a time-aware view of pricing across:
  - Items (by normalized description / part number)
  - Counterparties (vendors or customers)
  - Document types (PO price vs Invoice price vs DI price)
  - Time (document dates)

Only includes line items with valid prices (> 0).
Groups by a composite item key to track the same item over time.
"""

from collections import defaultdict


def build_item_key(item: dict) -> str:
    """Create a grouping key for an item.
    
    Uses the normalized description as the primary key.
    Falls back to part_number if description is empty.
    """
    desc = item.get("item_description_normalized", "").strip()
    part = item.get("part_number", "").strip()
    
    if desc:
        return desc
    elif part:
        return part
    else:
        return item.get("item_description_original", "UNKNOWN").strip()


def build_price_history(normalized_items: list) -> dict:
    """
    Build a price history keyed by normalized item.
    
    Returns:
        {
            "item_key": [
                {
                    "counterparty": "...",
                    "doc_type": "TAX_INVOICE",
                    "doc_date": "2026-01-04",
                    "unit_price": 171.50,
                    "quantity": 300.0,
                    "uom": "KGS",
                    "source_file": "doc_002.pdf",
                    "doc_number": "DDS/00484/25-26",
                    "amount": 51450.00,
                    "shade": "",
                    "lot_number": ""
                },
                ...
            ]
        }
    
    Each item_key maps to a list of price observations sorted by date.
    """
    history = defaultdict(list)
    
    skipped = 0
    included = 0
    
    for item in normalized_items:
        # Only include items with valid pricing data
        price = item.get("unit_price", 0.0)
        if price <= 0:
            skipped += 1
            continue
        
        item_key = build_item_key(item)
        
        record = {
            "counterparty": item.get("counterparty", ""),
            "doc_type": item.get("doc_type", ""),
            "doc_date": item.get("doc_date", ""),
            "unit_price": price,
            "quantity": item.get("quantity", 0.0),
            "uom": item.get("uom", "KGS"),
            "amount": item.get("amount", 0.0),
            "source_file": item.get("source_file", ""),
            "doc_number": item.get("doc_number", ""),
            "shade": item.get("shade", ""),
            "lot_number": item.get("lot_number", ""),
            "item_description_original": item.get("item_description_original", ""),
            "part_number": item.get("part_number", ""),
            "hsn_code": item.get("hsn_code", ""),
        }
        
        history[item_key].append(record)
        included += 1
    
    # Sort each item's history by date
    for item_key in history:
        history[item_key].sort(key=lambda r: r.get("doc_date", ""))
    
    # Print summary
    print(f"   Price records: {included} included, {skipped} skipped (no price)")
    print(f"   Unique items with price data: {len(history)}")
    
    # Show items with most price observations
    by_count = sorted(history.items(), key=lambda x: -len(x[1]))
    print(f"\n   Top items by observation count:")
    for item_key, records in by_count[:15]:
        counterparties = set(r["counterparty"] for r in records if r["counterparty"])
        date_range = ""
        dates = [r["doc_date"] for r in records if r["doc_date"]]
        if dates:
            date_range = f"{min(dates)} to {max(dates)}"
        prices = [r["unit_price"] for r in records]
        price_range = f"₹{min(prices):.2f} – ₹{max(prices):.2f}" if prices else ""
        
        print(f"     {item_key:45s} {len(records):3d} obs | {len(counterparties):2d} parties | {price_range} | {date_range}")
    
    # Cross-vendor comparison
    print(f"\n   Items sold to multiple counterparties:")
    multi_vendor = {k: v for k, v in history.items() 
                    if len(set(r["counterparty"] for r in v if r["counterparty"])) > 1}
    for item_key, records in sorted(multi_vendor.items(), key=lambda x: -len(x[1]))[:10]:
        by_cp = defaultdict(list)
        for r in records:
            if r["counterparty"]:
                by_cp[r["counterparty"]].append(r["unit_price"])
        
        print(f"     {item_key}")
        for cp, prices in sorted(by_cp.items()):
            avg = sum(prices) / len(prices)
            print(f"       {cp:40s} avg ₹{avg:.2f} ({len(prices)} records)")
    
    # Price changes over time
    print(f"\n   Items with price variation (same counterparty):")
    price_var_count = 0
    for item_key, records in sorted(history.items()):
        by_cp = defaultdict(list)
        for r in records:
            if r["counterparty"] and r["doc_date"]:
                by_cp[r["counterparty"]].append((r["doc_date"], r["unit_price"]))
        
        for cp, date_prices in by_cp.items():
            unique_prices = set(p for _, p in date_prices)
            if len(unique_prices) > 1 and price_var_count < 15:
                date_prices.sort()
                changes = []
                for j in range(1, len(date_prices)):
                    if date_prices[j][1] != date_prices[j-1][1]:
                        changes.append(f"{date_prices[j-1][0]}: ₹{date_prices[j-1][1]:.2f} → {date_prices[j][0]}: ₹{date_prices[j][1]:.2f}")
                if changes:
                    print(f"     {item_key} @ {cp}")
                    for c in changes[:3]:
                        print(f"       {c}")
                    price_var_count += 1
    
    return dict(history)


if __name__ == "__main__":
    import sys, json
    from stage1_classify import classify_all
    from stage2_parse import parse_all
    from stage3_normalize import normalize_all
    
    docs_dir = sys.argv[1] if len(sys.argv) > 1 else "./documents"
    pdf_files = sorted([
        os.path.join(docs_dir, f) for f in os.listdir(docs_dir)
        if f.lower().endswith(".pdf")
    ])
    classified = classify_all(pdf_files)
    parsed = parse_all(pdf_files, classified)
    normalized = normalize_all(parsed)
    history = build_price_history(normalized)
    
    # Output summary
    print(f"\n\nPrice history for {len(history)} unique items")
