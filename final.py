# """
# Arabic RFP → Compliance Table Pipeline (NO OCR)
# ------------------------------------------------
# ✓ نفس معالج PDF العربي الإنتاجي اللي اشتغل معك
# ✓ Detect Arabic PDF
# ✓ Extract text using best method
# ✓ Fix reversed Arabic text (حروف + ترتيب كلمات)
# ✓ Chunk text (مع فلترة ذكية)
# ✓ Send chunks to LLaMA for compliance extraction
# """

# import os
# import re
# import json
# from typing import List, Tuple
# from pathlib import Path

# import pdfplumber
# import fitz                     # PyMuPDF
# from PyPDF2 import PdfReader
# import ollama

# # ============================================================
# # CONFIGURATION
# # ============================================================

# DEBUG_MODE = True
# ARABIC_CHARS_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")
# LETTER_RE = re.compile(r"[\w\u0600-\u06FF]", re.UNICODE)

# # ============================================================
# # ARABIC TEXT FIXING - SAME AS PRODUCTION SCRIPT
# # ============================================================

# def has_arabic(text: str) -> bool:
#     """Check if text contains Arabic characters."""
#     return bool(ARABIC_CHARS_RE.search(text or ""))


# def fix_reversed_arabic(text: str) -> str:
#     """
#     Fix Arabic extracted from PDFs where:
#     - characters inside each word are reversed
#     - AND word order is reversed

#     Example:
#       'ةكلملما ةيبرعلا ةيدوعسلا' → 'المملكة العربية السعودية'
#     """
#     if not text or not has_arabic(text):
#         return text

#     fixed_lines = []

#     for line in text.split("\n"):
#         stripped = line.strip()
#         if not stripped:
#             fixed_lines.append("")
#             continue

#         if has_arabic(stripped):
#             # split into words
#             words = stripped.split()

#             # 1) reverse characters inside each Arabic word
#             reversed_letters = [
#                 w[::-1] if has_arabic(w) else w
#                 for w in words
#             ]

#             # 2) reverse the order of words in the sentence
#             corrected_order = list(reversed(reversed_letters))

#             fixed_lines.append(" ".join(corrected_order))

#         else:
#             fixed_lines.append(stripped)

#     return "\n".join(fixed_lines)


# # ============================================================
# # PDF FUNCTIONS
# # ============================================================

# def detect_arabic_pdf(pdf_path: str, sample_pages: int = 3) -> bool:
#     """Detect if PDF is primarily Arabic."""
#     try:
#         sample_text = ""
#         with pdfplumber.open(pdf_path) as pdf:
#             for page in pdf.pages[:sample_pages]:
#                 sample_text += page.extract_text() or ""
        
#         if not sample_text:
#             return False
        
#         arabic_count = len(ARABIC_CHARS_RE.findall(sample_text))
#         total_letters = len(LETTER_RE.findall(sample_text))
        
#         if total_letters == 0:
#             return arabic_count > 0
        
#         ratio = arabic_count / total_letters
#         is_arabic = ratio >= 0.15
        
#         if DEBUG_MODE:
#             print(f"📊 Language detection: {ratio:.1%} Arabic → {'Arabic PDF' if is_arabic else 'Non-Arabic PDF'}")
        
#         return is_arabic
        
#     except Exception as e:
#         if DEBUG_MODE:
#             print(f"⚠️ Detection error: {e}")
#         return False


# def extract_text_best(pdf_path: str) -> str:
#     """Extract text using best available method."""
#     methods = []
    
#     # Method 1: pdfplumber
#     try:
#         parts = []
#         with pdfplumber.open(pdf_path) as pdf:
#             for page in pdf.pages:
#                 text = page.extract_text()
#                 if text:
#                     parts.append(text.strip())
#         full = "\n\n".join(parts)
#         methods.append(("pdfplumber", full))
#     except Exception as e:
#         if DEBUG_MODE:
#             print(f"⚠️ pdfplumber failed: {e}")
    
#     # Method 2: PyMuPDF
#     try:
#         doc = fitz.open(pdf_path)
#         parts = []
#         for page in doc:
#             text = page.get_text("text")
#             if text:
#                 parts.append(text.strip())
#         doc.close()
#         full = "\n\n".join(parts)
#         methods.append(("PyMuPDF", full))
#     except Exception as e:
#         if DEBUG_MODE:
#             print(f"⚠️ PyMuPDF failed: {e}")
    
#     if not methods:
#         raise RuntimeError("All extraction methods failed!")
    
#     # Pick method with most content
#     scored = [(name, txt, len(LETTER_RE.findall(txt))) for name, txt in methods]
#     scored.sort(key=lambda x: x[2], reverse=True)
    
#     best_method, best_text, score = scored[0]
#     if DEBUG_MODE:
#         print(f"✅ Extraction method: {best_method} ({score:,} characters)")
    
#     return best_text


# def process_pdf(pdf_path: str) -> Tuple[str, bool]:
#     """
#     Main PDF processing pipeline.
#     Returns: (extracted_text, is_arabic)
#     """
#     print(f"\n{'='*70}")
#     print(f"📄 Processing: {Path(pdf_path).name}")
#     print(f"{'='*70}\n")
    
#     # Detect language
#     is_arabic = detect_arabic_pdf(pdf_path)
    
#     # Extract text
#     raw_text = extract_text_best(pdf_path)
    
#     if not raw_text.strip():
#         print("⚠️ No text extracted!")
#         return "", is_arabic
    
#     # Fix Arabic if needed
#     if is_arabic:
#         if DEBUG_MODE:
#             print("🔧 Fixing reversed Arabic text...")
#             sample_lines = [l for l in raw_text.split("\n") if has_arabic(l)][:2]
#             if sample_lines:
#                 print(f"\n   Before: {sample_lines[0][:60]}...")
#                 fixed_sample = fix_reversed_arabic(sample_lines[0])
#                 print(f"   After:  {fixed_sample[:60]}...\n")
        
#         fixed_text = fix_reversed_arabic(raw_text)
#         return fixed_text, True
    
#     return raw_text, False


# # ============================================================
# # TEXT PROCESSING
# # ============================================================

# def clean_text(text: str) -> str:
#     """Clean extracted text."""
#     if not text:
#         return ""
    
#     # Remove excessive dots
#     text = re.sub(r"\.{3,}", " ", text)
    
#     # Remove standalone page numbers
#     text = re.sub(r"(?m)^[\t ]*\d+[\t ]*$", "", text)
    
#     # Normalize line breaks
#     text = re.sub(r"\n{3,}", "\n\n", text)
    
#     # Normalize spaces
#     text = re.sub(r"[\t ]{2,}", " ", text)
    
#     # Remove zero-width chars
#     text = re.sub(r"[\u200b-\u200f\ufeff]", "", text)
    
#     return text.strip()


# def is_meaningful(text: str, min_words: int = 5) -> bool:
#     """Check if text is meaningful."""
#     if not text:
#         return False
    
#     words = re.split(r"\s+", text.strip())
#     words = [w for w in words if w]
    
#     if len(words) < min_words:
#         return False
    
#     letter_count = len(LETTER_RE.findall(text))
#     return letter_count >= min_words


# def create_chunks(pdf_path: str) -> List[dict]:
#     """Create chunks from PDF using the production Arabic processor."""
    
#     # Process PDF
#     text, is_arabic = process_pdf(pdf_path)
    
#     if not text:
#         return []
    
#     # Clean
#     text = clean_text(text)
    
#     # Split into paragraphs
#     paragraphs = text.split("\n\n")
#     paragraphs = [p.strip() for p in paragraphs if p.strip()]
    
#     if DEBUG_MODE:
#         print(f"📑 Found {len(paragraphs)} paragraphs")
    
#     # Create chunks
#     chunks = []
#     for para in paragraphs:
#         if is_meaningful(para):
#             chunks.append({
#                 "chunk_id": f"Chunk_{len(chunks)+1:03d}",
#                 "text": para,
#                 "word_count": len(para.split()),
#                 "is_arabic": is_arabic
#             })
    
#     print(f"✅ Created {len(chunks)} meaningful chunks")
    
#     if chunks:
#         total_words = sum(c["word_count"] for c in chunks)
#         print(f"📊 Total: {total_words:,} words, avg {total_words//len(chunks)} per chunk\n")
#     else:
#         print("⚠️ No meaningful chunks created – check extraction or thresholds.")
    
#     return chunks


# # ============================================================
# # JSON PARSING / NORMALIZATION FOR LLM
# # ============================================================
# import ast
# def _parse_json(text: str):
#     text = (text or "").strip()

#     # 1) Try direct JSON
#     try:
#         return json.loads(text)
#     except Exception:
#         pass

#     # 2) Try to grab the first {...} block
#     m = re.search(r"\{[\s\S]*\}", text)
#     if m:
#         candidate = m.group(0).strip()

#         # 2a) Try JSON again on the candidate
#         try:
#             return json.loads(candidate)
#         except Exception:
#             pass

#         # 2b) Try Python literal (handles single quotes, etc.)
#         try:
#             return ast.literal_eval(candidate)
#         except Exception:
#             pass

#     # 3) As last resort, try to build from simple key:value pairs
#     result = {}
#     for line in text.splitlines():
#         if ":" not in line:
#             continue
#         k, v = line.split(":", 1)
#         k = k.strip().strip('"').strip("'")
#         v = v.strip().strip(",").strip()
#         if k in ["compliant", "rephrased_requirement", "confidence"]:
#             result[k] = v

#     if result:
#         # try to cast confidence to float
#         if "confidence" in result:
#             try:
#                 result["confidence"] = float(result["confidence"])
#             except Exception:
#                 result["confidence"] = 0.5
#         return result

#     # 4) Default fallback
#     return {"compliant": "No", "rephrased_requirement": "default", "confidence": 0.2}


# def normalize_compliant(v):
#     if isinstance(v, bool): return "Yes" if v else "No"
#     if isinstance(v, str):  return "Yes" if v.lower() in ["yes","y","true","1","نعم","yes."] else "No"
#     if isinstance(v, (int,float)): return "Yes" if v!=0 else "No"
#     return "No"


# # ============================================================
# # LLM CALL (LLaMA via ollama)
# # ============================================================

# def ask_llm(text: str) -> dict:
#     prompt = f"""
# You are a Compliance Analyst at a Location Services firm (IT services Firm).
# Your task is to classify and extract mandatory technical requirements from the given RFP section.
# The text could be in English or Atabic you must be able to handle it .

# RFP section:
# ---
# {text}
# ---

# Follow these rules carefully:

# 1. Decide if this section contains any mandatory technical requirements.
# 2. Ignore commercial, legal, ownership, disclaimers, marketing, or descriptive text.
# 3. If relevant content exists, rephrase each requirement in 1–2 concise sentences suitable for a compliance table.
# 4. Regardless of whether you say "Yes" or "No", include a confidence score between 0.0 and 1.0 that reflects **your certainty in your decision**.
#    - Example: You can output `"compliant": "No", "confidence": 0.95"` if you're very confident it's irrelevant.
#    - Example: You can output `"compliant": "Yes", "confidence": 0.55"` if you’re unsure but think it’s relevant.
# 5. Output strictly in JSON with **no markdown, no explanations, no extra text**.
# 6. Your JSON must have exactly these fields:
#    - compliant
#    - rephrased_requirement
#    - confidence
# 7. Return EXACTLY ONE JSON object and nothing else.
#    Do not include multiple JSON objects. Do not add explanations, comments, or markdown.
# Examples:

# If no requirement is found:
# {{
#   "compliant": "No",
#   "rephrased_requirement": "",
#   "confidence": 0.94
# }}

# If somewhat relevant:
# {{
#   "compliant": "Yes",
#   "rephrased_requirement": "System may include integration with scheduling software.",
#   "confidence": 0.57
# }}

# If clearly relevant:
# {{
#   "compliant": "Yes",
#   "rephrased_requirement": "The system must support AI-driven space utilization analytics.",
#   "confidence": 0.93
# }}
# """


#     response = ollama.chat(
#         model="llama3.1",
#         messages=[{"role":"user","content":prompt}],
#         options={"temperature":0.0}
#     )

#     if isinstance(response, dict):
#         content = response["message"]["content"]
#     else:
#         content = str(response)

#     data = _parse_json(content)

#     return {
#         "compliant": normalize_compliant(data.get("compliant","No")),
#         "rephrased_requirement": data.get("rephrased_requirement",""),
#         "confidence": float(data.get("confidence",0.5)),
#     }


# # ============================================================
# # RUN CHUNKS → COMPLIANCE TABLE
# # ============================================================

# def process_chunks(chunks: List[dict]) -> List[dict]:
#     out = []

#     for ch in chunks:
#         r = ask_llm(ch["text"])
#         out.append({
#             "requirement_id": f"RFP_{len(out)+1:03d}",
#             "chunk_id": ch["chunk_id"],
#             "original_chunk": ch["text"],
#             "rephrased_requirement": r["rephrased_requirement"],
#             "compliant": r["compliant"],
#             "mandatory_optional": "",
#             "reference_evidence": "",
#             "notes": "",
#             "confidence": r["confidence"],
#         })

#     return out


# # ============================================================
# # MAIN
# # ============================================================

# if __name__ == "__main__":
#     # غيّر الاسم إذا ملفك اسمه مختلف
#     pdf_path = "كراسة الشروط والمواصفات.pdf"
#     output_json = "compliance_table.json"

#     print("\n" + "="*70)
#     print("🚀 ARABIC RFP → COMPLIANCE TABLE (NO OCR)")
#     print("="*70)

#     print("\n✂️ Creating chunks using production Arabic processor...")
#     chunks = create_chunks(pdf_path)
#     print(f"✔️ {len(chunks)} chunks created\n")

#     if not chunks:
#         print("⚠️ No chunks created – stop before calling LLM.")
#     else:
#         print("🤖 Running compliance extraction with LLaMA...")
#         results = process_chunks(chunks)

#         print(f"💾 Saving → {output_json}")
#         with open(output_json, "w", encoding="utf-8") as f:
#             json.dump(results, f, indent=2, ensure_ascii=False)

#         print("\n🎉 Done! You can now open compliance_table.json")


from typing import List
# لو final.py جوّا app، نقدر نستخدم models مباشرة
try:
    from .models import ComplianceRow
except ImportError:
    ComplianceRow = None  # لو شغّلت الملف stand-alone


import os
import re
import json
from typing import List, Tuple
from pathlib import Path

import pdfplumber
import fitz                     # PyMuPDF
from PyPDF2 import PdfReader
import ollama
import ast

# ============================================================
# CONFIGURATION
# ============================================================

DEBUG_MODE = True
ARABIC_CHARS_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")
LETTER_RE = re.compile(r"[\w\u0600-\u06FF]", re.UNICODE)

# How many raw LLM responses to print for debugging
DEBUG_LLM_SAMPLES = 3

# ============================================================
# ARABIC TEXT FIXING
# ============================================================

def has_arabic(text: str) -> bool:
    """Check if text contains Arabic characters."""
    return bool(ARABIC_CHARS_RE.search(text or ""))


def fix_reversed_arabic(text: str) -> str:
    """
    Fix Arabic extracted from PDFs where:
    - characters inside each word are reversed
    - AND word order is reversed

    Example:
      'ةكلملما ةيبرعلا ةيدوعسلا' → 'المملكة العربية السعودية'
    """
    if not text or not has_arabic(text):
        return text

    fixed_lines = []

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            fixed_lines.append("")
            continue

        if has_arabic(stripped):
            # split into words
            words = stripped.split()

            # 1) reverse characters inside each Arabic word
            reversed_letters = [
                w[::-1] if has_arabic(w) else w
                for w in words
            ]

            # 2) reverse the order of words in the sentence
            corrected_order = list(reversed(reversed_letters))

            fixed_lines.append(" ".join(corrected_order))
        else:
            fixed_lines.append(stripped)

    return "\n".join(fixed_lines)


# ============================================================
# PDF FUNCTIONS
# ============================================================

def detect_arabic_pdf(pdf_path: str, sample_pages: int = 3) -> bool:
    """Detect if PDF is primarily Arabic."""
    try:
        sample_text = ""
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages[:sample_pages]:
                sample_text += page.extract_text() or ""
        
        if not sample_text:
            return False
        
        arabic_count = len(ARABIC_CHARS_RE.findall(sample_text))
        total_letters = len(LETTER_RE.findall(sample_text))
        
        if total_letters == 0:
            return arabic_count > 0
        
        ratio = arabic_count / total_letters
        is_arabic = ratio >= 0.15
        
        if DEBUG_MODE:
            print(f"📊 Language detection: {ratio:.1%} Arabic → {'Arabic PDF' if is_arabic else 'Non-Arabic PDF'}")
        
        return is_arabic
        
    except Exception as e:
        if DEBUG_MODE:
            print(f"⚠️ Detection error: {e}")
        return False


def extract_text_best(pdf_path: str) -> str:
    """Extract text using best available method."""
    methods = []
    
    # Method 1: pdfplumber
    try:
        parts = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    parts.append(text.strip())
        full = "\n\n".join(parts)
        methods.append(("pdfplumber", full))
    except Exception as e:
        if DEBUG_MODE:
            print(f"⚠️ pdfplumber failed: {e}")
    
    # Method 2: PyMuPDF
    try:
        doc = fitz.open(pdf_path)
        parts = []
        for page in doc:
            text = page.get_text("text")
            if text:
                parts.append(text.strip())
        doc.close()
        full = "\n\n".join(parts)
        methods.append(("PyMuPDF", full))
    except Exception as e:
        if DEBUG_MODE:
            print(f"⚠️ PyMuPDF failed: {e}")
    
    if not methods:
        raise RuntimeError("All extraction methods failed!")
    
    # Pick method with most content
    scored = [(name, txt, len(LETTER_RE.findall(txt))) for name, txt in methods]
    scored.sort(key=lambda x: x[2], reverse=True)
    
    best_method, best_text, score = scored[0]
    if DEBUG_MODE:
        print(f"✅ Extraction method: {best_method} ({score:,} letters)")
    
    return best_text


def process_pdf(pdf_path: str) -> Tuple[str, bool]:
    """
    Main PDF processing pipeline.
    Returns: (extracted_text, is_arabic)
    """
    print(f"\n{'='*70}")
    print(f"📄 Processing: {Path(pdf_path).name}")
    print(f"{'='*70}\n")
    
    # Detect language
    is_arabic = detect_arabic_pdf(pdf_path)
    
    # Extract text
    raw_text = extract_text_best(pdf_path)
    
    if not raw_text.strip():
        print("⚠️ No text extracted!")
        return "", is_arabic
    
    # Fix Arabic if needed
    if is_arabic:
        if DEBUG_MODE:
            print("🔧 Fixing reversed Arabic text...")
            sample_lines = [l for l in raw_text.split("\n") if has_arabic(l)][:2]
            if sample_lines:
                print(f"\n   Before: {sample_lines[0][:60]}...")
                fixed_sample = fix_reversed_arabic(sample_lines[0])
                print(f"   After:  {fixed_sample[:60]}...\n")
        
        fixed_text = fix_reversed_arabic(raw_text)
        return fixed_text, True
    
    return raw_text, False


# ============================================================
# TEXT PROCESSING
# ============================================================

def clean_text(text: str) -> str:
    """Clean extracted text."""
    if not text:
        return ""
    
    # Remove excessive dots
    text = re.sub(r"\.{3,}", " ", text)
    
    # Remove standalone page numbers
    text = re.sub(r"(?m)^[\t ]*\d+[\t ]*$", "", text)
    
    # Normalize line breaks
    text = re.sub(r"\n{3,}", "\n\n", text)
    
    # Normalize spaces
    text = re.sub(r"[\t ]{2,}", " ", text)
    
    # Remove zero-width chars
    text = re.sub(r"[\u200b-\u200f\ufeff]", "", text)
    
    return text.strip()


def is_meaningful(text: str, min_words: int = 5) -> bool:
    """Check if text is meaningful."""
    if not text:
        return False
    
    words = re.split(r"\s+", text.strip())
    words = [w for w in words if w]
    
    if len(words) < min_words:
        return False
    
    letter_count = len(LETTER_RE.findall(text))
    return letter_count >= min_words


def create_chunks(pdf_path: str) -> List[dict]:
    """Create chunks from PDF using the production Arabic processor."""
    
    # Process PDF
    text, is_arabic = process_pdf(pdf_path)
    
    if not text:
        return []
    
    # Clean
    text = clean_text(text)
    
    # Split into paragraphs
    paragraphs = text.split("\n\n")
    paragraphs = [p.strip() for p in paragraphs if p.strip()]
    
    if DEBUG_MODE:
        print(f"📑 Found {len(paragraphs)} paragraphs")
    
    # Create chunks
    chunks = []
    for para in paragraphs:
        if is_meaningful(para):
            chunks.append({
                "chunk_id": f"Chunk_{len(chunks)+1:03d}",
                "text": para,
                "word_count": len(para.split()),
                "is_arabic": is_arabic
            })
    
    print(f"✅ Created {len(chunks)} meaningful chunks")
    
    if chunks:
        total_words = sum(c["word_count"] for c in chunks)
        print(f"📊 Total: {total_words:,} words, avg {total_words//len(chunks)} per chunk\n")
    else:
        print("⚠️ No meaningful chunks created – check extraction or thresholds.")
    
    return chunks


# ============================================================
# JSON PARSING / NORMALIZATION
# ============================================================

def normalize_compliant(v):
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, str):
        return "Yes" if v.strip().lower() in ["yes", "y", "true", "1", "نعم", "yes."] else "No"
    if isinstance(v, (int, float)):
        return "Yes" if v != 0 else "No"
    return "No"


def safe_parse_llm_json(text: str) -> dict:
    """
    Try to parse model output as JSON.
    We expect a *single* JSON object.
    """
    text = (text or "").strip()

    # 1) Try direct JSON
    try:
        return json.loads(text)
    except Exception:
        pass

    # 2) Try to grab the first {...} block
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        candidate = m.group(0).strip()

        # 2a) Try JSON again on the candidate
        try:
            return json.loads(candidate)
        except Exception:
            pass

        # 2b) Try Python literal (handles single quotes, etc.)
        try:
            return ast.literal_eval(candidate)
        except Exception:
            pass

    # 3) As last resort, try to build from simple key:value pairs
    result = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip().strip('"').strip("'")
        v = v.strip().strip(",").strip()
        if k in ["compliant", "rephrased_requirement", "confidence"]:
            result[k] = v

    if result:
        # try to cast confidence to float
        if "confidence" in result:
            try:
                result["confidence"] = float(result["confidence"])
            except Exception:
                result["confidence"] = 0.5
        return result

    # 4) Default fallback
    if DEBUG_MODE:
        print("⚠️ LLM output not parseable, using default 'No' result.")
    return {"compliant": "No", "rephrased_requirement": "", "confidence": 0.2}


# ============================================================
# LLM CALL (LLaMA via ollama)  — JSON MODE
# ============================================================

SYSTEM_PROMPT = """
You are a Compliance Analyst at an IT Location Services firm.
You read sections of an RFP (often in Arabic) and decide whether they contain
mandatory technical requirements relevant to IT / systems / solution design.

You must always answer with exactly one JSON object with the fields:
- compliant: "Yes" or "No"
- rephrased_requirement: short English summary (or empty string if No)
- confidence: float between 0.0 and 1.0

Ignore purely legal, commercial, introductory, or generic policy text.
Focus on technical / solution / system / architecture / performance / integration requirements.
"""

def ask_llm(text: str, idx: int = 0) -> dict:
    user_prompt = f"""
RFP section (may be Arabic or English):

---
{text}
---

Return ONLY a JSON object like:
{{
  "compliant": "Yes" or "No",
  "rephrased_requirement": "short requirement in English (or empty if No)",
  "confidence": 0.0-1.0
}}
"""

    response = ollama.chat(
        model="llama3.1",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ],
        options={
            "temperature": 0.0,
            "format": "json"
        }
    )

    # >>> FIX: extract JSON STRING directly
    content = response.message.content

    if DEBUG_MODE and idx < DEBUG_LLM_SAMPLES:
        print("── LLM RAW JSON ──")
        print(content)
        print("──────────────────\n")

    # Parse JSON
    try:
        data = json.loads(content)
    except:
        data = safe_parse_llm_json(content)

    compliant = normalize_compliant(data.get("compliant", "No"))
    rephrased = data.get("rephrased_requirement", "")
    confidence = float(data.get("confidence", 0.5))

    return {
        "compliant": compliant,
        "rephrased_requirement": rephrased,
        "confidence": confidence
    }


# ============================================================
# RUN CHUNKS → COMPLIANCE TABLE
# ============================================================

def process_chunks(chunks: List[dict]) -> List[dict]:
    out = []

    for i, ch in enumerate(chunks):
        r = ask_llm(ch["text"], idx=i)
        out.append({
            "requirement_id": f"RFP_{len(out)+1:03d}",
            "chunk_id": ch["chunk_id"],
            "original_chunk": ch["text"],
            "rephrased_requirement": r["rephrased_requirement"],
            "compliant": r["compliant"],
            "mandatory_optional": "",
            "reference_evidence": "",
            "notes": "",
            "confidence": r["confidence"],
        })

    return out

def run_arabic_pipeline(pdf_path: str):
    """
    High-level Arabic pipeline:
    - create_chunks
    - process_chunks (LLM)
    - يرجّع list من dicts جاهزة للتحويل لـ ComplianceRow
    """
    chunks = create_chunks(pdf_path)
    if not chunks:
        return []

    results = process_chunks(chunks)  # list[dict] with keys like:
    # requirement_id, chunk_id, original_chunk, rephrased_requirement, compliant, confidence, ...

    return results


# ============================================================
# MAIN
# ============================================================

# if __name__ == "__main__":
#     pdf_path = "SOW-MBZUAI-034.pdf"
#     output_json = "compliance_table.json"

#     print("\n" + "="*70)
#     print("🚀 ARABIC RFP → COMPLIANCE TABLE (NO OCR)")
#     print("="*70)

#     print("\n✂️ Creating chunks using production Arabic processor...")
#     chunks = create_chunks(pdf_path)
#     print(f"✔️ {len(chunks)} chunks created\n")

#     if not chunks:
#         print("⚠️ No chunks created – stop before calling LLM.")
#     else:
#         print("🤖 Running compliance extraction with LLaMA (JSON mode)...")
#         results = process_chunks(chunks)

#         print(f"💾 Saving → {output_json}")
#         with open(output_json, "w", encoding="utf-8") as f:
#             json.dump(results, f, indent=2, ensure_ascii=False)

#         print("\n🎉 Done! You can now open compliance_table.json")
