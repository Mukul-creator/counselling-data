#!/usr/bin/env python3
"""
MHT CET Engineering CAP Cutoff PDF -> JSON Converter
=====================================================
Handles the official CET Cell PDF table format (both MS and AI cutoffs):

  Dense table (MS cutoff - all categories filled):
    Stage   GOPENS    GSCS     GSTS   ...
      I     37591     50510    94334  ...
           (88.96)   (92.33)  (99.49) ...

  Sparse table (AI/MH cutoff - only allotted categories shown):
    Stage   GOPENS   GOBCS   LOPENS   LSTS    TFWS   DEFROBCS  ORPHAN
      I     41333    42953   35333            44533   128872
           (92.34)  (93.16) (91.76)          (91.23) (20.31)
     II                             88063
                                   (70.81)
    VII                                                          45160
                                                                (91.00)
  I-Non                                      49225
   PWD                                      (90.52)

Key: The Nth percentile always corresponds to the Nth non-empty merit (left-to-right).
     Merits are assigned to columns by proximity to the column header start position.

Usage:
    python3 mhtcet_pdf_to_json.py <input.pdf> [options]

Examples:
    python3 mhtcet_pdf_to_json.py 2025ENGG_CAP1_CutOff.pdf
    python3 mhtcet_pdf_to_json.py 2024ENGG_CAP2_AI_CutOff.pdf
    python3 mhtcet_pdf_to_json.py myfile.pdf --year 2025 --round CAP1 --quota MS
    python3 mhtcet_pdf_to_json.py 2025ENGG_CAP1_CutOff.pdf --pretty
    python3 mhtcet_pdf_to_json.py 2025ENGG_CAP1_CutOff.pdf --sample 20
    python3 mhtcet_pdf_to_json.py 2025ENGG_CAP1_CutOff.pdf --debug

Requirements (install ONE):
    sudo apt install poppler-utils   (Linux — recommended, likely already installed)
    brew install poppler             (macOS)
    pip install pypdf
    pip install pdfplumber
"""

import re
import json
import sys
import os
import argparse
import subprocess


# ─────────────────────────────────────────────────────────
# 1. PDF TEXT EXTRACTION
# ─────────────────────────────────────────────────────────

def _try_pdftotext(pdf_path):
    """pdftotext -layout preserves column positions (wide lines)."""
    try:
        r = subprocess.run(
            ["pdftotext", "-layout", pdf_path, "-"],
            capture_output=True, text=True, timeout=300
        )
        if r.returncode == 0 and len(r.stdout.strip()) > 100:
            return r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _try_pdftotext_simple(pdf_path):
    try:
        r = subprocess.run(
            ["pdftotext", pdf_path, "-"],
            capture_output=True, text=True, timeout=300
        )
        if r.returncode == 0 and len(r.stdout.strip()) > 100:
            return r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _try_pdfplumber(pdf_path):
    try:
        import pdfplumber
        pages = []
        with pdfplumber.open(pdf_path) as pdf:
            total = len(pdf.pages)
            for i, page in enumerate(pdf.pages):
                t = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
                pages.append(t)
                if (i + 1) % 50 == 0:
                    print(f"  Read {i+1}/{total} pages...", flush=True)
        return "\n".join(pages)
    except ImportError:
        pass
    except Exception as e:
        print(f"  [pdfplumber] {e}", file=sys.stderr)
    return None


def _try_pypdf(pdf_path):
    try:
        from pypdf import PdfReader
        reader = PdfReader(pdf_path)
        pages = []
        total = len(reader.pages)
        for i, page in enumerate(reader.pages):
            t = page.extract_text() or ""
            pages.append(t)
            if (i + 1) % 50 == 0:
                print(f"  Read {i+1}/{total} pages...", flush=True)
        return "\n".join(pages)
    except ImportError:
        pass
    except Exception as e:
        print(f"  [pypdf] {e}", file=sys.stderr)
    return None


def extract_pdf_text(pdf_path):
    print(f"\nExtracting: {os.path.basename(pdf_path)}")
    for label, fn in [
        ("pdftotext -layout", _try_pdftotext),
        ("pdftotext (simple)", _try_pdftotext_simple),
        ("pdfplumber",         _try_pdfplumber),
        ("pypdf",              _try_pypdf),
    ]:
        print(f"  Trying {label}...", end=" ", flush=True)
        text = fn(pdf_path)
        if text and len(text.strip()) > 100:
            print(f"OK  ({len(text):,} chars)")
            return text, label
        print("failed")

    print("\nERROR: No extraction method worked. Install one of:")
    print("  sudo apt install poppler-utils  (Linux)")
    print("  brew install poppler            (macOS)")
    print("  pip install pypdf")
    sys.exit(1)


# ─────────────────────────────────────────────────────────
# 2. AUTO-DETECT METADATA FROM FILENAME
# ─────────────────────────────────────────────────────────

def detect_from_filename(path):
    """
    2025ENGG_CAP1_CutOff.pdf    -> (2025, 'CAP1', 'MS')
    2024ENGG_CAP2_AI_CutOff.pdf -> (2024, 'CAP2', 'AI')
    """
    name = os.path.basename(path).upper()
    m_year  = re.search(r"(20\d{2})", name)
    m_round = re.search(r"(CAP\d+)", name)
    quota   = "AI" if ("_AI_" in name or name.endswith("_AI.PDF")) else "MS"
    return (
        int(m_year.group(1))  if m_year  else None,
        m_round.group(1)      if m_round else None,
        quota,
    )


# ─────────────────────────────────────────────────────────
# 3. TABLE PARSING HELPERS
# ─────────────────────────────────────────────────────────

# Stage detection: I-Non must come before roman numerals to avoid partial match
_STAGE_RE = re.compile(r"^\s*(I-Non\b|VII|VI|IV|I{1,3}|V)\b", re.IGNORECASE)

# I-Non continuation line (second line of two-line "I-Non / PWD" marker)
_INON_CONT_RE = re.compile(r"^\s*(PWD|Defence|Def)\s*$", re.IGNORECASE)

# Valid category code token: starts with letter, mix of uppercase letters+digits, 2-15 chars
# Handles: GOPENS, GNT3S, DEFRNT3S, PWDRSCS, EWS, TFWS, ORPHAN, MI, GSEBCS etc.
_CAT_RE = re.compile(r"^[A-Z][A-Z0-9]{1,14}$")

# Noise words that appear as tokens but are NOT category codes
_NOISE = {
    "STAGE", "STATE", "LEVEL", "HOME", "UNIVERSITY", "SEATS", "ALLOTTED",
    "OTHER", "THAN", "MINORITY", "GOVERNMENT", "AUTONOMOUS", "AIDED",
    "STATUS", "CUT", "OFF", "LIST", "DEGREE", "COURSES", "ENGINEERING",
    "TECHNOLOGY", "MASTER", "INTEGRATED", "YEARS", "YEAR", "ADMISSION",
    "LEGENDS", "FIGURES", "BRACKET", "INDICATES", "MERIT", "PERCENTILE",
    "MAHARASHTRA", "COMMON", "ENTRANCE", "TEST", "CELL", "CANDIDATURE",
    "CANDIDATES", "ROUND", "ACADEMIC", "FOUR", "FULL", "TIME",
}

def is_cat_token(tok):
    """True if token looks like a category code (GOPENS, GSCS, EWS, TFWS etc.)"""
    return bool(_CAT_RE.match(tok)) and tok not in _NOISE


def get_cat_starts(header_line):
    """
    Parse a category header line and return sorted list of (column_start, cat_code).
    Example: ' Stage    GOPENS     GOBCS    LOPENS ...'
    Returns: [(10, 'GOPENS'), (21, 'GOBCS'), (30, 'LOPENS'), ...]
    """
    result = []
    for m in re.finditer(r"([A-Z][A-Z0-9]+)", header_line):
        tok = m.group(1)
        if tok == "Stage" or not is_cat_token(tok):
            continue
        result.append((m.start(), tok))
    return sorted(result)


def is_cat_header_line(line):
    """
    Return cat_starts if this line is a category header row (3+ category codes).
    Otherwise return None.
    """
    tokens = re.findall(r"[A-Z][A-Z0-9]+", line)
    cat_tokens = [t for t in tokens if is_cat_token(t)]
    if len(cat_tokens) >= 2 and len(cat_tokens) >= len(tokens) * 0.5:
        cats = get_cat_starts(line)
        if len(cats) >= 2:
            return cats
    return None


def nearest_cat(pos, cat_starts):
    """Return the category whose header start is closest to pos."""
    best_cat, best_dist = None, 9999
    for col_start, cat in cat_starts:
        dist = abs(pos - col_start)
        if dist < best_dist:
            best_dist = dist
            best_cat = cat
    return best_cat


def parse_merit_line(line, cat_starts):
    """
    Extract merit numbers from a data row, assigned to columns by proximity.
    Returns dict {cat_code: merit_int}.
    """
    merits = {}
    for m in re.finditer(r"\b(\d{3,9})\b", line):
        cat = nearest_cat(m.start(), cat_starts)
        if cat and cat not in merits:
            merits[cat] = int(m.group(1))
    return merits


def parse_pct_line(line):
    """Extract all percentile values from a line in left-to-right order."""
    return [float(m.group(1)) for m in re.finditer(r"\((\d+\.\d+)\)", line)]


def detect_stage(line):
    """Return stage string or None if line is not a stage marker."""
    m = _STAGE_RE.match(line)
    if m:
        return m.group(1).upper().replace("-NON", "-Non")
    return None


def is_inon_continuation(line):
    """True if line is the second line of 'I-Non / PWD' marker."""
    return bool(_INON_CONT_RE.match(line))


def detect_seat_type(line):
    s = line.strip()
    for kw in [
        "State Level",
        "Home University Seats Allotted to Home University",
        "Home University Seats Allotted to Other Than Home University",
        "Other Than Home University Seats Allotted to Other Than Home University",
        "Other Than Home University Seats Allotted to Home University",
        "Minority Seats",
    ]:
        if s.startswith(kw):
            return s
    return None


# ─────────────────────────────────────────────────────────
# 4. CORE PARSER
# ─────────────────────────────────────────────────────────

def parse_cutoff_text(text, year, cap_round, quota, debug=False):
    """
    Parse the full extracted PDF text into a list of cutoff records.

    Handles:
    - Dense tables  (all categories present, every cell filled)
    - Sparse tables (only allotted categories shown, empty cells are spaces)
    - Multiple stages per table (I, II, VII, I-Non PWD)
    - Both 5-digit and 4-digit college codes
    - Both 10-digit and 9-digit branch codes
    """
    records = []
    college_count = 0

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    n = len(lines)

    college_code = college_name = ""
    branch_code  = branch_name  = ""
    status       = ""
    seat_type    = "State Level"
    cat_starts   = []   # current table's column positions

    def save_record(merits, pcts_ordered, stage):
        """Build a record from current context + merit/pct data."""
        if not merits or not branch_code:
            return
        # Sort merits by column position (left-to-right)
        merit_ordered = sorted(
            [(next((col for col, c in cat_starts if c == cat), 9999), cat, val)
             for cat, val in merits.items()]
        )
        cutoffs = {}
        for idx, (_, cat, merit_no) in enumerate(merit_ordered):
            pct = pcts_ordered[idx] if idx < len(pcts_ordered) else None
            cutoffs[cat] = {"merit_no": merit_no, "percentile": pct}

        records.append({
            "year":         year,
            "cap_round":    cap_round,
            "quota":        quota,
            "college_code": college_code,
            "college_name": college_name,
            "branch_code":  branch_code,
            "branch_name":  branch_name,
            "status":       status,
            "seat_type":    seat_type,
            "stage":        stage,
            "cutoffs":      cutoffs,
        })

    i = 0
    while i < n:
        raw  = lines[i]
        line = raw.strip()
        i   += 1

        if debug and i <= 300:
            print(f"DBG {i:4d} | {repr(raw[:130])}")

        if not line:
            continue

        # ── Skip known noise ──────────────────────────────────────
        if any(line.startswith(p) for p in (
            "Cut Off List", "Degree Courses", "State Common Entrance",
            "Government of Maharashtra", "Legends", "Figures in bracket",
            "Maharashtra State Seats", "All India Seats", "AI to AI",
        )):
            continue

        # ── College header: DDDD[D] - Name ───────────────────────
        cm = re.match(r"^(\d{4,5})\s*-\s*(.+)$", line)
        if cm and not re.match(r"^\d{9,10}", line):
            college_code = cm.group(1).strip()
            college_name = cm.group(2).strip()
            college_count += 1
            branch_code = branch_name = ""
            status = ""
            seat_type = "State Level"
            cat_starts = []
            continue

        # ── Branch header: DDDDDDDDD[D] - Name ───────────────────
        bm = re.match(r"^(\d{9,10})\s*-\s*(.+)$", line)
        if bm:
            branch_code = bm.group(1).strip()
            branch_name = bm.group(2).strip()
            status = ""
            seat_type = "State Level"
            cat_starts = []
            continue

        # ── Status line ───────────────────────────────────────────
        sm = re.search(r"Status:\s*(.+)", line)
        if sm:
            status = sm.group(1).strip()
            continue

        # ── Seat type header ──────────────────────────────────────
        st = detect_seat_type(line)
        if st:
            seat_type = st
            cat_starts = []  # reset table for this new seat section
            continue

        # ── Category header row ───────────────────────────────────
        cats = is_cat_header_line(raw)
        if cats:
            cat_starts = cats
            continue

        # ── Stage data row ────────────────────────────────────────
        if cat_starts:
            stage = detect_stage(raw)
            if stage:
                merit_line = raw
                pct_line   = ""

                # Peek ahead for pct line
                # Skip blank lines and I-Non continuation lines
                j = i
                while j < n:
                    peek = lines[j]
                    peek_stripped = peek.strip()

                    if not peek_stripped:
                        j += 1
                        continue

                    if is_inon_continuation(peek_stripped):
                        # This is "PWD" or "Defence" — it's part of I-Non pct line
                        pct_line = peek
                        j += 1
                        break

                    if "(" in peek and re.search(r"\(\d+\.\d+\)", peek):
                        pct_line = peek
                        j += 1
                        break

                    # Next non-empty line has no pct → no pct for this stage
                    break

                i = j  # advance past consumed pct line

                merits = parse_merit_line(merit_line, cat_starts)
                pcts   = parse_pct_line(pct_line)
                save_record(merits, pcts, stage)
                continue

            # ── I-Non continuation line without merit (PWD/Def label) ─
            if is_inon_continuation(raw):
                continue

    return records, college_count


# ─────────────────────────────────────────────────────────
# 5. OUTPUT METADATA
# ─────────────────────────────────────────────────────────

CATEGORY_LEGEND = {
    "how_to_read": (
        "Format: [Prefix][Category][Suffix]. "
        "Example: GOPENS = G(General) + OPEN(Open/General) + S(State Level). "
        "LSCS = L(Ladies) + SC(Scheduled Caste) + S(State Level)."
    ),
    "prefix":   {"G": "General seats (all genders)", "L": "Ladies-only seats"},
    "category": {
        "OPEN":   "Open / General category",
        "SC":     "Scheduled Caste",
        "ST":     "Scheduled Tribe",
        "VJ":     "Vimukta Jati (Denotified Tribes)",
        "NT1":    "Nomadic Tribe 1 (NT-A)",
        "NT2":    "Nomadic Tribe 2 (NT-B)",
        "NT3":    "Nomadic Tribe 3 (NT-C/D)",
        "OBC":    "Other Backward Class",
        "SEBC":   "Socially & Educationally Backward Class",
        "EWS":    "Economically Weaker Section",
        "PWD":    "Persons with Disability",
        "PWDR":   "PWD Reserved carry-forward",
        "DEF":    "Defence category",
        "DEFR":   "Defence Reserved carry-forward",
        "TFWS":   "Tuition Fee Waiver Scheme",
        "MI":     "Minority seats",
        "ORPHAN": "Orphan category",
    },
    "suffix":   {
        "S": "State Level seat",
        "H": "Home University seat",
        "O": "Other than Home University seat",
    },
    "stage": {
        "I":     "Stage I — first preference allotment",
        "II":    "Stage II — second preference",
        "III":   "Stage III — third preference",
        "VII":   "Stage VII — betterment round",
        "I-Non": "Stage I-Non — Non-PWD or Non-Defence sub-stage",
    },
}

RECORD_SCHEMA = {
    "year":         "Academic year (2025 = AY 2025-26, 2024 = AY 2024-25)",
    "cap_round":    "CAP1 | CAP2 | CAP3 | CAP4",
    "quota":        "MS = Maharashtra State (85% seats) | AI = All India (15% seats)",
    "college_code": "4 or 5-digit CET Cell college identifier",
    "college_name": "Full college name",
    "branch_code":  "9 or 10-digit branch code",
    "branch_name":  "Engineering branch / specialisation",
    "status":       "College type (Government / Aided / Un-Aided / Autonomous…)",
    "seat_type":    "Seat grouping (State Level / Home University / Other Than HU…)",
    "stage":        "Allotment stage: I | II | III | VII | I-Non",
    "cutoffs": {
        "_note":      "Only categories with seats allotted appear. Empty cells are omitted.",
        "merit_no":   "Closing merit number (MS: MH State merit | AI: All India JEE rank)",
        "percentile": "Closing score in brackets (MS: MHT-CET percentile | AI: JEE Main score)",
    },
}


def build_output(records, source_file, year, cap_round, quota, college_count):
    return {
        "metadata": {
            "source":          "Maharashtra State CET Cell — Government of Maharashtra",
            "portal":          "https://fe2025.mahacet.org",
            "source_file":     os.path.basename(source_file),
            "year":            year,
            "cap_round":       cap_round,
            "quota":           quota,
            "quota_note": (
                "Maharashtra State seats (85% of intake). "
                "merit_no = MH State Merit Number. percentile = MHT-CET PCM percentile."
                if quota == "MS" else
                "All India seats (15% of intake). "
                "merit_no = All India Merit Number. percentile = JEE Main score."
            ),
            "total_records":   len(records),
            "colleges_parsed": college_count,
            "record_schema":   RECORD_SCHEMA,
            "category_legend": CATEGORY_LEGEND,
            "pdf_url_templates": {
                "MS": "https://fe2025.mahacet.org/{year}/{year}ENGG_{round}_CutOff.pdf",
                "AI": "https://fe2025.mahacet.org/{year}/{year}ENGG_{round}_AI_CutOff.pdf",
            },
        },
        "data": records,
    }


# ─────────────────────────────────────────────────────────
# 6. CLI
# ─────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Convert MHT CET Engineering CAP cutoff PDF to JSON",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
EXAMPLES
  Auto-detect year/round/quota from filename:
    python3 mhtcet_pdf_to_json.py 2025ENGG_CAP1_CutOff.pdf

  All India seats:
    python3 mhtcet_pdf_to_json.py 2024ENGG_CAP2_AI_CutOff.pdf

  Override metadata:
    python3 mhtcet_pdf_to_json.py myfile.pdf --year 2025 --round CAP1 --quota MS

  Pretty JSON (readable):
    python3 mhtcet_pdf_to_json.py 2025ENGG_CAP1_CutOff.pdf --pretty

  Compact JSON (smallest file):
    python3 mhtcet_pdf_to_json.py 2025ENGG_CAP1_CutOff.pdf --compact

  Test first 20 records only:
    python3 mhtcet_pdf_to_json.py 2025ENGG_CAP1_CutOff.pdf --sample 20

  Debug - show raw PDF lines to diagnose issues:
    python3 mhtcet_pdf_to_json.py 2025ENGG_CAP1_CutOff.pdf --debug | head -100

QUOTA
  MS  Maharashtra State (85%%)  ->  merit = MH State Merit No., score = MHT-CET percentile
  AI  All India         (15%%)  ->  merit = All India Merit No., score = JEE Main score

CATEGORY CODES  [Prefix][Category][Suffix]
  Prefix   G=General  L=Ladies
  Category OPEN SC ST VJ NT1 NT2 NT3 OBC SEBC EWS PWD PWDR DEF DEFR TFWS ORPHAN MI
  Suffix   S=State  H=Home University  O=Other than Home University
  Example  GOPENS = General + Open/General category + State Level seats
        """,
    )
    ap.add_argument("pdf",                           help="Path to the MHT CET cutoff PDF")
    ap.add_argument("--year",    type=int,           help="Academic year e.g. 2025")
    ap.add_argument("--round",   dest="cap_round",   help="CAP1|CAP2|CAP3|CAP4")
    ap.add_argument("--quota",   choices=["MS","AI"],help="MS (Maharashtra) or AI (All India)")
    ap.add_argument("--out",                         help="Output JSON path (default: <pdf>.json)")
    ap.add_argument("--pretty",  action="store_true",help="4-space indent, human-readable")
    ap.add_argument("--compact", action="store_true",help="No whitespace, smallest file")
    ap.add_argument("--sample",  type=int, metavar="N", default=0,
                    help="Output only first N records (for quick testing)")
    ap.add_argument("--debug",   action="store_true",
                    help="Print raw PDF text lines (helps diagnose 0-record issues)")
    args = ap.parse_args()

    if not os.path.isfile(args.pdf):
        print(f"ERROR: File not found: {args.pdf}", file=sys.stderr)
        sys.exit(1)

    auto_year, auto_round, auto_quota = detect_from_filename(args.pdf)
    year      = args.year      or auto_year      or 0
    cap_round = args.cap_round or auto_round     or "UNKNOWN"
    quota     = args.quota     or auto_quota     or "MS"

    if not (args.year or auto_year):
        print("WARNING: year not detected — use --year 2025", file=sys.stderr)
    if not (args.cap_round or auto_round):
        print("WARNING: round not detected — use --round CAP1", file=sys.stderr)

    print(f"Settings  ->  year={year}  round={cap_round}  quota={quota}")

    out_path = args.out or (os.path.splitext(args.pdf)[0] + ".json")

    raw, method = extract_pdf_text(args.pdf)

    if args.debug:
        print(f"\n=== DEBUG: first 300 raw lines from {method} ===")
        for idx, ln in enumerate(raw.split("\n")[:300]):
            print(f"{idx+1:4d} | {repr(ln[:130])}")
        print("=== END DEBUG ===\n")

    print("\nParsing...", flush=True)
    records, college_count = parse_cutoff_text(raw, year, cap_round, quota)
    print(f"  Colleges : {college_count}")
    print(f"  Records  : {len(records):,}")

    if len(records) == 0:
        print("\nWARNING: 0 records parsed.")
        print("Run with --debug to inspect the raw PDF text:")
        print(f"  python3 {sys.argv[0]} {args.pdf} --debug | head -80")

    if args.sample > 0:
        records = records[:args.sample]
        print(f"  (sample — first {args.sample} only)")

    output = build_output(records, args.pdf, year, cap_round, quota, college_count)

    indent     = 4 if args.pretty else (None if args.compact else 2)
    separators = (",", ":") if args.compact else None
    json_str   = json.dumps(output, indent=indent, separators=separators, ensure_ascii=False)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(json_str)

    kb = os.path.getsize(out_path) / 1024
    print(f"\nDone! -> {out_path}")
    print(f"  Records   : {len(records):,}")
    print(f"  File size : {kb:,.1f} KB")

    if records:
        r = records[0]
        print("\n--- Sample record ---")
        print(f"  College   : [{r['college_code']}] {r['college_name']}")
        print(f"  Branch    : [{r['branch_code']}] {r['branch_name']}")
        print(f"  Seat type : {r['seat_type']}")
        print(f"  Stage     : {r['stage']}")
        for cat, val in list(r["cutoffs"].items())[:6]:
            pct = f"{val['percentile']}" if val["percentile"] is not None else "N/A"
            print(f"  {cat:14s}: merit={val['merit_no']:>7,}  score={pct}")
        rest = len(r["cutoffs"]) - 6
        if rest > 0:
            print(f"  ... +{rest} more categories")
        print("---------------------")


if __name__ == "__main__":
    main()