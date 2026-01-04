
import os
import re
import json
import subprocess
from typing import List

import pdfplumber
import ollama
from PyPDF2 import PdfReader   # NEW: for scanned detection


# ---------- Scanned Detection + OCR ----------

def is_scanned_pdf(pdf_path: str, min_text_threshold: int = 50) -> bool:
    """
    Roughly determine if a PDF is scanned (image-based) by checking how much extractable text it has.
    If total text length < min_text_threshold => likely scanned.
    """
    try:
        reader = PdfReader(pdf_path)
        total_text_len = 0

        for page in reader.pages:
            txt = page.extract_text() or ""
            total_text_len += len(txt.strip())
            if total_text_len >= min_text_threshold:
                # Enough text found -> not scanned
                return False

        # Very little or no text found -> probably scanned
        return True

    except Exception as e:
        print(f"⚠️ Could not inspect PDF for text, assuming NOT scanned. Error: {e}")
        return False


def ocr_pdf_if_needed(pdf_path: str) -> str:
    """
    If the PDF appears to be scanned (image-only), run OCR using ocrmypdf and return
    the path to the OCR-processed PDF. Otherwise, return original path.
    """
    if not is_scanned_pdf(pdf_path):
        print("✅ Detected searchable PDF (no OCR needed).")
        return pdf_path

    print("🔎 Detected scanned / image-only PDF. Running OCR with ocrmypdf...")

    base, ext = os.path.splitext(pdf_path)
    ocr_output = f"{base}_ocr{ext}"

    try:
        # --force-ocr: even if it thinks there is text, ensure OCR
        # --deskew: straighten skewed pages
        subprocess.run(
            ["ocrmypdf", "--force-ocr", "--deskew", pdf_path, ocr_output],
            check=True
        )
        print(f"✅ OCR complete. Using OCR version: {ocr_output}")
        return ocr_output

    except FileNotFoundError:
        print(
            "❌ ocrmypdf is not installed or not found in PATH.\n"
            "   Install it with: pip install ocrmypdf\n"
            "   Using original PDF without OCR."
        )
        return pdf_path

    except subprocess.CalledProcessError as e:
        print(f"❌ OCR process failed: {e}\nUsing original PDF without OCR.")
        return pdf_path


# ---------- Helper Functions ----------

def read_pdf(file_path: str) -> str:
    """Read text from a PDF file."""
    text = ""
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text.rstrip() + "\n\n"
    return text


def clean_text(text: str) -> str:
    """Clean text and normalize formatting."""
    text = re.sub(r"\.{3,}", " ", text)
    text = re.sub(r"(?m)^[\t ]*\d+[\t ]*$", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[\t ]{2,}", " ", text)
    return text.strip()


def split_into_sections(text: str) -> List[str]:
    """Split text into hierarchical sections based on headers."""
    pattern = r"(?=(?:^|\n)(?:Section\s*\d+|[A-Z][A-Z\s]{4,}|^\d+(?:\.\d+)*\s|^[A-Z]\.\s))"
    parts = re.split(pattern, text, flags=re.MULTILINE)
    sections = [p.strip() for p in parts if p.strip()]
    return sections


def split_paragraphs(section_text: str) -> List[str]:
    """Split a section into paragraphs."""
    paragraphs = [p.strip() for p in section_text.split("\n\n") if p.strip()]
    return paragraphs


def sliding_window_split(text: str, max_words: int = 1500, overlap: int = 200) -> List[str]:
    """Split long text into overlapping sliding window chunks."""
    words = text.split()
    chunks = []
    start = 0
    while start < len(words):
        end = min(start + max_words, len(words))
        chunks.append(" ".join(words[start:end]))
        if end == len(words):
            break
        start = end - overlap
    return chunks


def is_meaningful(text: str, min_alpha_num: int = 20, min_words: int = 5) -> bool:
    """
    Filter out garbage / bullets-only chunks.
    - Require at least min_alpha_num alphanumeric characters.
    - Require at least min_words words.
    """
    core = re.sub(r"[^A-Za-z0-9]", "", text)
    if len(core) < min_alpha_num:
        return False
    if len(text.split()) < min_words:
        return False
    return True


def create_chunks(file_path: str) -> List[dict]:
    """Create chunks from a PDF using hierarchical sections and sliding window."""
    raw_text = read_pdf(file_path)
    cleaned = clean_text(raw_text)
    sections = split_into_sections(cleaned)

    chunks = []
    for section_index, section in enumerate(sections, start=1):
        paragraphs = split_paragraphs(section)
        if not paragraphs:
            continue

        for para in paragraphs:
            # Skip bullets-only / low-signal paragraphs
            if not is_meaningful(para):
                continue

            # If paragraph is too long, split into sliding window
            if len(para.split()) > 1500:
                window_chunks = sliding_window_split(para, max_words=1500, overlap=200)
                for wc in window_chunks:
                    chunks.append({
                        "chunk_id": f"Chunk_{len(chunks)+1:03d}",
                        "text": wc
                    })
            else:
                chunks.append({
                    "chunk_id": f"Chunk_{len(chunks)+1:03d}",
                    "text": para
                })
    return chunks

def _parse_llm_json(text: str) -> dict:
    """Robustly extract a single JSON object from the LLM output."""
    text = text.strip()

    # 1) Try direct parse
    try:
        return json.loads(text)
    except Exception:
        pass

    # 2) If there are ```json ... ``` fences, parse the inside
    fenced = re.search(r"```(?:json)?(.*?)```", text, re.DOTALL)
    if fenced:
        inner = fenced.group(1).strip()
        try:
            return json.loads(inner)
        except Exception:
            text = inner  # continue working on inner

    # 3) Try every {...} block as a candidate JSON object
    candidates = re.findall(r"\{.*?\}", text, re.DOTALL)
    for c in candidates:
        c = c.strip()
        try:
            return json.loads(c)
        except Exception:
            continue

    # 4) If multiple objects are glued together, split after each }
    parts = re.split(r'(?<=\})\s*(?=\{)', text)
    for p in parts:
        p = p.strip()
        if not p:
            continue
        try:
            return json.loads(p)
        except Exception:
            continue

    # 5) Fallback default if nothing could be parsed
    return {
        "compliant": "No",
        "rephrased_requirement": "",
        "confidence": 0.2,
    }


def call_llm(prompt: str) -> dict:
    try:
        response = ollama.chat(
            model="llama3.1",
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.2}
        )

        # Extract text content
        if isinstance(response, dict) and "message" in response and "content" in response["message"]:
            text = response["message"]["content"]
        elif hasattr(response, "message") and hasattr(response.message, "content"):
            text = response.message.content
        else:
            text = str(response)

        # Debug preview
        print("\n--- RAW LLM RESPONSE (truncated) ---")
        print(text[:300].strip(), "...\n")

        # Use robust JSON parser
        data = _parse_llm_json(text)

        compliant = data.get("compliant", "No")
        rephrased = data.get("rephrased_requirement", "")
        confidence = data.get("confidence", 0.5)

        # If model returns a list of requirements, join them into a single string
        if isinstance(rephrased, list):
            rephrased = " ".join(
                r.strip() for r in rephrased
                if isinstance(r, str) and r.strip()
            )

        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.5

        return {
            "compliant": compliant,
            "rephrased_requirement": rephrased,
            "confidence": confidence,
        }

    except Exception as e:
        print("❌ Error calling LLaMA:", e)
        return {
            "compliant": "No",
            "rephrased_requirement": "",
            "confidence": 0.0
        }

def process_chunks(chunks: List[dict]) -> List[dict]:
    compliance_table = []

    for chunk in chunks:
        prompt = f"""
You are a Compliance Analyst at a Location Services firm (IT services Firm).
Your task is to classify and extract mandatory technical requirements from the given RFP section.

RFP section:
---
{chunk['text']}
---

Follow these rules carefully:

1. Decide if this section contains any mandatory technical requirements.
2. Ignore commercial, legal, ownership, disclaimers, marketing, or descriptive text.
3. If relevant content exists, rephrase each requirement in 1–2 concise sentences suitable for a compliance table.
4. Regardless of whether you say "Yes" or "No", include a confidence score between 0.0 and 1.0 that reflects **your certainty in your decision**.
   - Example: You can output `"compliant": "No", "confidence": 0.95"` if you're very confident it's irrelevant.
   - Example: You can output `"compliant": "Yes", "confidence": 0.55"` if you’re unsure but think it’s relevant.
5. Output strictly in JSON with **no markdown, no explanations, no extra text**.
6. Your JSON must have exactly these fields:
   - compliant
   - rephrased_requirement
   - confidence
7. Return EXACTLY ONE JSON object and nothing else.
   Do not include multiple JSON objects. Do not add explanations, comments, or markdown.
Examples:

If no requirement is found:
{{
  "compliant": "No",
  "rephrased_requirement": "",
  "confidence": 0.94
}}

If somewhat relevant:
{{
  "compliant": "Yes",
  "rephrased_requirement": "System may include integration with scheduling software.",
  "confidence": 0.57
}}

If clearly relevant:
{{
  "compliant": "Yes",
  "rephrased_requirement": "The system must support AI-driven space utilization analytics.",
  "confidence": 0.93
}}
"""
        response = call_llm(prompt)
        print(f"{chunk['chunk_id']} -> {response}")

        # Normalize rephrased_requirement field
        req = response.get("rephrased_requirement", "")
        if isinstance(req, list):
            req = " ".join(r.strip() for r in req if r.strip())

        compliant = response.get("compliant", "No")
        confidence = float(response.get("confidence", 0.0))

        # Keep all results (both Yes and No) for review
        compliance_table.append({
            "requirement_id": f"RFP_{len(compliance_table)+1:03d}",
            "rephrased_requirement": req,
            "chunk_id": chunk["chunk_id"],
            "original_chunk": chunk["text"],
            "mandatory_optional": "",
            "compliant": compliant,
            "reference_evidence": "",
            "notes": "",
            "confidence": confidence,
        })

    return compliance_table



def save_json(data: List[dict], output_path: str):
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ---------- Main Execution ----------

# if __name__ == "__main__":
#     pdf_path = "SOW-MBZUAI-034.pdf"
#     output_json = "compliance_table.json"

#     # 🔁 NEW: auto-detect scanned & apply OCR if needed
#     pdf_path = ocr_pdf_if_needed(pdf_path)

#     print("📄 Loading PDF and creating hierarchical chunks...")
#     chunks = create_chunks(pdf_path)
#     print(f"✅ Total chunks created: {len(chunks)}")

#     print("\n⚙️ Processing chunks with LLaMA 3...\n")
#     compliance_table = process_chunks(chunks)
#     print(f"\n✅ Total extracted compliance entries: {len(compliance_table)}")

#     save_json(compliance_table, output_json)
#     print(f"💾 Compliance table saved to {output_json}")
# ---------- Public API for backend ----------

from typing import List

def run_english_pipeline(pdf_path: str) -> List[dict]:
    """
    High-level English RFP pipeline:
    - OCR if needed
    - chunk PDF text (hierarchical + sliding window)
    - call LLaMA on each chunk
    - يرجّع list[dict] بنفس فورمات العربي تقريباً.
    """
    # 1) OCR لو PDF سكان
    pdf_to_use = ocr_pdf_if_needed(pdf_path)

    # 2) Chunks
    print("📄 [EN] Loading PDF and creating hierarchical chunks...")
    chunks = create_chunks(pdf_to_use)
    print(f"✅ [EN] Total chunks created: {len(chunks)}")

    # 3) LLM
    print("\n⚙️ [EN] Processing chunks with LLaMA 3...\n")
    compliance_table = process_chunks(chunks)
    print(f"\n✅ [EN] Total extracted compliance entries: {len(compliance_table)}")

    # مافي حفظ JSON هنا، الـ backend / React يتصرف فيهم
    return compliance_table
