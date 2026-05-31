"""
NSUT B.Tech/B.Arch Admissions Cutoff PDF → JSON Parser
Supports the standard NSUT round-wise cutoff PDF format.

Usage:
    python nsut_cutoff_parser.py <input.pdf> [output.json]
"""

import sys
import re
import json
import pdfplumber


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# If a token appears more than this many px to the RIGHT of a column centre,
# the column is assumed empty and we skip past it.
SKIP_THRESHOLD = 35

COURSE_CODE_RE = re.compile(
    r'^(CSAI|CSE|CSDS|IT(?:NS)?|MAC|ECE|EVDT|EE|ICE|ME(?:EV\*\*)?|'
    r'BT|CSDA\*|CIOT\*|ECAM\*|CE\*\*|GI\*\*|B\.Arch\.\*\*)$'
)


# ---------------------------------------------------------------------------
# Cell parsing
# ---------------------------------------------------------------------------

def parse_cell_value(text):
    """Plain rank → int; annotated rank → {"rank": int, "round": str}; else None."""
    if not text:
        return None
    text = text.strip()
    m = re.match(r'^(\d+)\s*\(([^)]+(?:\([^)]*\))?[^)]*)\)$', text)
    if m:
        return {"rank": int(m.group(1)), "round": m.group(2).strip()}
    try:
        return int(text.replace(',', ''))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Token extraction
# ---------------------------------------------------------------------------

def words_to_tokens(words):
    """
    Convert word list to tokens. A word starting with '(' is appended to
    the previous token (round annotation for a CW value, e.g. "(VI)").
    Returns [(text, x0), ...] sorted by x0.
    """
    tokens = []
    for w in sorted(words, key=lambda w: w['x0']):
        t = w['text'].strip()
        if not t:
            continue
        if t.startswith('(') and tokens:
            prev_text, prev_x = tokens[-1]
            tokens[-1] = (prev_text + ' ' + t, prev_x)
        else:
            tokens.append((t, w['x0']))
    return tokens


# ---------------------------------------------------------------------------
# Sequential column assignment
# ---------------------------------------------------------------------------

def assign_tokens_to_columns(tokens, columns):
    """
    Greedy left-to-right sequential assignment.

    For each token: skip any leading column whose x + SKIP_THRESHOLD < token_x
    (that column is assumed empty), then assign to the first remaining column.

    Returns {col_name: raw_text_or_None}.
    """
    result = {col_name: None for col_name, _ in columns}
    col_queue = list(columns)          # (col_name, col_x)

    for text, token_x in tokens:
        while col_queue and token_x > col_queue[0][1] + SKIP_THRESHOLD:
            col_queue.pop(0)
        if not col_queue:
            break
        col_name, _ = col_queue.pop(0)
        result[col_name] = text

    return result


# ---------------------------------------------------------------------------
# Header detection
# ---------------------------------------------------------------------------

def detect_columns(rows_by_y):
    """Find the header row (contains GNGND/GNGNO) and return column list."""
    for y in sorted(rows_by_y.keys()):
        row_words = rows_by_y[y]
        if any(re.match(r'^GNG[ND]', w['text']) for w in row_words):
            seen, cols = set(), []
            for w in sorted(row_words, key=lambda w: w['x0']):
                name = w['text'].strip()
                if re.match(r'^[A-Z]{2,5}$', name) and name not in seen:
                    cols.append((name, w['x0']))
                    seen.add(name)
            return y, cols
    return None, []


# ---------------------------------------------------------------------------
# Legend parser
# ---------------------------------------------------------------------------

def parse_legend(rows_by_y):
    """Extract course-code → full-name legend. Returns {code: name}."""
    legend = {}
    in_legend = False

    for y in sorted(rows_by_y.keys()):
        row = sorted(rows_by_y[y], key=lambda w: w['x0'])
        texts = [w['text'] for w in row]

        if 'Course' in texts and 'Code' in texts:
            in_legend = True
            continue
        if not in_legend:
            continue

        i = 0
        while i < len(row):
            code_w = row[i]
            code = code_w['text'].strip()

            # Code: short, starts uppercase, followed by a name word
            if (len(code) <= 10 and re.match(r'^[A-Z]', code) and
                    code not in ('Course', 'Code', 'Name', 'with') and
                    i + 1 < len(row)):
                # Collect name until next code-like word at an anchor x
                name_parts = []
                j = i + 1
                while j < len(row):
                    nw = row[j]
                    nt = nw['text'].strip()
                    is_next_code = (
                        len(nt) <= 10 and
                        re.match(r'^[A-Z]', nt) and
                        nt not in ('and', 'of', 'in', 'the') and
                        any(abs(nw['x0'] - ax) < 25 for ax in [41, 273, 511])
                    )
                    if is_next_code:
                        break
                    name_parts.append(nt)
                    j += 1

                if name_parts:
                    legend[code] = ' '.join(name_parts).strip()
                i = j if j > i + 1 else i + 1
            else:
                i += 1

    return legend


# ---------------------------------------------------------------------------
# Page parser
# ---------------------------------------------------------------------------

def parse_page(page):
    """
    Parse one NSUT cutoff page.
    Returns (columns, branch_rows, legend_dict).
    """
    words = page.extract_words(x_tolerance=3, y_tolerance=3)

    rows_by_y = {}
    for w in words:
        y = round(w['top'] / 5) * 5
        rows_by_y.setdefault(y, []).append(w)

    header_y, columns = detect_columns(rows_by_y)
    if not columns:
        return [], [], {}

    branch_rows = []
    barch_pending = None   # B.Arch.** accumulator
    barch_label_y = None   # y of "B.Arch.**" label row

    for y in sorted(rows_by_y.keys()):
        if y <= header_y:
            continue

        row_words = sorted(rows_by_y[y], key=lambda w: w['x0'])
        if not row_words:
            continue

        first = row_words[0]['text'].strip()

        # ── Stop at footnote / legend section ──────────────────────────────
        if first.startswith('*') or first.startswith('**West'):
            break
        if first == 'Course' and any(w['text'] == 'Code' for w in row_words):
            break

        # ── B.Arch continuation: "(Paper-2)" label row ────────────────────
        if first == '(Paper-2)' and barch_pending is not None:
            barch_pending['course_code'] = 'B.Arch.** (Paper-2)'
            branch_rows.append(barch_pending)
            barch_pending = None
            barch_label_y = None
            continue

        # ── B.Arch data row (numbers-only row right after the label) ──────
        if (barch_pending is not None and
                barch_label_y is not None and
                y == barch_label_y + 5 and
                first[0].isdigit()):
            tokens = words_to_tokens(row_words)   # all words are data
            extra = assign_tokens_to_columns(tokens, columns)
            for col_name, raw in extra.items():
                if barch_pending.get(col_name) is None and raw is not None:
                    barch_pending[col_name] = parse_cell_value(raw)
            continue

        # ── Regular branch row ─────────────────────────────────────────────
        if COURSE_CODE_RE.match(first):
            data_words = [w for w in row_words if w['x0'] > 60]
            tokens = words_to_tokens(data_words)
            assignments = assign_tokens_to_columns(tokens, columns)

            entry = {'course_code': first}
            for col_name, _ in columns:
                entry[col_name] = parse_cell_value(assignments.get(col_name))

            if first == 'B.Arch.**':
                barch_pending = entry
                barch_label_y = y
            else:
                branch_rows.append(entry)

    # Flush any incomplete B.Arch (shouldn't happen, but be safe)
    if barch_pending is not None:
        branch_rows.append(barch_pending)

    legend = parse_legend(rows_by_y)
    return columns, branch_rows, legend


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

def parse_nsut_cutoff_pdf(pdf_path):
    """Parse a 2-page NSUT cutoff PDF → structured dict."""
    result = {
        "source_file": pdf_path,
        "university": "NSUT",
        "course_legend": {},
        "delhi_region": [],
        "outside_delhi_region": [],
    }

    with pdfplumber.open(pdf_path) as pdf:
        if len(pdf.pages) >= 1:
            _, rows, legend = parse_page(pdf.pages[0])
            result["delhi_region"] = rows
            result["course_legend"].update(legend)

        if len(pdf.pages) >= 2:
            _, rows, legend = parse_page(pdf.pages[1])
            result["outside_delhi_region"] = rows
            result["course_legend"].update(legend)

    for region in ("delhi_region", "outside_delhi_region"):
        for row in result[region]:
            code = row.get("course_code", "")
            row["course_name"] = result["course_legend"].get(code, "")

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print("Usage: python nsut_cutoff_parser.py <input.pdf> [output.json]")
        sys.exit(1)

    pdf_path = sys.argv[1]
    out_path = (sys.argv[2] if len(sys.argv) > 2
                else pdf_path.rsplit('.', 1)[0] + '.json')

    print(f"Parsing : {pdf_path}")
    data = parse_nsut_cutoff_pdf(pdf_path)

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Output  : {out_path}")
    print(f"\nSummary:")
    print(f"  Legend entries      : {len(data['course_legend'])}")
    print(f"  Delhi branches      : {len(data['delhi_region'])}")
    print(f"  Outside Delhi       : {len(data['outside_delhi_region'])}")


if __name__ == '__main__':
    main()