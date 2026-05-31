"""
DTU B.Tech Admissions Cutoff PDF → JSON Parser
Supports the standard DTU round-wise cutoff PDF format.

Usage:
    python dtu_cutoff_parser.py <path_to_pdf> [output.json]

Output structure:
{
  "source_file": "...",
  "delhi_region": {
    "general": [
      {"sno": 1, "branch": "...", "GNGND": 11352, ...},
      ...
    ],
    "defense_cw": [
      {"sno": 1, "branch": "...", "GNCWD": {"rank": 205133, "round": "V(v)"}, ...},
      ...
    ],
    "kashmiri_migrants_km": {"branch": "CSE", "rank": 285540}
  },
  "outside_delhi_region": {
    "general": [...],
    "defense_cw": [...]
  }
}
"""

import sys
import re
import json
import pdfplumber


# ---------------------------------------------------------------------------
# Cell-level helpers
# ---------------------------------------------------------------------------

def clean_rank(value):
    """Convert a raw cell to an integer rank, or None."""
    if not value:
        return None
    v = str(value).strip().replace(' ', '')
    if not v or v in ('-', '–', 'N/A'):
        return None
    try:
        return int(v)
    except ValueError:
        return None


def parse_cw_cell(value):
    """
    Parse a Defense (CW) cell like '205133 (V(v))' or '86912 (VI)'.
    Returns {"rank": int, "round": str} or None.
    """
    if not value:
        return None
    v = str(value).strip()
    if not v or v in ('-', '–'):
        return None
    # Match 'NUMBER (ROUND)' where ROUND may contain nested parens
    match = re.match(r'^(\d+)\s*\(([^)]+(?:\([^)]*\))?[^)]*)\)$', v)
    if match:
        return {"rank": int(match.group(1)), "round": match.group(2)}
    try:
        return {"rank": int(v), "round": None}
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Column-map utilities
# ---------------------------------------------------------------------------

def extract_sno_from_row(row):
    """Scan first 6 cells for an S.No. integer. Returns (int, col_idx) or (None, None)."""
    for idx in range(min(6, len(row))):
        val = row[idx]
        if val and re.match(r'^\d+$', str(val).strip()):
            return int(str(val).strip()), idx
    return None, None


def build_header_map(table):
    """
    Merge the first two rows of a table to produce a single header list.
    Returns (hmap, sno_idx, branch_idx) where hmap maps col_name -> col_index.
    """
    if not table:
        return {}, None, None

    max_cols = max(len(r) for r in table[:2])
    merged_header = []
    for col in range(max_cols):
        val = None
        for row_idx in range(min(2, len(table))):
            cell = table[row_idx][col] if col < len(table[row_idx]) else None
            if cell and str(cell).strip():
                val = str(cell).strip()
                break
        merged_header.append(val or '')

    hmap = {}
    sno_idx = None
    branch_idx = None
    seen = set()
    for i, name in enumerate(merged_header):
        if not name:
            continue
        if name == 'S.No.' and sno_idx is None:
            sno_idx = i
        elif name == 'Branch' and branch_idx is None:
            branch_idx = i
        elif name not in seen:
            hmap[name] = i
            seen.add(name)

    return hmap, sno_idx, branch_idx


def detect_data_offset(table, sno_header_idx):
    """
    The pdfplumber rendering sometimes shifts data columns by 1 relative to
    the header. Find the first row with a valid S.No. and compute the offset.
    """
    if sno_header_idx is None:
        return 0
    for row in table:
        sno_int, sno_data_idx = extract_sno_from_row(row)
        if sno_int is not None and 1 <= sno_int <= 100:
            return sno_data_idx - sno_header_idx
    return 0


# ---------------------------------------------------------------------------
# Row merging
# ---------------------------------------------------------------------------

def merge_rows(raw_rows, branch_header_idx, offset):
    """
    Merge continuation rows into their parent.

    A row is a continuation if:
      (a) it has no detectable S.No., OR
      (b) its S.No. equals the previous merged row's S.No. (duplicate due
          to merged-cell rendering artefact).
    """
    branch_pos = (branch_header_idx or 4) + offset
    merged = []

    for raw_row in raw_rows:
        row = list(raw_row)
        sno_int, _ = extract_sno_from_row(row)

        is_continuation = False
        if sno_int is None:
            is_continuation = bool(merged)
        elif merged and merged[-1]['_sno'] == sno_int:
            is_continuation = True

        if is_continuation and merged:
            prev = merged[-1]
            # Extend branch name if this row adds more text
            cont = (row[branch_pos].strip()
                    if branch_pos < len(row) and row[branch_pos] else '')
            if cont:
                prev['_branch'] = (prev['_branch'] + ' ' + cont).strip()
            # Fill any None values with data from continuation row
            for j, v in enumerate(row):
                key = f'_c{j}'
                if prev.get(key) is None and v is not None:
                    prev[key] = v
        else:
            record = {
                '_sno': sno_int,
                '_branch': (row[branch_pos].strip()
                            if branch_pos < len(row) and row[branch_pos] else ''),
            }
            for j, v in enumerate(row):
                record[f'_c{j}'] = v
            merged.append(record)

    return merged


# ---------------------------------------------------------------------------
# Table parsers
# ---------------------------------------------------------------------------

def parse_general_table(table):
    """Parse main cutoff table → list of branch dicts with integer ranks."""
    if not table or len(table) < 2:
        return []

    hmap, sno_hdr, branch_hdr = build_header_map(table)
    offset = detect_data_offset(table, sno_hdr)

    # Find first data row
    data_start = 0
    for idx, row in enumerate(table):
        sno_int, _ = extract_sno_from_row(row)
        if sno_int is not None:
            data_start = idx
            break

    records = merge_rows(table[data_start:], branch_hdr, offset)

    results = []
    for rec in records:
        sno = rec['_sno']
        branch = rec['_branch']
        if not sno or not branch:
            continue
        entry = {"sno": sno, "branch": branch}
        for col_name, hdr_idx in hmap.items():
            data_idx = hdr_idx + offset
            entry[col_name] = clean_rank(rec.get(f'_c{data_idx}'))
        results.append(entry)

    return results


def parse_cw_table(table):
    """Parse Defense (CW) table → list of branch dicts with rank/round dicts."""
    if not table or len(table) < 2:
        return []

    hmap, sno_hdr, branch_hdr = build_header_map(table)
    offset = detect_data_offset(table, sno_hdr)

    data_start = 0
    for idx, row in enumerate(table):
        sno_int, _ = extract_sno_from_row(row)
        if sno_int is not None:
            data_start = idx
            break

    records = merge_rows(table[data_start:], branch_hdr, offset)

    results = []
    for rec in records:
        sno = rec['_sno']
        branch = rec['_branch']
        if not sno or not branch:
            continue
        entry = {"sno": sno, "branch": branch}
        for col_name, hdr_idx in hmap.items():
            data_idx = hdr_idx + offset
            entry[col_name] = parse_cw_cell(rec.get(f'_c{data_idx}'))
        results.append(entry)

    return results


# ---------------------------------------------------------------------------
# Kashmiri Migrants note
# ---------------------------------------------------------------------------

def extract_km_note(page_text):
    """Extract Kashmiri Migrants (KM) rank from page text."""
    if not page_text:
        return None
    match = re.search(
        r'Kashmiri\s+Migrants\s*\(KM\)[^\d]*(\d+)\s*\(([^)]+)\)',
        page_text, re.IGNORECASE)
    if match:
        return {"branch": match.group(2).strip(), "rank": int(match.group(1))}
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_dtu_cutoff_pdf(pdf_path):
    """
    Parse a DTU cutoff round PDF (2 pages) and return structured data.

    Page 1: Delhi Region general + KM note + Delhi CW
    Page 2: Outside Delhi general + Outside Delhi CW
    """
    result = {
        "source_file": pdf_path,
        "delhi_region": {
            "general": [],
            "defense_cw": [],
            "kashmiri_migrants_km": None,
        },
        "outside_delhi_region": {
            "general": [],
            "defense_cw": [],
        },
    }

    with pdfplumber.open(pdf_path) as pdf:
        if len(pdf.pages) >= 1:
            p1 = pdf.pages[0]
            t1 = p1.extract_tables()
            if len(t1) >= 1:
                result["delhi_region"]["general"] = parse_general_table(t1[0])
            if len(t1) >= 2:
                result["delhi_region"]["defense_cw"] = parse_cw_table(t1[1])
            result["delhi_region"]["kashmiri_migrants_km"] = extract_km_note(
                p1.extract_text())

        if len(pdf.pages) >= 2:
            p2 = pdf.pages[1]
            t2 = p2.extract_tables()
            if len(t2) >= 1:
                result["outside_delhi_region"]["general"] = (
                    parse_general_table(t2[0]))
            if len(t2) >= 2:
                result["outside_delhi_region"]["defense_cw"] = (
                    parse_cw_table(t2[1]))

    return result


def main():
    if len(sys.argv) < 2:
        print("Usage: python dtu_cutoff_parser.py <input.pdf> [output.json]")
        sys.exit(1)

    pdf_path = sys.argv[1]
    out_path = (sys.argv[2] if len(sys.argv) > 2
                else pdf_path.rsplit('.', 1)[0] + '.json')

    print(f"Parsing : {pdf_path}")
    data = parse_dtu_cutoff_pdf(pdf_path)

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Output  : {out_path}")
    dr = data["delhi_region"]
    od = data["outside_delhi_region"]
    print(f"\nSummary:")
    print(f"  Delhi General       : {len(dr['general'])} branches")
    print(f"  Delhi CW            : {len(dr['defense_cw'])} branches")
    print(f"  Kashmiri Migrants   : {dr['kashmiri_migrants_km']}")
    print(f"  Outside Delhi Gen   : {len(od['general'])} branches")
    print(f"  Outside Delhi CW    : {len(od['defense_cw'])} branches")


if __name__ == '__main__':
    main()