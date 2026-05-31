#!/usr/bin/env python3
"""
MHT CET Engineering CAP Cutoff PDF -> JSON Converter
=====================================================
Handles the pdftotext -layout horizontal table format:

  Stage   GOPENS    GSCS     GSTS   ...
    I     37591     50510    94334  ...
         (88.96)   (92.33)  (99.49) ...

Usage:
    python3 mhtcet_pdf_to_json.py <input.pdf> [options]

Examples:
    python3 mhtcet_pdf_to_json.py 2025ENGG_CAP1_CutOff.pdf
    python3 mhtcet_pdf_to_json.py 2024ENGG_CAP2_AI_CutOff.pdf
    python3 mhtcet_pdf_to_json.py myfile.pdf --year 2025 --round CAP1 --quota MS
    python3 mhtcet_pdf_to_json.py 2025ENGG_CAP1_CutOff.pdf --pretty
    python3 mhtcet_pdf_to_json.py 2025ENGG_CAP1_CutOff.pdf --sample 20
    python3 mhtcet_pdf_to_json.py 2025ENGG_CAP1_CutOff.pdf --debug   <- show raw lines

Requirements (install ONE):
    sudo apt install poppler-utils   (Linux  - RECOMMENDED, already on Ubuntu)
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
    """pdftotext -layout keeps columns on the same line (wide format)."""
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
    """pdftotext without -layout (simpler, one-token-per-line style)."""
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
    print("  sudo apt install poppler-utils  (Linux — recommended)")
    print("  brew install poppler            (macOS)")
    print("  pip install pypdf")
    print("  pip install pdfplumber")
    sys.exit(1)


# ─────────────────────────────────────────────────────────
# 2. AUTO-DETECT METADATA FROM FILENAME
# ─────────────────────────────────────────────────────────

def detect_from_filename(path):
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
# 3. PARSING HELPERS
# ─────────────────────────────────────────────────────────

# Valid category code:  1-4 uppercase letters/digits, optionally ending in S/H/O
# e.g. GOPENS, GSCS, PWDOPENS, DEFROBCS, EWS, TFWS, MI, ORPHAN
_VALID_CAT = re.compile(r"^[A-Z]{1,10}\d{0,2}[A-Z]?$")

# A bracket-wrapped percentile like (88.9600679) or (7.1701606)
_PCT_TOKEN = re.compile(r"^\((\d+\.\d+)\)$")

# Tokens that look like merit numbers: pure integers 1-9 digits
_MERIT_TOKEN = re.compile(r"^\d{1,9}$")

# Stage token: Roman numeral
_STAGE_RE = re.compile(r"^(I{1,3}|IV|V|VI|VII)$")

# Seat type patterns
_SEAT_PATTERNS = [
    "State Level",
    "Home University Seats Allotted to Home University",
    "Home University Seats Allotted to Other Than Home University",
    "Other Than Home University Seats Allotted to Other Than Home University",
    "Other Than Home University Seats Allotted to Home University",
    "Minority Seats",
]

# Known noise words that appear in the table area but are not data
_NOISE_WORDS = {
    "Stage", "Legends", "Government", "State", "Common", "Entrance",
    "Test", "Cell", "Cut", "Off", "List", "Maharashtra", "Minority",
    "Degree", "Courses", "Engineering", "Technology", "Master",
    "Integrated", "Years", "Year", "Admission", "Status", "Home",
    "University", "Autonomous", "Institute", "Aided", "Un-Aided",
    "Un", "Other", "Than", "Allotted", "Candidates", "Non",
}

def is_cat_token(tok):
    """Return True if token looks like a category code (GOPENS, GSCS, EWS, etc.)"""
    if not _VALID_CAT.match(tok):
        return False
    # Must be all uppercase and not a common noise word
    if tok in _NOISE_WORDS:
        return False
    # Must have at least one letter
    if not any(c.isalpha() for c in tok):
        return False
    # At least 2 chars
    if len(tok) < 2:
        return False
    return True


def extract_pct_value(tok):
    """Return float from (88.96) style token, or None."""
    m = _PCT_TOKEN.match(tok)
    return float(m.group(1)) if m else None


def is_merit_token(tok):
    return bool(_MERIT_TOKEN.match(tok)) and len(tok) >= 1


def is_stage_token(tok):
    return bool(_STAGE_RE.match(tok))


def detect_seat_type(line):
    """Return seat type string if line starts with a known seat type keyword."""
    stripped = line.strip()
    for pat in _SEAT_PATTERNS:
        if stripped.startswith(pat):
            return stripped
    return None


# ─────────────────────────────────────────────────────────
# 4. CORE PARSER
# ─────────────────────────────────────────────────────────

def parse_cutoff_text(text, year, cap_round, quota, debug=False):
    """
    Parse the full extracted PDF text.

    pdftotext -layout produces wide lines like:
      Stage   GOPENS    GSCS     GSTS  ...
        I     37591     50510    94334 ...
             (88.96)   (92.33)  (99.49)...

    Strategy:
      - Detect category header line: line containing 3+ consecutive cat codes
      - Next non-empty line: merit numbers (split by whitespace)
      - Line after that: percentiles in (xx.xxx) format
      - Map cats[0]->merit[0]+pct[0], cats[1]->merit[1]+pct[1], etc.
      - Handle multiple stages (I, II, VII) and multiple seat types
    """
    records = []
    college_count = 0

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    n = len(lines)

    # Current context
    college_code = college_name = ""
    branch_code  = branch_name  = ""
    status       = ""
    seat_type    = "State Level"

    def save_record(cat_codes, merit_vals, pct_vals, stage):
        """Build cutoffs dict and append record."""
        if not cat_codes or not merit_vals or not branch_code:
            return
        cutoffs = {}
        for j, cat in enumerate(cat_codes):
            if j < len(merit_vals):
                pct = pct_vals[j] if j < len(pct_vals) else None
                cutoffs[cat] = {"merit_no": merit_vals[j], "percentile": pct}
        if cutoffs:
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

    def try_parse_cat_header(line):
        """
        If line contains 3+ consecutive category tokens, return the list.
        Handles both:
          "Stage  GOPENS  GSCS  GSTS ..."  (with Stage prefix)
          "GOPENS  GSCS  GSTS ..."
        """
        tokens = line.split()
        # Strip leading 'Stage' if present
        if tokens and tokens[0] == "Stage":
            tokens = tokens[1:]
        cat_tokens = [t for t in tokens if is_cat_token(t)]
        # Need at least 3 consecutive cat-looking tokens in the line
        # and they should dominate (at least half the non-empty tokens)
        if len(cat_tokens) >= 3 and len(cat_tokens) >= len(tokens) * 0.5:
            return cat_tokens
        return None

    def try_parse_merit_line(line, n_cats):
        """
        Extract merit numbers from a data row.
        The stage marker (I, II, VII) is in the first column, then n_cats numbers.
        Returns (stage, [merit_ints]) or (None, []) if not a data line.
        """
        tokens = line.split()
        if not tokens:
            return None, []

        stage = None
        start = 0

        # First token may be stage marker
        if is_stage_token(tokens[0]):
            stage = tokens[0]
            start = 1
        # Or "I-Non" / "I-Non" split across tokens
        elif tokens[0] == "I" and len(tokens) > 1 and tokens[1].startswith("Non"):
            stage = "I-Non"
            start = 2

        merit_tokens = tokens[start:]
        merits = []
        for t in merit_tokens:
            # Strip any stuck-on parenthesized percentile like "37591(88.96)"
            clean = re.sub(r"\(.*\)", "", t).strip()
            if _MERIT_TOKEN.match(clean):
                merits.append(int(clean))

        if merits and len(merits) >= max(1, n_cats // 2):
            return stage, merits
        return None, []

    def try_parse_pct_line(line, n_cats):
        """
        Extract percentile values from a line of (xx.xxx)(yy.yyy)... tokens.
        Returns list of floats, or [] if not a percentile line.
        """
        # The line may look like: (88.96)(92.33)(99.49)...
        # or tokens separated by spaces: (88.9600679) (92.3332294) ...
        pct_values = re.findall(r"\((\d+\.\d+)\)", line)
        if len(pct_values) >= max(1, n_cats // 2):
            return [float(p) for p in pct_values]
        return []

    i = 0
    while i < n:
        raw  = lines[i]
        line = raw.strip()
        i   += 1

        if debug and i <= 200:
            print(f"DBG {i:4d} | {repr(raw[:120])}")

        if not line:
            continue

        # ── Noise / header lines ──────────────────────────────────
        if any(line.startswith(p) for p in (
            "Cut Off List", "Degree Courses", "State Common Entrance",
            "Government of Maharashtra", "Legends", "Figures in bracket",
            "Maharashtra State Seats",
        )):
            continue

        # ── College header:  DDDDD - Name ────────────────────────
        cm = re.match(r"^(\d{5})\s*-\s*(.+)$", line)
        if cm and not re.match(r"^\d{10}", line):
            college_code = cm.group(1).strip()
            college_name = cm.group(2).strip()
            college_count += 1
            branch_code = branch_name = ""
            status = ""
            seat_type = "State Level"
            continue

        # ── Branch header:  DDDDDDDDDD - Name ────────────────────
        bm = re.match(r"^(\d{10})\s*-\s*(.+)$", line)
        if bm:
            branch_code = bm.group(1).strip()
            branch_name = bm.group(2).strip()
            status = ""
            seat_type = "State Level"
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
            continue

        # ── Category header row ───────────────────────────────────
        cat_codes = try_parse_cat_header(raw)  # use raw to preserve spacing info
        if cat_codes:
            # Look ahead for data rows
            # Scan next lines for (stage+merits) and (percentiles)
            j = i  # i already advanced past current line

            while j < n:
                next_raw  = lines[j]
                next_line = next_raw.strip()
                j += 1

                if not next_line:
                    continue

                # New section starts -> stop
                if (re.match(r"^\d{5}\s*-", next_line) or
                    re.match(r"^\d{10}\s*-", next_line) or
                    detect_seat_type(next_line) or
                    try_parse_cat_header(next_raw)):
                    j -= 1   # back up
                    break

                # Check if this is a merit/stage row
                stage, merits = try_parse_merit_line(next_raw, len(cat_codes))

                if merits:
                    # Look ahead for percentile row
                    pcts = []
                    if j < n:
                        pct_raw  = lines[j]
                        pct_line = pct_raw.strip()
                        candidate_pcts = try_parse_pct_line(pct_raw, len(cat_codes))
                        if candidate_pcts:
                            pcts = candidate_pcts
                            j += 1  # consume percentile line

                    # Use detected stage or default to "I"
                    effective_stage = stage if stage else "I"
                    save_record(cat_codes, merits, pcts, effective_stage)
                    continue

                # If we hit a completely different kind of line, stop
                # (avoid consuming lines from next branch)
                if re.match(r"^\d{5}\s*-", next_line):
                    j -= 1
                    break

            i = j  # advance main pointer past all consumed lines
            continue

    return records, college_count


# ─────────────────────────────────────────────────────────
# 5. OUTPUT METADATA
# ─────────────────────────────────────────────────────────

CATEGORY_LEGEND = {
    "how_to_read": (
        "Format: [Prefix][Category][Suffix]. "
        "Example: GOPENS = G(General) + OPEN(Open category) + S(State Level). "
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
        "I-Non": "I (Non-PWD) or I (Non-Defence) sub-stage",
    },
}

RECORD_SCHEMA = {
    "year":         "Academic year (2025 = AY 2025-26, 2024 = AY 2024-25)",
    "cap_round":    "CAP1 | CAP2 | CAP3 | CAP4",
    "quota":        "MS = Maharashtra State (85% seats) | AI = All India (15% seats)",
    "college_code": "5-digit CET Cell college identifier",
    "college_name": "Full college name",
    "branch_code":  "10-digit branch code (first 5 = college code)",
    "branch_name":  "Engineering branch / specialisation",
    "status":       "College type (Government / Aided / Un-Aided / Autonomous…)",
    "seat_type":    "Seat grouping (State Level / Home University / Other Than HU…)",
    "stage":        "Allotment stage: I | II | III | VII | I-Non",
    "cutoffs": {
        "_note":      "Only categories with seats allotted appear here.",
        "merit_no":   "Closing State General Merit Number (lower = better rank)",
        "percentile": "Closing MHT-CET PCM percentile (MS) or JEE Main score (AI)",
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
                "Maharashtra State seats (85% of intake). Cutoff = MHT-CET PCM percentile."
                if quota == "MS" else
                "All India seats (15% of intake). Cutoff = JEE Main score/rank."
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

  Pretty JSON:
    python3 mhtcet_pdf_to_json.py 2025ENGG_CAP1_CutOff.pdf --pretty

  Compact JSON (smallest file):
    python3 mhtcet_pdf_to_json.py 2025ENGG_CAP1_CutOff.pdf --compact

  Test first 20 records:
    python3 mhtcet_pdf_to_json.py 2025ENGG_CAP1_CutOff.pdf --sample 20

  Debug (show raw lines):
    python3 mhtcet_pdf_to_json.py 2025ENGG_CAP1_CutOff.pdf --debug

QUOTA
  MS  Maharashtra State (85%%)  cutoff = MHT-CET percentile
  AI  All India         (15%%)  cutoff = JEE Main score

CATEGORY CODES  [Prefix][Category][Suffix]
  Prefix   G = General   L = Ladies
  Category OPEN SC ST VJ NT1 NT2 NT3 OBC SEBC EWS PWD DEF TFWS ORPHAN MI
  Suffix   S = State   H = Home University   O = Other than Home University
  Example  GOPENS = General + Open category + State Level seats
        """,
    )
    ap.add_argument("pdf",                           help="Path to the MHT CET cutoff PDF")
    ap.add_argument("--year",    type=int,           help="Academic year e.g. 2025 (auto-detected from filename)")
    ap.add_argument("--round",   dest="cap_round",   help="CAP1|CAP2|CAP3|CAP4 (auto-detected)")
    ap.add_argument("--quota",   choices=["MS","AI"],help="MS or AI (auto-detected)")
    ap.add_argument("--out",                         help="Output JSON path (default: <pdf>.json)")
    ap.add_argument("--pretty",  action="store_true",help="4-space indent — readable, larger file")
    ap.add_argument("--compact", action="store_true",help="No whitespace — smallest file")
    ap.add_argument("--sample",  type=int, metavar="N", default=0,
                    help="Output only first N records (for testing)")
    ap.add_argument("--debug",   action="store_true",
                    help="Print first 200 raw lines from PDF text (helps diagnose parsing issues)")
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
        print(f"\n=== DEBUG: first 200 lines from {method} ===")
        for idx, ln in enumerate(raw.split("\n")[:200]):
            print(f"{idx+1:4d} | {repr(ln[:130])}")
        print("\n=== END DEBUG ===\n")

    print("\nParsing...", flush=True)
    records, college_count = parse_cutoff_text(raw, year, cap_round, quota, debug=False)
    print(f"  Colleges : {college_count}")
    print(f"  Records  : {len(records):,}")

    if len(records) == 0:
        print("\nWARNING: 0 records parsed!")
        print("Run with --debug to see raw PDF lines and identify the format.")
        print("Example: python3 mhtcet_pdf_to_json.py yourfile.pdf --debug | head -50")

    if args.sample > 0:
        records = records[:args.sample]
        print(f"  (sample — first {args.sample} records only)")

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
            pct = f"{val['percentile']:.7f}" if val["percentile"] is not None else "N/A"
            print(f"  {cat:14s}: merit={val['merit_no']:>7,}  pct={pct}")
        rest = len(r["cutoffs"]) - 6
        if rest > 0:
            print(f"  ... +{rest} more categories")
        print("---------------------")


if __name__ == "__main__":
    main()