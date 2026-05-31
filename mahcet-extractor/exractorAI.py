"""
Extract 2025 Engineering CAP Round-I AI Cut-Off data from PDF into JSON.

Strategy
--------
- pdftotext -layout preserves column alignment (all rows are ~190 chars wide).
- Fixed slices are used for Sr.No, Merit, Choice Code, and the tail columns.
- The institute+course region (cols 45-152) is parsed by splitting on 3+
  consecutive spaces, which reliably separates the institute name from the
  (right-aligned) course name regardless of 1-2 char positional variance.
- Continuation lines are classified by their indent:
    indent >= 150  →  Merit Exam  (JEE / NEET / MHT-CET)
    100 <= indent < 150  →  Course Name continuation

Usage
-----
  python extract_cutoff.py
  python extract_cutoff.py --input path/to/file.pdf --output result.json
"""

import re
import json
import argparse
import subprocess
from pathlib import Path


# ── Patterns ───────────────────────────────────────────────────────────────── #
ROW_START = re.compile(r"^\s{0,5}[\d,]{1,6}\s{2,}\d{4,}")   # sr_no  +  merit_no
MERIT_RE  = re.compile(r"(\d[\d,]*)\s*\(([0-9.]+)\)")         # "15312 (86.6844102)"
INST_RE   = re.compile(r"^(\d{5})\s*-\s*(.*)")                # "01101 - Name…"
EXAM_RE   = re.compile(r"\b(JEE|NEET|MHT-CET)\b")
ATYPE_RE  = re.compile(r"\bAI to AI\b")
STYPE_RE  = re.compile(r"\b(AI|OP|SC|ST|VJ|NT1|NT2|NT3|OBC|EWS|PWD|TFWS)\b")

SKIP_RE = re.compile(
    r"Government of Maharashtra|State Common Entrance|Cut Off List for|"
    r"Engineering and Technology|^\s*Sr\.\s*No|All India\s*$|Choice Code|"
    r"Institute Name|Course Name|Merit Exam|Seat Type|Cut Off Indicates|"
    r"Page \d+ of \d+|Merit\s*$|^\s*$"
)


def leading_spaces(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def extract_raw_text(pdf_path: str) -> str:
    """Use pdftotext -layout to preserve fixed-width column alignment."""
    result = subprocess.run(
        ["pdftotext", "-layout", pdf_path, "-"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def split_inst_course(region: str):
    """
    Given the institute+course region (cols 45-152), return (inst_code,
    inst_name, course_name).  Splitting on 3+ spaces separates the
    left-aligned institute name from the right-aligned course name.
    """
    im = INST_RE.match(region)
    if not im:
        return "", region.strip(), ""

    inst_code = im.group(1)
    tail      = im.group(2)                      # everything after "DDDDD - "

    # Split on 3+ consecutive spaces (gap between inst name and course name)
    parts = re.split(r"\s{3,}", tail.strip(), maxsplit=1)
    inst_name   = parts[0].strip() if parts          else ""
    course_name = parts[1].strip() if len(parts) > 1 else ""

    return inst_code, inst_name, course_name


def parse_records(raw_text: str) -> list:
    records = []
    cur     = None

    for line in raw_text.splitlines():

        # ── skip header / footer / blank ────────────────────────────────── #
        if SKIP_RE.search(line):
            continue

        indent = leading_spaces(line)

        # ── new data record ──────────────────────────────────────────────── #
        if ROW_START.match(line):
            if cur:
                records.append(cur)

            padded = line.ljust(195)

            # Sr. No
            sr_raw = padded[0:9].strip()
            sr_no  = int(re.sub(r"[^\d]", "", sr_raw)) if sr_raw else 0

            # Merit number + score
            mm       = MERIT_RE.search(padded[9:30])
            merit_no = int(mm.group(1).replace(",", "")) if mm else 0
            score    = float(mm.group(2))               if mm else 0.0

            # Choice code (10 digits)
            choice_code = padded[31:41].strip()

            # Institute code, institute name, course name
            inst_code, inst_name, course_name = split_inst_course(padded[45:153])

            # Tail: Merit Exam / Allot Type / Seat Type (cols 153+)
            tail = padded[153:].strip()
            em         = EXAM_RE.search(tail)
            merit_exam = em.group(1) if em else ""
            allot_type = "AI to AI" if ATYPE_RE.search(tail) else ""
            tail_clean = ATYPE_RE.sub("", EXAM_RE.sub("", tail)).strip()
            sm         = STYPE_RE.search(tail_clean)
            seat_type  = sm.group(1) if sm else ""

            cur = {
                "sr_no":       sr_no,
                "merit_no":    merit_no,
                "score":       score,
                "choice_code": choice_code,
                "inst_code":   inst_code,
                "inst_name":   inst_name,
                "course_name": course_name,
                "merit_exam":  merit_exam,
                "allot_type":  allot_type,
                "seat_type":   seat_type,
            }

        # ── continuation line ────────────────────────────────────────────── #
        elif cur is not None:
            text = line.strip()
            if not text:
                continue

            if indent >= 150:
                # Exam continuation  (JEE / NEET / MHT-CET)
                em = EXAM_RE.search(text)
                if em and not cur["merit_exam"]:
                    cur["merit_exam"] = em.group(1)

            elif 100 <= indent < 150:
                # Course name continuation (wraps to next line)
                cur["course_name"] = (cur["course_name"] + " " + text).strip()

    if cur:
        records.append(cur)

    return records


def build_output(records: list, pdf_path: str) -> dict:
    return {
        "source":    Path(pdf_path).name,
        "title": (
            "Cut Off List for All India Seats of CAP Round - I "
            "for Admission to First Year of Four Year Full Time Degree "
            "Courses in Engineering and Technology for the Academic Year 2025-26"
        ),
        "state":         "Maharashtra",
        "authority":     "State Common Entrance Test Cell",
        "total_records": len(records),
        "records":       records,
    }


def main():
    parser = argparse.ArgumentParser(description="Extract CAP Cut-Off PDF -> JSON")
    parser.add_argument(
        "--input",
        default="/mnt/user-data/uploads/1778886122444_2025ENGG_CAP1_AI_CutOff.pdf",
        help="Path to the input PDF file",
    )
    parser.add_argument(
        "--output",
        default="/mnt/user-data/outputs/cutoff_data.json",
        help="Path for the output JSON file",
    )
    args = parser.parse_args()

    print(f"[1/3] Extracting text from: {args.input}")
    raw = extract_raw_text(args.input)

    print("[2/3] Parsing records ...")
    records = parse_records(raw)
    print(f"      -> {len(records):,} records parsed")

    print("[3/3] Writing JSON ...")
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    output = build_output(records, args.input)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"      Saved -> {args.output}")

    # ── QA preview ────────────────────────────────────────────────────────── #
    print("\n--- First 5 records ---")
    for r in records[:5]:
        print(json.dumps(r, indent=2, ensure_ascii=False))

    missing_course = sum(1 for r in records if not r["course_name"])
    missing_exam   = sum(1 for r in records if not r["merit_exam"])
    print(f"\nQA  missing course_name : {missing_course}")
    print(f"QA  missing merit_exam  : {missing_exam}")
    print(f"QA  total records       : {len(records):,}")


if __name__ == "__main__":
    main()