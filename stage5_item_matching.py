#!/usr/bin/env python3
"""
Stage 5: Item Matching
=======================
Determines when two line items across documents likely refer to the same
underlying item, even with different descriptions.

Matching strategies:
  1. Exact match — same normalized description
  2. Part number match — same denier/filament spec + yarn type
  3. Fuzzy string match — similar descriptions (rapidfuzz)
  4. Component-based match — same spec components with different phrasing
  5. HSN code + spec match — same tax classification + similar spec

Each match includes:
  - Confidence score (0.0 – 1.0)
  - Match type (exact, part_number, fuzzy, component, hsn)
  - Signals used (which fields matched and how)
"""

import re
from collections import defaultdict
from itertools import combinations

try:
    from rapidfuzz import fuzz, process
    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False


# ── Component Extraction ─────────────────────────────────────────────────

def extract_components(desc: str) -> dict:
    """Extract structured components from a yarn description.
    
    Returns dict with:
      - spec: "80/34/600" (denier/filament/twist)
      - material: "POLYESTER" or "NYLON"
      - yarn_type: "T-TWT", "CATEX", "POY", etc.
      - process: "GREY", "DYED", "RAW"
      - shade: color/shade info
    """
    if not desc:
        return {}
    
    desc = desc.upper().strip()
    components = {}
    
    # Extract spec (denier/filament/optional-twist)
    spec_match = re.match(r'(\d+/\d+(?:/\d+)?)', desc)
    if spec_match:
        components["spec"] = spec_match.group(1)
        # Parse denier and filament
        parts = components["spec"].split("/")
        components["denier"] = int(parts[0])
        components["filament"] = int(parts[1])
        if len(parts) > 2:
            components["twist"] = int(parts[2])
    
    # Material
    components["material"] = "NYLON" if "NYLON" in desc or "NYL" in desc else "POLYESTER"
    
    # Yarn type
    yarn_types = ["T-TWT", "TWT", "CATEX", "SD POY", "FD POY", "POY", "FDY", "DTY"]
    for yt in yarn_types:
        if yt in desc:
            components["yarn_type"] = yt
            break
    
    # Process state
    if "DYED" in desc or "DYD" in desc:
        components["process"] = "DYED"
    elif "GREY" in desc or "GRY" in desc:
        components["process"] = "GREY"
    elif "RAW" in desc:
        components["process"] = "RAW"
    
    return components


def components_match_score(comp_a: dict, comp_b: dict) -> tuple:
    """Score how well two component sets match.
    
    Returns (score, signals) where score is 0.0–1.0.
    """
    if not comp_a or not comp_b:
        return 0.0, ["missing components"]
    
    signals = []
    score = 0.0
    max_score = 0.0
    
    # Spec match (most important)
    max_score += 0.5
    if comp_a.get("spec") and comp_b.get("spec"):
        if comp_a["spec"] == comp_b["spec"]:
            score += 0.5
            signals.append(f"spec_exact: {comp_a['spec']}")
        elif comp_a.get("denier") == comp_b.get("denier") and comp_a.get("filament") == comp_b.get("filament"):
            # Same denier/filament but different twist
            score += 0.3
            signals.append(f"spec_partial: same denier/filament ({comp_a['denier']}/{comp_a['filament']}), different twist")
    
    # Material match
    max_score += 0.15
    if comp_a.get("material") == comp_b.get("material"):
        score += 0.15
        signals.append(f"material: {comp_a['material']}")
    
    # Yarn type match
    max_score += 0.2
    if comp_a.get("yarn_type") and comp_b.get("yarn_type"):
        if comp_a["yarn_type"] == comp_b["yarn_type"]:
            score += 0.2
            signals.append(f"yarn_type: {comp_a['yarn_type']}")
        elif _yarn_types_compatible(comp_a["yarn_type"], comp_b["yarn_type"]):
            score += 0.1
            signals.append(f"yarn_type_compatible: {comp_a['yarn_type']} ~ {comp_b['yarn_type']}")
    
    # Process state match
    max_score += 0.15
    if comp_a.get("process") and comp_b.get("process"):
        if comp_a["process"] == comp_b["process"]:
            score += 0.15
            signals.append(f"process: {comp_a['process']}")
    elif not comp_a.get("process") and not comp_b.get("process"):
        score += 0.05  # Both missing = neutral
    
    final_score = score / max_score if max_score > 0 else 0.0
    return final_score, signals


def _yarn_types_compatible(yt_a: str, yt_b: str) -> bool:
    """Check if two yarn types are compatible (close enough)."""
    # T-TWT and TWT are closely related
    compatible_groups = [
        {"T-TWT", "TWT"},
        {"POY", "SD POY", "FD POY"},
    ]
    for group in compatible_groups:
        if yt_a in group and yt_b in group:
            return True
    return False


# ── Matching Engine ───────────────────────────────────────────────────────

def find_item_matches(normalized_items: list) -> list:
    """Find matches between unique item descriptions across documents.
    
    Returns a list of match records, each containing:
      - item_a: first item description
      - item_b: second item description
      - confidence: 0.0–1.0
      - match_type: method that produced the match
      - signals: list of matching signals
      - example_sources_a: example source files for item_a
      - example_sources_b: example source files for item_b
    """
    # Collect unique item descriptions with metadata
    items_by_desc = defaultdict(list)
    for item in normalized_items:
        norm_desc = item.get("item_description_normalized", "").strip()
        orig_desc = item.get("item_description_original", "").strip()
        if norm_desc:
            items_by_desc[norm_desc].append(item)
    
    unique_items = list(items_by_desc.keys())
    print(f"   Unique normalized descriptions: {len(unique_items)}")
    
    if len(unique_items) < 2:
        return []
    
    # Pre-compute components for all items
    components = {desc: extract_components(desc) for desc in unique_items}
    
    matches = []
    seen_pairs = set()
    
    # ── Strategy 1: Component-based matching ──────────────────────────
    print("   Running component-based matching...")
    for i, desc_a in enumerate(unique_items):
        comp_a = components[desc_a]
        if not comp_a.get("spec"):
            continue
        
        for j in range(i + 1, len(unique_items)):
            desc_b = unique_items[j]
            comp_b = components[desc_b]
            if not comp_b.get("spec"):
                continue
            
            pair_key = tuple(sorted([desc_a, desc_b]))
            if pair_key in seen_pairs:
                continue
            
            score, signals = components_match_score(comp_a, comp_b)
            
            if score >= 0.6:  # Threshold for component match
                sources_a = [it["source_file"] for it in items_by_desc[desc_a][:3]]
                sources_b = [it["source_file"] for it in items_by_desc[desc_b][:3]]
                
                matches.append({
                    "item_a": desc_a,
                    "item_b": desc_b,
                    "confidence": round(score, 3),
                    "match_type": "component",
                    "signals": signals,
                    "example_sources_a": sources_a,
                    "example_sources_b": sources_b,
                    "original_descriptions_a": list(set(it["item_description_original"] for it in items_by_desc[desc_a][:5])),
                    "original_descriptions_b": list(set(it["item_description_original"] for it in items_by_desc[desc_b][:5])),
                })
                seen_pairs.add(pair_key)
    
    # ── Strategy 2: Fuzzy string matching ─────────────────────────────
    if HAS_RAPIDFUZZ and len(unique_items) > 1:
        print("   Running fuzzy string matching...")
        for i, desc_a in enumerate(unique_items):
            # Use rapidfuzz to find top candidates
            candidates = [unique_items[j] for j in range(i + 1, len(unique_items))]
            if not candidates:
                continue
            
            results = process.extract(
                desc_a, candidates,
                scorer=fuzz.token_sort_ratio,
                limit=5,
                score_cutoff=70  # Minimum similarity
            )
            
            for match_str, score, idx in results:
                pair_key = tuple(sorted([desc_a, match_str]))
                if pair_key in seen_pairs:
                    continue
                
                confidence = score / 100.0
                
                # Boost or penalize based on component analysis
                comp_a = components.get(desc_a, {})
                comp_b = components.get(match_str, {})
                comp_score, comp_signals = components_match_score(comp_a, comp_b)
                
                # Combine scores
                combined_confidence = 0.4 * confidence + 0.6 * comp_score
                
                if combined_confidence >= 0.55:
                    sources_a = [it["source_file"] for it in items_by_desc[desc_a][:3]]
                    sources_b = [it["source_file"] for it in items_by_desc[match_str][:3]]
                    
                    all_signals = [f"fuzzy_score: {score:.0f}%"] + comp_signals
                    
                    matches.append({
                        "item_a": desc_a,
                        "item_b": match_str,
                        "confidence": round(combined_confidence, 3),
                        "match_type": "fuzzy+component",
                        "signals": all_signals,
                        "example_sources_a": sources_a,
                        "example_sources_b": sources_b,
                        "original_descriptions_a": list(set(it["item_description_original"] for it in items_by_desc[desc_a][:5])),
                        "original_descriptions_b": list(set(it["item_description_original"] for it in items_by_desc[match_str][:5])),
                    })
                    seen_pairs.add(pair_key)
    
    # ── Strategy 3: HSN code + spec matching ──────────────────────────
    print("   Running HSN+spec matching...")
    items_by_hsn = defaultdict(list)
    for item in normalized_items:
        hsn = item.get("hsn_code", "")
        if hsn:
            items_by_hsn[hsn].append(item)
    
    for hsn, hsn_items in items_by_hsn.items():
        descs_in_hsn = set()
        for item in hsn_items:
            norm = item.get("item_description_normalized", "").strip()
            if norm:
                descs_in_hsn.add(norm)
        
        for desc_a, desc_b in combinations(descs_in_hsn, 2):
            pair_key = tuple(sorted([desc_a, desc_b]))
            if pair_key in seen_pairs:
                continue
            
            comp_a = components.get(desc_a, {})
            comp_b = components.get(desc_b, {})
            
            # Same HSN means same product category
            comp_score, comp_signals = components_match_score(comp_a, comp_b)
            
            # HSN match boosts confidence
            confidence = comp_score * 0.7 + 0.3  # HSN adds 0.3 base
            
            if confidence >= 0.55:
                sources_a = [it["source_file"] for it in items_by_desc.get(desc_a, [])[:3]]
                sources_b = [it["source_file"] for it in items_by_desc.get(desc_b, [])[:3]]
                
                all_signals = [f"same_hsn: {hsn}"] + comp_signals
                
                matches.append({
                    "item_a": desc_a,
                    "item_b": desc_b,
                    "confidence": round(min(confidence, 1.0), 3),
                    "match_type": "hsn+component",
                    "signals": all_signals,
                    "example_sources_a": sources_a,
                    "example_sources_b": sources_b,
                    "original_descriptions_a": list(set(it["item_description_original"] for it in items_by_desc.get(desc_a, [])[:5])),
                    "original_descriptions_b": list(set(it["item_description_original"] for it in items_by_desc.get(desc_b, [])[:5])),
                })
                seen_pairs.add(pair_key)
    
    # Sort by confidence descending
    matches.sort(key=lambda m: -m["confidence"])
    
    # Print summary
    print(f"\n   Total suggested matches: {len(matches)}")
    if matches:
        print(f"   Confidence distribution:")
        high = sum(1 for m in matches if m["confidence"] >= 0.8)
        med = sum(1 for m in matches if 0.6 <= m["confidence"] < 0.8)
        low = sum(1 for m in matches if m["confidence"] < 0.6)
        print(f"     High (≥0.8): {high}")
        print(f"     Medium (0.6–0.8): {med}")
        print(f"     Low (<0.6): {low}")
        
        print(f"\n   Top matches:")
        for m in matches[:10]:
            print(f"     [{m['confidence']:.2f}] {m['item_a']}")
            print(f"        ↔ {m['item_b']}")
            print(f"        Signals: {', '.join(m['signals'][:3])}")
            print()
    
    return matches


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
    matches = find_item_matches(normalized)
    print(json.dumps(matches[:5], indent=2))
