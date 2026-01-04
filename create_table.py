import json
import re
from pathlib import Path
import pandas as pd

# ---------------- Config ----------------
JSON_IN  = Path("compliance_table.json")
CSV_OUT  = Path("hierarchical_outline6.csv")
XLSX_OUT = Path("hierarchical_outline6.xlsx")

pd.set_option("display.max_colwidth", None)

# ================= Parsing helpers =================

# A broad set of bullet glyphs seen in PDF text extraction
# (added several extras + a private-use fallback \uF0B7 some tools emit)


# BULLET_CHARS = (
#     "\u2022"  # •
#     "\u25CF"  # ●
#     "\u25E6"  # ◦
#     "\u25AA"  # ▪
#     "\u25AB"  # ▫
#     "\u2043"  # ⁃
#     "\u2219"  # ∙
#     "\u00B7"  # ·
#     "\u2023"  # ‣
#     "\u204C"  # ⁌
#     "\u204D"  # ⁍
#     "\u2218"  # ∘
#     "\u25C9"  # ◉
#     "\u25CB"  # ○
#     "\u25A0"  # ■
#     "\u25A1"  # □
#     "\u25B6"  # ▶
#     "\u25B8"  # ▸
#     "\uF0B7" 
#     "\u0640"
#     "\u2043"
#     "\u25aa"
#     "\u25cf"
#     "\u25cb"
#     "\u25a0"
#      "\u06d4"
#       "\u066d" # (private-use; common from some PDF extractors)
# )
BULLET_CHARS = (
    r"\-"      # Standard Dash (Escaped)
    "\u2022"   # • Standard Bullet
    "\u25CF"   # ● Black Circle
    "\u25E6"   # ◦ White Bullet
    "\u25AA"   # ▪ Black Small Square
    "\u25AB"   # ▫ White Small Square
    "\u2043"   # ⁃ Hyphen Bullet
    "\u2219"   # ∙ Bullet Operator
    "\u00B7"   # · Middle Dot
    "\u2023"   # ‣ Triangular Bullet
    "\u204C"   # ⁌ Black Left Bullet
    "\u204D"   # ⁍ Black Right Bullet
    "\u2218"   # ∘ Ring Operator
    "\u25C9"   # ◉ Fisheye
    "\u25CB"   # ○ White Circle
    "\u25A0"   # ■ Black Square
    "\u25A1"   # □ White Square
    "\u25B6"   # ▶ Black Right-Pointing Triangle
    "\u25B8"   # ▸ Black Right-Pointing Small Triangle
    "\uF0B7"   # (Private Use Area)
    "\u066d"   # ٭ Arabic Star
    "\u06d4"   # ۔ Arabic Full Stop
    "\u06dd"   # ۝ End of Ayah
    "\u06de"   # ۞ Rub El Hizb
)

# Bullets / enumerators at the **start** of a line
BULLET_RE = re.compile(
    rf"""^
        (?P<indent>\s*)
        (?:
            [\-\*{BULLET_CHARS}]
          | \d+[\.\)]               # 1. or 1)
          | [A-Za-z][\.\)]          # a. or A)
        )
        \s+
        (?P<text>.*\S.*)
    $""",
    re.VERBOSE
)

# Numeric chains like "1.2.3 Some text"
CHAIN_RE = re.compile(r"^\s*((?:\d+\.)+\d+)\s+(.*\S.*)$")

# Inline bullets:
#  (1) primary: bullet with surrounding spaces (nice case)
INLINE_SPLIT_SPACED = re.compile(rf"\s+([{BULLET_CHARS}])\s+")
#  (2) fallback: bullet anywhere (no spaces required)
INLINE_SPLIT_NOSPACE = re.compile(rf"[{BULLET_CHARS}]")

def normalize_indent(s: str, tabsize: int = 4) -> int:
    """Return indentation width after expanding tabs."""
    return len(s.expandtabs(tabsize))

def is_section_header(line: str) -> bool:
    """Headers like '3.0 Title' or '3.0) Title'"""
    return bool(re.match(r"^\s*\d+\.0(?:[.)])?\s+\S", line))

def parse_chain(line: str):
    """Return (parts_tuple_or_None, rest_of_text) for numeric chains like '1.2.3 Text'."""
    m = CHAIN_RE.match(line)
    if not m:
        return None, line
    chain_str, rest = m.group(1), m.group(2)
    parts = tuple(int(p) for p in chain_str.split("."))
    # Ignore X.0 section headers
    if len(parts) == 2 and parts[1] == 0:
        return None, line
    return parts, rest

# def split_inline_bullets(line: str):
#     """
#     Split a single line that contains multiple inline bullets into segments.
#     Returns a list of tuples tagged as ("prefix" | "bullet", text).
#     Works with bullets with/without surrounding spaces.
#     """
#     s = line.strip()
#     if not s:
#         return []

#     # Try nice spaced bullets first
#     parts = INLINE_SPLIT_SPACED.split(s)
#     if len(parts) > 1:
#         out = []
#         prefix = parts[0].strip()
#         if prefix:
#             out.append(("prefix", prefix))
#         for i in range(1, len(parts), 2):
#             if i + 1 >= len(parts):
#                 break
#             seg = parts[i + 1].strip()
#             if seg:
#                 out.append(("bullet", seg))
#         return out

#     # Fallback: split by any bullet char regardless of spacing
#     raw = INLINE_SPLIT_NOSPACE.split(s)
#     if len(raw) > 1:
#         out = []
#         prefix = raw[0].strip()
#         if prefix:
#             out.append(("prefix", prefix))
#         for seg in raw[1:]:
#             seg = seg.strip()
#             if seg:
#                 out.append(("bullet", seg))
#         return out

#     return []
INLINE_SPLIT_REGEX = re.compile(rf"(?:[\s\.]+) ([{BULLET_CHARS}]) \s*", re.VERBOSE)

def split_inline_bullets(line: str):
    """
    Splits a line like: "Text one. -Text two •Text three"
    """
    s = line.strip()
    if not s:
        return []

    # Use regex split, keeping the delimiters (the bullets)
    parts = INLINE_SPLIT_REGEX.split(s)
    
    # Parts will look like: ['Text one', '-', 'Text two', '•', 'Text three']
    if len(parts) == 1:
        return [] # No split found

    out = []
    
    # The first chunk is always the "Prefix" (text before the first bullet)
    if parts[0].strip():
        out.append(("prefix", parts[0].strip()))
    
    # Iterate pairs: (Bullet, Text)
    # The regex split results in [text, bullet, text, bullet, text...]
    for i in range(1, len(parts), 2):
        if i + 1 < len(parts):
            bullet_char = parts[i]
            segment_text = parts[i+1].strip()
            if segment_text:
                out.append(("bullet", segment_text))
                
    return out

def extract_hierarchical_items(chunk_text: str):
    """
    Extract header + sub-items from the chunk text.

    Rules:
      - Headers 'X.0' are captured as header_text (main row).
      - Lines that START with bullets / numeric chains create NEW items.
      - Plain lines:
          * if they contain **inline bullets**, split into (optional prefix + bullet segments),
            where bullets become separate items.
          * else if items exist -> append as continuation to the last item.
          * else -> start the first item with this line.
    Returns: (header_text, items) where items = list of (indent, level_hint, text)
    """
    lines = [ln.rstrip("\r\n") for ln in str(chunk_text).splitlines()]
    header_text = None
    items = []

    def append_to_last(text: str):
        if not items:
            items.append((0, None, text.strip()))
            return
        ind, hint, prev = items[-1]
        joiner = "" if prev.endswith("-") else " "
        items[-1] = (ind, hint, (prev + joiner + text).strip())

    for ln in lines:
        if not ln.strip():
            continue

        # Header (e.g., "4.1. Marketing Requirements")
        if is_section_header(ln):
            m = re.match(r"^\s*\d+\.0(?:[.)])?\s+(.+)$", ln)
            if m:
                header_text = m.group(1).strip()
            continue

        # Numeric chain at start
        chain_parts, rest = parse_chain(ln)
        if chain_parts:
            first_num = str(chain_parts[0])
            indent_guess = normalize_indent(ln[:ln.find(first_num)])
            items.append((indent_guess, chain_parts, rest.strip()))
            continue

        # Bullet at start
        m = BULLET_RE.match(ln)
        if m:
            indent = normalize_indent(m.group("indent"))
            text = m.group("text").strip()
            items.append((indent, None, text))
            continue

        # Plain line → check for inline bullets
        chunks = split_inline_bullets(ln)
        if chunks:
            if chunks[0][0] == "prefix":
                # prefix becomes continuation if something is open; otherwise starts first item
                if items:
                    append_to_last(chunks[0][1])
                else:
                    items.append((0, None, chunks[0][1]))
                chunks = chunks[1:]
            for _, seg in chunks:
                items.append((0, None, seg))
        else:
            # No bullets → treat as paragraph continuation or start
            append_to_last(ln.strip())

    return header_text, items

def build_hierarchy(base_num: str, header_text: str, items, min_indent_step: int = 2):
    """
    Build rows for a chunk.
      - If header_text exists, add a level-0 main row (outline = base_num).
      - Subpoints are numbered base_num.1, base_num.2, ... (depth from indent or chain).
      - For numeric chains we use **depth only** (we don't reuse original numbers).
    """
    rows = []

    if header_text:
        rows.append({
            "outline_number": base_num,
            "level": 0,
            "text": header_text
        })

    if not items:
        return rows

    counters = []
    indent_levels = [items[0][0]]

    def ensure_level(level: int):
        while len(counters) < level:
            counters.append(0)
        while len(counters) > level:
            counters.pop()

    for indent, level_hint, text in items:
        if level_hint:
            level = len(level_hint)
            ensure_level(level)
            counters[-1] += 1
        else:
            cur = indent
            if cur > indent_levels[-1] + (min_indent_step - 1):
                indent_levels.append(cur)
            else:
                while len(indent_levels) > 1 and cur < indent_levels[-1] - (min_indent_step - 1):
                    indent_levels.pop()
                indent_levels[-1] = cur
            level = len(indent_levels)
            ensure_level(level)
            counters[-1] += 1

        suffix = ".".join(str(c) for c in counters[:level])
        rows.append({
            "outline_number": f"{base_num}.{suffix}",
            "level": level,
            "text": text
        })

    return rows

def numeric_sort_key(s):
    """Turn '3.10.2' into (3,10,2) for natural sorting."""
    if s is None:
        return ()
    parts = str(s).split(".")
    return tuple(int(p) if p.isdigit() else 0 for p in parts)

# ================= Load & filter =================

with open(JSON_IN, "r", encoding="utf-8") as f:
    raw = json.load(f)

def is_compliant(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"yes", "y", "true", "1"}
    return False

compliant_records = [r for r in raw if is_compliant(r.get("compliant"))]

# ================= Build table =================

rows = []
chunk_index = 0

for record in compliant_records:
    chunk_index += 1
    base_num = str(chunk_index)

    chunk_id = record.get("chunk_id", f"Chunk_{chunk_index:03d}")
    chunk_text = record.get("original_chunk", "") or record.get("chunk", "") or record.get("text", "")
    requirement_id = record.get("requirement_id", "")
    compliant = record.get("compliant", "")
    confidence = record.get("confidence", None)
    mandatory_optional = record.get("mandatory_optional", "")

    header_text, items = extract_hierarchical_items(chunk_text)

    if not header_text and not items:
        rows.append({
            "chunk_id": chunk_id,
            "requirement_id": requirement_id,
            "outline_number": base_num,
            "level": 0,
            "text": str(chunk_text).strip(),
            "compliant": compliant,
            "mandatory_optional": mandatory_optional,
            "confidence": confidence
        })
        continue

    for row in build_hierarchy(base_num, header_text, items):
        row.update({
            "chunk_id": chunk_id,
            "requirement_id": requirement_id,
            "compliant": compliant,
            "mandatory_optional": mandatory_optional,
            "confidence": confidence
        })
        rows.append(row)

# ================= Save =================

df = pd.DataFrame(rows, columns=[
    "chunk_id",
    "requirement_id",
    "outline_number",
    "level",
    "text",
    "compliant",
    "mandatory_optional",
    "confidence"
])

if not df.empty:
    df["sort_key"] = df["outline_number"].apply(numeric_sort_key)
    df = df.sort_values(by=["sort_key"], kind="mergesort").drop(columns=["sort_key"])

df.to_csv(CSV_OUT, index=False, encoding="utf-8-sig")
print(f"✅ Saved CSV -> {CSV_OUT}")

excel_saved = False
for engine in ("xlsxwriter", "openpyxl"):
    try:
        with pd.ExcelWriter(XLSX_OUT, engine=engine) as writer:
            df.to_excel(writer, index=False, sheet_name="Hierarchical Outline")
            if engine == "xlsxwriter":
                wb = writer.book
                ws = writer.sheets["Hierarchical Outline"]
                wrap = wb.add_format({"text_wrap": True, "valign": "top"})
                ws.set_column("A:A", 15, wrap)
                ws.set_column("B:B", 15, wrap)
                ws.set_column("C:C", 15, wrap)
                ws.set_column("D:D", 8,  wrap)
                ws.set_column("E:E", 100, wrap)
                ws.set_column("F:F", 12, wrap)
                ws.set_column("G:G", 18, wrap)
                ws.set_column("H:H", 12, wrap)
        excel_saved = True
        print(f"✅ Saved Excel -> {XLSX_OUT}") 
        break
    except Exception:
        continue


if not excel_saved:
    print("ℹ️ Excel save skipped (install openpyxl or xlsxwriter)")


print(f"\n📊 Total JSON rows: {len(raw)}")
print(f"✅ Compliant chunks: {len(compliant_records)}")
print(f"📋 Hierarchical rows created: {len(df)}")
