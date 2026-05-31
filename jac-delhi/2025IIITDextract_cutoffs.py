"""
Extract IIIT Delhi Round 5 cutoff data from PDF into structured JSON.

The PDF has two tables (Without Bonus / With Bonus), each laid out as
categories (rows) x branches (columns). Empty cells mean no cutoff was
released for that category-branch combination in this round.
"""

import json
import re
import sys
from pathlib import Path

import pdfplumber


# The 9 branch columns, in the exact order they appear in the PDF.
BRANCHES = ["CSAM", "CSAI", "CSB", "CSD", "CSEcon", "CSE", "CSSS", "EVE", "ECE"]

# All category codes we expect to see as row labels.
CATEGORIES = {
    "OBCWD", "OBGND", "OBGNO",
    "EWCWD", "EWGND", "EWGNO", "EWPDD",
    "GNKM", "GNCWD", "GNGND", "GNGNO", "GNPDD",
    "SCGND", "SCGNO",
    "STGND", "STGNO", "STPDD",
}


def parse_pdf(pdf_path: str) -> dict:
    """Parse the cutoff PDF and return a nested dict.

    Structure:
        {
          "Without Bonus": {category: {branch: rank, ...}, ...},
          "With Bonus":    {category: {branch: rank, ...}, ...}
        }

    Branches without a cutoff for a given category are omitted.
    """
    result = {"Without Bonus": {}, "With Bonus": {}}
    current_section = None  # flips when we see the "With Bonus" header

    with pdfplumber.open(pdf_path) as pdf:
        # The document is a single page, but we iterate to be safe.
        for page in pdf.pages:
            # extract_tables() returns one big table here. Each visual row
            # is split into ~30 sub-columns because pdfplumber sees the
            # internal cell-padding lines, but the real content is sparse.
            # Strategy: find the category label in the row, then collect
            # every other non-empty cell as the 9 branch ranks in order.
            for table in page.extract_tables():
                for row in table:
                    # Normalize: strip Nones and whitespace.
                    cells = [(c or "").strip() for c in row]
                    nonempty = [c for c in cells if c]
                    if not nonempty:
                        continue

                    # Section banner rows: a single cell with the section name.
                    joined = " ".join(nonempty)
                    if "Without Bonus" in joined and len(nonempty) <= 2:
                        current_section = "Without Bonus"
                        continue
                    if "With Bonus" in joined and len(nonempty) <= 2 and "Without" not in joined:
                        current_section = "With Bonus"
                        continue

                    # Find the category label (a known code) in this row.
                    label = next((c for c in nonempty if c in CATEGORIES), None)
                    if label is None:
                        continue
                    if current_section is None:
                        current_section = "Without Bonus"

                    # The 30-column row layout places the 9 branch cutoffs
                    # at fixed sub-column indices. Confirmed by inspecting
                    # all rows: each branch's value sits at one of these
                    # positions (the other column of the pair is always
                    # empty for that branch).
                    branch_positions = [3, 7, 9, 13, 15, 19, 21, 25, 27]
                    branch_ranks = {}
                    for branch, pos in zip(BRANCHES, branch_positions):
                        if pos >= len(cells):
                            continue
                        val = cells[pos]
                        if val and re.fullmatch(r"\d+", val):
                            branch_ranks[branch] = int(val)

                    if branch_ranks:
                        result[current_section][label] = branch_ranks

    return result


def main():
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads/IIIT-D_round5.pdf"
    out_path = sys.argv[2] if len(sys.argv) > 2 else "/mnt/user-data/outputs/iiitd_round5_cutoffs.json"

    data = parse_pdf(pdf_path)

    # Wrap with a bit of metadata so the JSON is self-describing.
    output = {
        "source": Path(pdf_path).name,
        "institute": "IIIT Delhi",
        "round": 5,
        "year": 2025,
        "branches": BRANCHES,
        "cutoffs": data,
    }

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    # Quick sanity summary
    for section, cats in data.items():
        total = sum(len(v) for v in cats.values())
        print(f"{section}: {len(cats)} categories, {total} cutoff entries")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()