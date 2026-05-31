"""
IGDTUW Cutoff PDF → JSON Parser
Supports the standard IGDTUW round-wise cutoff PDF format.

FORMAT:
  - Rows = Category codes (GNGND, GNCWD, GNPDD, SCGND, ...)
  - Columns = Branch codes (CSE-AI, CSE, ECE-AI, ECE, IT, AIML, MAE, DMAM, MAC, B.Arch)
  - Dash '-' = explicit null value
  - Round annotations like "(VI)" appear on a SEPARATE y-row below their rank number
  - Page 1 = Delhi Region,  Page 2 = Outside Delhi Region

Two rendering quirks this parser handles:
  1. For some categories (page 2), data words appear BEFORE the category label in y.
  2. Round annotations for category row N appear between row N and row N+1 in y —
     they must NOT bleed into the next category's data. We detect the exact y-start
     of each category's data to achieve clean separation.

Usage:
    python igdtuw_cutoff_parser.py <input.pdf> [output.json]
"""

import sys
import re
import json
import pdfplumber


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Sequential-assignment: skip column if token_x > col_x + SKIP_THRESHOLD
SKIP_THRESHOLD = 100

CATEGORY_RE = re.compile(r'^(GN|SC|ST|OB|EW)(GN|CW|PD)[DO]$|^SG$|^KM$')
BRANCH_RE   = re.compile(r'^(CSE-AI|CSE|ECE-AI|ECE|IT|AIML|MAE|DMAM|MAC|B\.Arch)$')


# ---------------------------------------------------------------------------
# Data word validation
# ---------------------------------------------------------------------------

def is_data_word(text):
    """True for dashes, round annotations, and integer strings."""
    t = text.strip()
    if t == '-':
        return True
    if t.startswith('(') and t.endswith(')'):
        return True
    return bool(re.match(r'^\d[\d,]*$', t))


# ---------------------------------------------------------------------------
# Cell value helpers
# ---------------------------------------------------------------------------

def build_value(num_str, round_str):
    """Combine a rank string + optional round string into a JSON-friendly value."""
    if num_str is None:
        return None
    try:
        rank = int(num_str.replace(',', ''))
    except ValueError:
        return None
    if round_str:
        inner = round_str
        if inner.startswith('(') and inner.endswith(')'):
            inner = inner[1:-1]
        return {"rank": rank, "round": inner}
    return rank


# ---------------------------------------------------------------------------
# Column detection
# ---------------------------------------------------------------------------

def detect_columns(rows_by_y, first_cat_y):
    """
    Collect branch column positions from all header rows (y < first_cat_y).
    Returns [(name, x0), ...] sorted by x0.
    """
    seen = {}
    for y, words in rows_by_y.items():
        if y >= first_cat_y:
            continue
        for w in words:
            name = w['text'].strip()
            if BRANCH_RE.match(name) and name not in seen:
                seen[name] = w['x0']

    cols = sorted(seen.items(), key=lambda c: c[1])
    # Expand abbreviation
    return [('B.Arch (Paper 2)' if n == 'B.Arch' else n, x) for n, x in cols]


# ---------------------------------------------------------------------------
# Category detection
# ---------------------------------------------------------------------------

def detect_categories(rows_by_y, header_y):
    """Find all category labels (x<160) below the header."""
    cats = []
    for y in sorted(rows_by_y.keys()):
        if y <= header_y:
            continue
        for w in rows_by_y[y]:
            if w['x0'] < 160 and CATEGORY_RE.match(w['text'].strip()):
                cats.append((w['text'].strip(), y))
                break
    return cats


# ---------------------------------------------------------------------------
# Per-category y-range computation
# ---------------------------------------------------------------------------

def compute_category_y_start(rows_by_y, cat_y, prev_boundary):
    """
    Determine where a category's data actually begins.

    On page 1, data appears at the same y as the category label.
    On page 2, data appears up to ~20 px BEFORE the label.

    We look for number/dash words strictly BEFORE cat_y in the window
    [prev_boundary+1, cat_y-1].  If found, the earliest such y is the start.
    Otherwise we use cat_y itself.
    """
    scan_lo = prev_boundary + 1
    scan_hi = cat_y - 1

    min_y = None
    for y in sorted(rows_by_y.keys()):
        if y < scan_lo or y > scan_hi:
            continue
        for w in rows_by_y[y]:
            t = w['text'].strip()
            if w['x0'] > 155 and (t == '-' or re.match(r'^\d[\d,]*$', t)):
                if min_y is None or y < min_y:
                    min_y = y
                break     # one hit per y-row is enough

    return min_y if min_y is not None else cat_y


# ---------------------------------------------------------------------------
# Sequential column assignment
# ---------------------------------------------------------------------------

def assign_to_columns(data_words, columns):
    """
    Greedy left-to-right sequential assignment.

    Words starting with '(' are round annotations → attached to the
    previously assigned column (no column slot consumed).
    Dashes and integers consume the next available column slot.
    A column is "skipped" if the token's x > col_x + SKIP_THRESHOLD.

    Returns {col_name: parsed_value}.
    """
    col_num   = {name: None for name, _ in columns}
    col_round = {name: None for name, _ in columns}
    col_queue = list(columns)
    last_col  = None

    for text, tx in data_words:

        if text.startswith('('):
            # Round annotation: attach to last assigned column
            if last_col and col_round[last_col] is None:
                inner = text
                if inner.startswith('(') and inner.endswith(')'):
                    inner = inner[1:-1]
                col_round[last_col] = inner
            continue

        # Skip columns too far to the left of this token
        while col_queue and tx > col_queue[0][1] + SKIP_THRESHOLD:
            col_queue.pop(0)

        if not col_queue:
            continue

        col_name, _ = col_queue.pop(0)
        last_col = col_name

        if text != '-':
            col_num[col_name] = text     # integer string

    return {name: build_value(col_num[name], col_round[name]) for name, _ in columns}


# ---------------------------------------------------------------------------
# Page parser
# ---------------------------------------------------------------------------

def parse_page(page):
    """
    Parse one IGDTUW cutoff page.
    Returns (columns, {cat_name: {branch_name: value}}).
    """
    words = page.extract_words(x_tolerance=3, y_tolerance=3)

    rows_by_y = {}
    for w in words:
        y = round(w['top'] / 5) * 5
        rows_by_y.setdefault(y, []).append(w)

    # ---- first category label y ----
    first_cat_y = None
    for y in sorted(rows_by_y.keys()):
        for w in rows_by_y[y]:
            if w['x0'] < 160 and CATEGORY_RE.match(w['text'].strip()):
                first_cat_y = y
                break
        if first_cat_y:
            break

    if not first_cat_y:
        return [], {}

    # ---- header y (row containing "Category") ----
    header_y = 0
    for y in sorted(rows_by_y.keys()):
        if y >= first_cat_y:
            break
        if any(w['text'] == 'Category' for w in rows_by_y[y]):
            header_y = y

    columns   = detect_columns(rows_by_y, first_cat_y)
    categories = detect_categories(rows_by_y, header_y)

    if not columns or not categories:
        return columns, {}

    # ---- compute precise y-start for each category ----
    # prev_boundary: the label y of the previous category (categories don't share data)
    y_starts = []
    prev_boundary = header_y
    for cat_name, cat_y in categories:
        y_start = compute_category_y_start(rows_by_y, cat_y, prev_boundary)
        y_starts.append(y_start)
        prev_boundary = cat_y     # next category cannot reach into this label y

    # y-end = just before the next category's data starts; cap at label+30
    # (round annotations never extend >30 px below their category label)
    y_ends = []
    for i, (cat_name, cat_y) in enumerate(categories):
        next_y_start = y_starts[i + 1] if i + 1 < len(categories) else cat_y + 55
        y_ends.append(min(next_y_start - 1, cat_y + 30))

    # ---- parse each category ----
    result = {}
    for i, (cat_name, cat_y) in enumerate(categories):
        y_start = y_starts[i]
        y_end   = y_ends[i]

        # Collect valid data words in [y_start, y_end] at x > 155
        data_words = []
        for y, row_words in rows_by_y.items():
            if y < y_start or y > y_end:
                continue
            for w in row_words:
                t = w['text'].strip()
                if w['x0'] > 155 and is_data_word(t):
                    data_words.append((t, w['x0']))

        # Sort by x
        data_words.sort(key=lambda d: d[1])

        result[cat_name] = assign_to_columns(data_words, columns)

    return columns, result


# ---------------------------------------------------------------------------
# Pivot: category-major → branch-major list
# ---------------------------------------------------------------------------

def pivot_to_branches(columns, cat_data):
    """Convert {cat: {branch: val}} to [{branch: ..., cat1: v1, ...}, ...]."""
    return [
        {"branch": bname, **{cat: cat_data[cat].get(bname) for cat in cat_data}}
        for bname, _ in columns
    ]


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

def parse_igdtuw_cutoff_pdf(pdf_path):
    """Parse a 2-page IGDTUW cutoff PDF → structured dict."""
    result = {
        "source_file": pdf_path,
        "university": "IGDTUW",
        "delhi_region": [],
        "outside_delhi_region": [],
    }

    with pdfplumber.open(pdf_path) as pdf:
        if len(pdf.pages) >= 1:
            cols, cats = parse_page(pdf.pages[0])
            result["delhi_region"] = pivot_to_branches(cols, cats)

        if len(pdf.pages) >= 2:
            cols, cats = parse_page(pdf.pages[1])
            result["outside_delhi_region"] = pivot_to_branches(cols, cats)

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print("Usage: python igdtuw_cutoff_parser.py <input.pdf> [output.json]")
        sys.exit(1)

    pdf_path = sys.argv[1]
    out_path = (sys.argv[2] if len(sys.argv) > 2
                else pdf_path.rsplit('.', 1)[0] + '.json')

    print(f"Parsing : {pdf_path}")
    data = parse_igdtuw_cutoff_pdf(pdf_path)

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Output  : {out_path}")
    print(f"\nSummary:")
    print(f"  Delhi branches      : {len(data['delhi_region'])}")
    print(f"  Outside Delhi       : {len(data['outside_delhi_region'])}")
    if data['delhi_region']:
        cats = [k for k in data['delhi_region'][0] if k != 'branch']
        print(f"  Delhi categories    : {cats}")


if __name__ == '__main__':
    main()