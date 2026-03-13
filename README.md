# Polymr Document Parsing & Price History Pipeline

This project is a technical solution for the Polymr Document Processing assessment. It implements an automated pipeline to extract structured procurement data from semi-structured PDFs and build cross-document price histories.

## Key Features
- **Automated Classification**: Identifies Invoices, POs, GRNs, etc. from raw PDF content.
- **Robust Parsing**: Handles complex Indian textile document layouts using word-position analysis to overcome PDF text merging challenges.
- **Data Normalization**: Standardizes yarn specifications, material types, and vendor names.
- **Price History**: Generates a time-series view of item pricing across diverse suppliers.
- **Semantic Item Matching**: Uses component analysis and fuzzy matching to link similar items described differently.

## Quick Start
1. **Setup**:
   ```bash
   pip install pdfplumber pandas rapidfuzz
   ```
2. **Execute**:
   ```bash
   python3 pipeline.py
   ```
3. **Review**:
   Check the `output/` directory for JSON and CSV results.

## Documentation
- [Approach & Solution Architecture](SOLUTION.md) - Detailed explanation of the logic and trade-offs.
- [Pipeline Orchestrator](pipeline.py) - Main entry point.

## Project Structure
- `stage1_classify.py`: Document type identification.
- `stage2_parse.py`: Positional and regex extraction.
- `stage3_normalize.py`: Data cleaning and standardizing.
- `stage4_price_history.py`: Pricing aggregation.
- `stage5_item_matching.py`: Semantic similarity engine.
