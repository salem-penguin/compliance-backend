# from __future__ import annotations

# import io
# import json
# import os
# import re
# import subprocess
# from pathlib import Path
# from typing import List, Tuple

# import ollama
# import pdfplumber
# import fitz  # PyMuPDF
# import pandas as pd
# from fastapi import FastAPI, File, HTTPException, UploadFile
# from fastapi.middleware.cors import CORSMiddleware
# from fastapi.responses import StreamingResponse
# from pydantic import BaseModel
# from PyPDF2 import PdfReader

# # ============================================================
# # FastAPI app & CORS
# # ============================================================

# app = FastAPI(title="Compliance Table Backend", version="0.1.0")

# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"],  # TODO: in production, restrict this to your frontend origin
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )

# # ============================================================
# # Shared regex / helpers
# # ============================================================

# ARABIC_CHARS_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")
# LETTER_RE = re.compile(r"[\w\u0600-\u06FF]", re.UNICODE)


# def has_arabic(text: str) -> bool:
#     return bool(ARABIC_CHARS_RE.search(text or ""))


# # ============================================================
# # Pydantic models (API contracts with frontend)
# # ============================================================


# class ComplianceRow(BaseModel):
#     """
#     Row shape expected by the React DataGrid (camelCase keys),
#     matching your ComplianceRow TS interface.
#     """

#     id: str
#     chunkId: str
#     outlineNumber: str
#     text: str
#     rephrasedRequirement: str
#     compliant: str
#     mandatoryOptional: str
#     confidence: float
#     pageNumber: int | None = None


# class RunResponse(BaseModel):
#     language: str  # "arabic" | "english"
#     rows: List[ComplianceRow]


# class SaveSelectionRequest(BaseModel):
#     fileName: str
#     rows: List[ComplianceRow]


# # ============================================================
# # BASIC HEALTH CHECK
# # ============================================================


# @app.get("/api/health")
# async def health():
#     return {"status": "ok"}


# # ============================================================
# # ENGLISH PIPELINE (with OCR for scanned PDFs)
# # ============================================================

# def is_scanned_pdf(pdf_path: str, min_text_threshold: int = 50) -> bool:
#     """
#     Roughly determine if a PDF is scanned (image-based)
#     by checking how much extractable text it has.
#     """
#     try:
#         reader = PdfReader(pdf_path)
#         total_text_len = 0

#         for page in reader.pages:
#             txt = page.extract_text() or ""
#             total_text_len += len(txt.strip())
#             if total_text_len >= min_text_threshold:
#                 return False  # has enough text

#         return True
#     except Exception:
#         # If anything goes wrong, assume NOT scanned to avoid hard failure
#         return False


# def ocr_pdf_if_needed(pdf_path: str) -> str:
#     """
#     If the PDF appears to be scanned (image-only), run OCR using ocrmypdf and
#     return the path to the OCR-processed PDF. Otherwise, return original path.
#     """
#     if not is_scanned_pdf(pdf_path):
#         print("✅ Detected searchable PDF (no OCR needed).")
#         return pdf_path

#     print("🔎 Detected scanned / image-only PDF. Running OCR with ocrmypdf...")

#     base, ext = os.path.splitext(pdf_path)
#     ocr_output = f"{base}_ocr{ext}"

#     try:
#         subprocess.run(
#             ["ocrmypdf", "--force-ocr", "--deskew", pdf_path, ocr_output],
#             check=True,
#         )
#         print(f"✅ OCR complete. Using OCR version: {ocr_output}")
#         return ocr_output
#     except FileNotFoundError:
#         print(
#             "❌ ocrmypdf is not installed. "
#             "Install with `pip install ocrmypdf` if you want OCR. "
#             "Continuing with original PDF."
#         )
#         return pdf_path
#     except subprocess.CalledProcessError as e:
#         print(f"❌ OCR failed: {e}. Using original PDF.")
#         return pdf_path


# def read_pdf_text(pdf_path: str) -> str:
#     text = ""
#     with pdfplumber.open(pdf_path) as pdf:
#         for page in pdf.pages:
#             page_text = page.extract_text()
#             if page_text:
#                 text += page_text.rstrip() + "\n\n"
#     return text


# def clean_text(text: str) -> str:
#     text = re.sub(r"\.{3,}", " ", text)
#     text = re.sub(r"(?m)^[\t ]*\d+[\t ]*$", "", text)
#     text = re.sub(r"\n{3,}", "\n\n", text)
#     text = re.sub(r"[\t ]{2,}", " ", text)
#     return text.strip()


# def split_into_sections(text: str) -> List[str]:
#     pattern = r"(?=(?:^|\n)(?:Section\s*\d+|[A-Z][A-Z\s]{4,}|^\d+(?:\.\d+)*\s|^[A-Z]\.\s))"
#     parts = re.split(pattern, text, flags=re.MULTILINE)
#     return [p.strip() for p in parts if p.strip()]


# def split_paragraphs(section_text: str) -> List[str]:
#     return [p.strip() for p in section_text.split("\n\n") if p.strip()]


# def sliding_window_split(
#     text: str, max_words: int = 1500, overlap: int = 200
# ) -> List[str]:
#     words = text.split()
#     chunks: List[str] = []
#     start = 0
#     while start < len(words):
#         end = min(start + max_words, len(words))
#         chunks.append(" ".join(words[start:end]))
#         if end == len(words):
#             break
#         start = end - overlap
#     return chunks


# def is_meaningful(text: str, min_alpha_num: int = 20, min_words: int = 5) -> bool:
#     core = re.sub(r"[^A-Za-z0-9]", "", text)
#     if len(core) < min_alpha_num:
#         return False
#     if len(text.split()) < min_words:
#         return False
#     return True


# def create_chunks_english(pdf_path: str) -> List[dict]:
#     raw_text = read_pdf_text(pdf_path)
#     cleaned = clean_text(raw_text)
#     sections = split_into_sections(cleaned)

#     chunks: List[dict] = []
#     for section in sections:
#         paragraphs = split_paragraphs(section)
#         if not paragraphs:
#             continue

#         for para in paragraphs:
#             if not is_meaningful(para):
#                 continue

#             if len(para.split()) > 1500:
#                 for wc in sliding_window_split(para):
#                     chunks.append(
#                         {
#                             "chunk_id": f"Chunk_{len(chunks)+1:03d}",
#                             "text": wc,
#                         }
#                     )
#             else:
#                 chunks.append(
#                     {
#                         "chunk_id": f"Chunk_{len(chunks)+1:03d}",
#                         "text": para,
#                     }
#                 )
#     return chunks


# def _parse_llm_json(text: str) -> dict:
#     text = text.strip()

#     try:
#         return json.loads(text)
#     except Exception:
#         pass

#     fenced = re.search(r"```(?:json)?(.*?)```", text, re.DOTALL)
#     if fenced:
#         inner = fenced.group(1).strip()
#         try:
#             return json.loads(inner)
#         except Exception:
#             text = inner

#     candidates = re.findall(r"\{.*?\}", text, re.DOTALL)
#     for c in candidates:
#         c = c.strip()
#         try:
#             return json.loads(c)
#         except Exception:
#             continue

#     parts = re.split(r"(?<=\})\s*(?=\{)", text)
#     for p in parts:
#         p = p.strip()
#         if not p:
#             continue
#         try:
#             return json.loads(p)
#         except Exception:
#             continue

#     return {
#         "compliant": "No",
#         "rephrased_requirement": "",
#         "confidence": 0.2,
#     }


# def call_llm_english(prompt: str) -> dict:
#     try:
#         response = ollama.chat(
#             model="llama3.1",
#             messages=[{"role": "user", "content": prompt}],
#             options={"temperature": 0.2},
#         )

#         if isinstance(response, dict) and "message" in response:
#             text = response["message"]["content"]
#         else:
#             # ollama-python usually returns a dict; this is just a safety net
#             text = getattr(response, "message", getattr(response, "content", str(response)))

#         print("\n--- RAW LLM RESPONSE (truncated) ---")
#         print(text[:300].strip(), "...\n")

#         data = _parse_llm_json(text)

#         compliant = data.get("compliant", "No")
#         rephrased = data.get("rephrased_requirement", "")
#         confidence = data.get("confidence", 0.5)

#         if isinstance(rephrased, list):
#             rephrased = " ".join(
#                 r.strip() for r in rephrased if isinstance(r, str) and r.strip()
#             )

#         try:
#             confidence = float(confidence)
#         except (TypeError, ValueError):
#             confidence = 0.5

#         return {
#             "compliant": compliant,
#             "rephrased_requirement": rephrased,
#             "confidence": confidence,
#         }
#     except Exception as e:
#         print("❌ Error calling LLaMA (english):", e)
#         return {
#             "compliant": "No",
#             "rephrased_requirement": "",
#             "confidence": 0.0,
#         }


# def process_chunks_english(chunks: List[dict]) -> List[dict]:
#     compliance_table: List[dict] = []

#     for chunk in chunks:
#         prompt = f"""
# You are a Compliance Analyst at a Location Services firm (IT services Firm).
# Your task is to classify and extract mandatory technical requirements from the given RFP section.

# RFP section:
# ---
# {chunk['text']}
# ---

# Follow these rules carefully:

# 1. Decide if this section contains any mandatory technical requirements.
# 2. Ignore commercial, legal, ownership, disclaimers, marketing, or descriptive text.
# 3. If relevant content exists, rephrase each requirement in 1–2 concise sentences suitable for a compliance table.
# 4. Regardless of whether you say "Yes" or "No", include a confidence score between 0.0 and 1.0 that reflects your certainty in your decision.
# 5. Output strictly in JSON with no markdown, no explanations, no extra text.
# 6. Your JSON must have exactly these fields:
#    - compliant
#    - rephrased_requirement
#    - confidence
# """
#         response = call_llm_english(prompt)
#         print(f"{chunk['chunk_id']} -> {response}")

#         req = response.get("rephrased_requirement", "")
#         if isinstance(req, list):
#             req = " ".join(r.strip() for r in req if r.strip())

#         compliant = response.get("compliant", "No")
#         confidence = float(response.get("confidence", 0.0))

#         compliance_table.append(
#             {
#                 "requirement_id": f"RFP_{len(compliance_table)+1:03d}",
#                 "rephrased_requirement": req,
#                 "chunk_id": chunk["chunk_id"],
#                 "original_chunk": chunk["text"],
#                 "mandatory_optional": "",
#                 "compliant": compliant,
#                 "reference_evidence": "",
#                 "notes": "",
#                 "confidence": confidence,
#             }
#         )

#     return compliance_table


# # ============================================================
# # ARABIC PIPELINE  (adapted from your final.py)
# # ============================================================

# def fix_reversed_arabic(text: str) -> str:
#     if not text or not has_arabic(text):
#         return text

#     fixed_lines: List[str] = []

#     for line in text.split("\n"):
#         stripped = line.strip()
#         if not stripped:
#             fixed_lines.append("")
#             continue

#         if has_arabic(stripped):
#             words = stripped.split()
#             reversed_letters = [w[::-1] if has_arabic(w) else w for w in words]
#             corrected = list(reversed(reversed_letters))
#             fixed_lines.append(" ".join(corrected))
#         else:
#             fixed_lines.append(stripped)

#     return "\n".join(fixed_lines)


# def detect_arabic_pdf(pdf_path: str, sample_pages: int = 3) -> bool:
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
#         print(f"📊 Language detection: {ratio:.1%} Arabic")
#         return ratio >= 0.15
#     except Exception as e:
#         print(f"⚠️ Detection error: {e}")
#         return False


# def extract_text_best(pdf_path: str) -> str:
#     methods: List[Tuple[str, str]] = []

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
#         print(f"⚠️ pdfplumber failed: {e}")

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
#         print(f"⚠️ PyMuPDF failed: {e}")

#     if not methods:
#         raise RuntimeError("All extraction methods failed!")

#     scored = [(name, txt, len(LETTER_RE.findall(txt))) for name, txt in methods]
#     scored.sort(key=lambda x: x[2], reverse=True)

#     best_method, best_text, score = scored[0]
#     print(f"✅ Extraction method: {best_method} ({score:,} letters)")
#     return best_text


# def process_pdf_arabic(pdf_path: str) -> Tuple[str, bool]:
#     print(f"\n{'='*70}")
#     print(f"📄 Processing (arabic detector): {Path(pdf_path).name}")
#     print(f"{'='*70}\n")

#     is_arabic = detect_arabic_pdf(pdf_path)
#     raw_text = extract_text_best(pdf_path)

#     if not raw_text.strip():
#         print("⚠️ No text extracted!")
#         return "", is_arabic

#     if is_arabic:
#         print("🔧 Fixing reversed Arabic text where needed...")
#         fixed_text = fix_reversed_arabic(raw_text)
#         return fixed_text, True

#     return raw_text, False


# def clean_text_generic(text: str) -> str:
#     if not text:
#         return ""
#     text = re.sub(r"\.{3,}", " ", text)
#     text = re.sub(r"(?m)^[\t ]*\d+[\t ]*$", "", text)
#     text = re.sub(r"\n{3,}", "\n\n", text)
#     text = re.sub(r"[\t ]{2,}", " ", text)
#     text = re.sub(r"[\u200b-\u200f\ufeff]", "", text)
#     return text.strip()


# def is_meaningful_generic(text: str, min_words: int = 5) -> bool:
#     if not text:
#         return False
#     words = re.split(r"\s+", text.strip())
#     words = [w for w in words if w]
#     if len(words) < min_words:
#         return False
#     letter_count = len(LETTER_RE.findall(text))
#     return letter_count >= min_words


# def create_chunks_arabic(pdf_path: str) -> List[dict]:
#     text, is_arabic_pdf = process_pdf_arabic(pdf_path)
#     if not text:
#         return []

#     text = clean_text_generic(text)
#     paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

#     chunks: List[dict] = []
#     for para in paragraphs:
#         if is_meaningful_generic(para):
#             chunks.append(
#                 {
#                     "chunk_id": f"Chunk_{len(chunks)+1:03d}",
#                     "text": para,
#                     "word_count": len(para.split()),
#                     "is_arabic": is_arabic_pdf,
#                 }
#             )

#     print(f"✅ Created {len(chunks)} meaningful Arabic chunks")
#     return chunks


# SYSTEM_PROMPT_AR = """
# You are a Compliance Analyst at an IT Location Services firm.
# You read sections of an RFP (often in Arabic) and decide whether they contain
# mandatory technical requirements relevant to IT / systems / solution design.

# You must always answer with exactly one JSON object with the fields:
# - compliant: "Yes" or "No"
# - rephrased_requirement: short English summary (or empty string if No)
# - confidence: float between 0.0 and 1.0
# """


# def safe_parse_llm_json_ar(text: str) -> dict:
#     text = (text or "").strip()
#     try:
#         return json.loads(text)
#     except Exception:
#         pass

#     m = re.search(r"\{[\s\S]*\}", text)
#     if m:
#         candidate = m.group(0).strip()
#         try:
#             return json.loads(candidate)
#         except Exception:
#             pass

#     return {"compliant": "No", "rephrased_requirement": "", "confidence": 0.2}


# def normalize_compliant(v) -> str:
#     if isinstance(v, bool):
#         return "Yes" if v else "No"
#     if isinstance(v, str):
#         return "Yes" if v.strip().lower() in ["yes", "y", "true", "1", "نعم", "yes."] else "No"
#     if isinstance(v, (int, float)):
#         return "Yes" if v != 0 else "No"
#     return "No"


# def ask_llm_arabic(text: str) -> dict:
#     user_prompt = f"""
# RFP section (may be Arabic or English):

# ---
# {text}
# ---

# Return ONLY a JSON object like:
# {{
#   "compliant": "Yes" or "No",
#   "rephrased_requirement": "short requirement in English (or empty if No)",
#   "confidence": 0.0-1.0
# }}
# """
#     try:
#         response = ollama.chat(
#             model="llama3.1",
#             messages=[
#                 {"role": "system", "content": SYSTEM_PROMPT_AR},
#                 {"role": "user", "content": user_prompt},
#             ],
#             options={"temperature": 0.0, "format": "json"},
#         )

#         content = (
#             response.message.content
#             if hasattr(response, "message")
#             else response["message"]["content"]  # type: ignore[index]
#         )

#         print("── LLM RAW JSON (arabic) ──")
#         print(content[:300])
#         print("──────────────────────────\n")

#         try:
#             data = json.loads(content)
#         except Exception:
#             data = safe_parse_llm_json_ar(content)

#         compliant = normalize_compliant(data.get("compliant", "No"))
#         rephrased = data.get("rephrased_requirement", "")
#         confidence = float(data.get("confidence", 0.5))
#         return {
#             "compliant": compliant,
#             "rephrased_requirement": rephrased,
#             "confidence": confidence,
#         }
#     except Exception as e:
#         print("❌ Error calling LLaMA (arabic):", e)
#         return {
#             "compliant": "No",
#             "rephrased_requirement": "",
#             "confidence": 0.0,
#         }


# def process_chunks_arabic(chunks: List[dict]) -> List[dict]:
#     out: List[dict] = []
#     for ch in chunks:
#         r = ask_llm_arabic(ch["text"])
#         out.append(
#             {
#                 "requirement_id": f"RFP_{len(out)+1:03d}",
#                 "chunk_id": ch["chunk_id"],
#                 "original_chunk": ch["text"],
#                 "rephrased_requirement": r["rephrased_requirement"],
#                 "compliant": r["compliant"],
#                 "mandatory_optional": "",
#                 "reference_evidence": "",
#                 "notes": "",
#                 "confidence": r["confidence"],
#             }
#         )
#     return out


# # ============================================================
# # SHARED: map raw compliance JSON → ComplianceRow for frontend
# # ============================================================

# def map_raw_to_rows(raw: List[dict]) -> List[ComplianceRow]:
#     rows: List[ComplianceRow] = []
#     for i, rec in enumerate(raw, start=1):
#         req_id = rec.get("requirement_id") or f"RFP_{i:03d}"
#         chunk_id = rec.get("chunk_id") or f"Chunk_{i:03d}"

#         outline_number = str(i)  # simple 1..N for now
#         text = rec.get("original_chunk") or ""
#         rephrased = rec.get("rephrased_requirement") or ""
#         compliant = rec.get("compliant") or "No"
#         mandatory_optional = rec.get("mandatory_optional") or ""
#         confidence = float(rec.get("confidence") or 0.0)

#         rows.append(
#             ComplianceRow(
#                 id=req_id,
#                 chunkId=chunk_id,
#                 outlineNumber=outline_number,
#                 text=text,
#                 rephrasedRequirement=rephrased,
#                 compliant=compliant,
#                 mandatoryOptional=mandatory_optional,
#                 confidence=confidence,
#                 pageNumber=None,
#             )
#         )
#     return rows


# # ============================================================
# # API: run pipeline on uploaded PDF
# # ============================================================


# @app.post("/api/compliance/run", response_model=RunResponse)
# async def run_compliance(file: UploadFile = File(...)):
#     """
#     Main entry point for the frontend:
#     - Save uploaded PDF
#     - Detect language (Arabic vs non-Arabic)
#     - Run the appropriate pipeline
#     - Return rows ready for the DataGrid
#     """
#     if file.content_type != "application/pdf":
#         raise HTTPException(status_code=400, detail="Only PDF files are supported.")

#     upload_dir = Path("uploads")
#     upload_dir.mkdir(exist_ok=True)
#     pdf_path = upload_dir / file.filename

#     with pdf_path.open("wb") as f:
#         f.write(await file.read())

#     # Quick language detection based on first pages
#     is_arabic = detect_arabic_pdf(str(pdf_path))

#     if is_arabic:
#         print("🌐 Detected ARABIC RFP → using Arabic pipeline")
#         chunks = create_chunks_arabic(str(pdf_path))
#         raw_results = process_chunks_arabic(chunks)
#         language = "arabic"
#     else:
#         print("🌐 Detected NON-ARABIC RFP → using English pipeline")
#         pdf_for_english = ocr_pdf_if_needed(str(pdf_path))
#         chunks = create_chunks_english(pdf_for_english)
#         raw_results = process_chunks_english(chunks)
#         language = "english"

#     # Optionally: save full raw JSON for offline debugging / your
#     # hierarchical-outline script (the big code you pasted).
#     with open("compliance_table.json", "w", encoding="utf-8") as f:
#         json.dump(raw_results, f, indent=2, ensure_ascii=False)

#     rows = map_raw_to_rows(raw_results)
#     return RunResponse(language=language, rows=rows)


# # ============================================================
# # API: export selected rows to Excel
# # ============================================================

# def build_excel_from_rows(rows: List[ComplianceRow]) -> io.BytesIO:
#     raw = [r.model_dump() for r in rows]
#     df = pd.DataFrame(raw)

#     # Rename for nicer column names
#     df = df.rename(
#         columns={
#             "id": "requirement_id",
#             "chunkId": "chunk_id",
#             "outlineNumber": "outline_number",
#             "text": "text",
#             "rephrasedRequirement": "rephrased_requirement",
#             "mandatoryOptional": "mandatory_optional",
#         }
#     )

#     cols = [
#         "chunk_id",
#         "requirement_id",
#         "outline_number",
#         "text",
#         "rephrased_requirement",
#         "compliant",
#         "mandatory_optional",
#         "confidence",
#         "pageNumber",
#     ]
#     df = df[[c for c in cols if c in df.columns]]

#     output = io.BytesIO()
#     with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
#         df.to_excel(writer, index=False, sheet_name="Compliance")

#         wb = writer.book
#         ws = writer.sheets["Compliance"]
#         wrap = wb.add_format({"text_wrap": True, "valign": "top"})

#         ws.set_column("A:A", 15, wrap)
#         ws.set_column("B:B", 18, wrap)
#         ws.set_column("C:C", 12, wrap)
#         ws.set_column("D:D", 60, wrap)
#         ws.set_column("E:E", 60, wrap)
#         ws.set_column("F:F", 12, wrap)
#         ws.set_column("G:G", 18, wrap)
#         ws.set_column("H:H", 10, wrap)
#         if "pageNumber" in df.columns:
#             ws.set_column("I:I", 10, wrap)

#     output.seek(0)
#     return output


# @app.post("/api/compliance/export-xlsx")
# async def export_selected_to_excel(payload: SaveSelectionRequest):
#     """
#     Receives the rows the user selected in the frontend and
#     returns an Excel file containing only those rows.
#     """
#     if not payload.rows:
#         raise HTTPException(status_code=400, detail="No rows provided to export.")

#     excel_buffer = build_excel_from_rows(payload.rows)

#     stem = Path(payload.fileName).stem or "compliance_table"
#     filename = f"{stem}_compliance.xlsx"

#     return StreamingResponse(
#         excel_buffer,
#         media_type=(
#             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
#         ),
#         headers={"Content-Disposition": f'attachment; filename="{filename}"'},
#     )

# backend/main.py
# main.py
# main.py
# main.py

#################################
#################################

# import io
# import os
# import re
# import json
# import subprocess
# from pathlib import Path
# from typing import List, Optional

# import pdfplumber
# import pandas as pd
# from PyPDF2 import PdfReader
# import fitz  # PyMuPDF
# import ollama
# from fastapi import FastAPI, UploadFile, File, HTTPException
# from fastapi.middleware.cors import CORSMiddleware
# from fastapi.responses import StreamingResponse, FileResponse
# from pydantic import BaseModel

# # ============================================================
# # FastAPI app + CORS
# # ============================================================

# app = FastAPI(title="Compliance Table Backend", version="1.0.0")

# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"],
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )

# UPLOAD_DIR = Path("temp_uploads")
# UPLOAD_DIR.mkdir(exist_ok=True)

# # ============================================================
# # Serve PDFs for frontend preview
# # ============================================================

# @app.get("/api/compliance/pdf/{filename}")
# async def get_pdf(filename: str):
#     """
#     Serve a PDF (original or OCR/annotated) from temp_uploads so the frontend
#     can preview it using /api/compliance/pdf/{filename}.
#     """
#     pdf_path = UPLOAD_DIR / filename

#     if not pdf_path.exists():
#         raise HTTPException(status_code=404, detail="PDF not found")

#     return FileResponse(
#         path=str(pdf_path),
#         media_type="application/pdf",
#         filename=pdf_path.name,
#     )

# # ============================================================
# # ---------------- ENGLISH PIPELINE --------------------------
# # ============================================================

# # ---------- Scanned Detection + OCR ----------

# def is_scanned_pdf(pdf_path: str, min_text_threshold: int = 50) -> bool:
#     """
#     Roughly determine if a PDF is scanned (image-based) by checking how much
#     extractable text it has.
#     If total text length < min_text_threshold => likely scanned.
#     """
#     try:
#         reader = PdfReader(pdf_path)
#         total_text_len = 0

#         for page in reader.pages:
#             txt = page.extract_text() or ""
#             total_text_len += len(txt.strip())
#             if total_text_len >= min_text_threshold:
#                 return False

#         return True

#     except Exception as e:
#         print(f"⚠️ Could not inspect PDF for text, assuming NOT scanned. Error: {e}")
#         return False


# def ocr_pdf_if_needed(pdf_path: str) -> str:
#     """
#     If the PDF appears to be scanned (image-only), run OCR using ocrmypdf and
#     return the path to the OCR-processed PDF. Otherwise, return original path.
#     """
#     if not is_scanned_pdf(pdf_path):
#         print("✅ Detected searchable PDF (no OCR needed).")
#         return pdf_path

#     print("🔎 Detected scanned / image-only PDF. Running OCR with ocrmypdf...")

#     base, ext = os.path.splitext(pdf_path)
#     ocr_output = f"{base}_ocr{ext}"

#     try:
#         subprocess.run(
#             ["ocrmypdf", "--force-ocr", "--deskew", pdf_path, ocr_output],
#             check=True,
#         )
#         print(f"✅ OCR complete. Using OCR version: {ocr_output}")
#         return ocr_output

#     except FileNotFoundError:
#         print(
#             "❌ ocrmypdf is not installed or not found in PATH.\n"
#             "   Install it with: pip install ocrmypdf\n"
#             "   Using original PDF without OCR."
#         )
#         return pdf_path

#     except subprocess.CalledProcessError as e:
#         print(f"❌ OCR process failed: {e}\nUsing original PDF without OCR.")
#         return pdf_path


# # ---------- Helper Functions for text / chunks ----------

# def read_pdf(file_path: str) -> str:
#     """Read text from a PDF file."""
#     text = ""
#     with pdfplumber.open(file_path) as pdf:
#         for page in pdf.pages:
#             page_text = page.extract_text()
#             if page_text:
#                 text += page_text.rstrip() + "\n\n"
#     return text


# def clean_text(text: str) -> str:
#     """Clean text and normalize formatting."""
#     text = re.sub(r"\.{3,}", " ", text)
#     text = re.sub(r"(?m)^[\t ]*\d+[\t ]*$", "", text)
#     text = re.sub(r"\n{3,}", "\n\n", text)
#     text = re.sub(r"[\t ]{2,}", " ", text)
#     return text.strip()


# def split_into_sections(text: str) -> List[str]:
#     """Split text into hierarchical sections based on headers."""
#     pattern = r"(?=(?:^|\n)(?:Section\s*\d+|[A-Z][A-Z\s]{4,}|^\d+(?:\.\d+)*\s|^[A-Z]\.\s))"
#     parts = re.split(pattern, text, flags=re.MULTILINE)
#     sections = [p.strip() for p in parts if p.strip()]
#     return sections


# def split_paragraphs(section_text: str) -> List[str]:
#     """Split a section into paragraphs."""
#     paragraphs = [p.strip() for p in section_text.split("\n\n") if p.strip()]
#     return paragraphs


# def sliding_window_split(text: str, max_words: int = 1500, overlap: int = 200) -> List[str]:
#     """Split long text into overlapping sliding window chunks."""
#     words = text.split()
#     chunks = []
#     start = 0
#     while start < len(words):
#         end = min(start + max_words, len(words))
#         chunks.append(" ".join(words[start:end]))
#         if end == len(words):
#             break
#         start = end - overlap
#     return chunks


# def is_meaningful(text: str, min_alpha_num: int = 20, min_words: int = 5) -> bool:
#     """
#     Filter out garbage / bullets-only chunks.
#     - Require at least min_alpha_num alphanumeric characters.
#     - Require at least min_words words.
#     """
#     core = re.sub(r"[^A-Za-z0-9]", "", text)
#     if len(core) < min_alpha_num:
#         return False
#     if len(text.split()) < min_words:
#         return False
#     return True


# def create_chunks(file_path: str) -> List[dict]:
#     """Create chunks from a PDF using hierarchical sections and sliding window."""
#     raw_text = read_pdf(file_path)
#     cleaned = clean_text(raw_text)
#     sections = split_into_sections(cleaned)

#     chunks = []
#     for section_index, section in enumerate(sections, start=1):
#         paragraphs = split_paragraphs(section)
#         if not paragraphs:
#             continue

#         for para in paragraphs:
#             if not is_meaningful(para):
#                 continue

#             if len(para.split()) > 1500:
#                 window_chunks = sliding_window_split(para, max_words=1500, overlap=200)
#                 for wc in window_chunks:
#                     chunks.append(
#                         {
#                             "chunk_id": f"Chunk_{len(chunks)+1:03d}",
#                             "text": wc,
#                         }
#                     )
#             else:
#                 chunks.append(
#                     {
#                         "chunk_id": f"Chunk_{len(chunks)+1:03d}",
#                         "text": para,
#                     }
#                 )
#     return chunks


# # ---------- Robust LLM JSON parsing + call ----------

# def _parse_llm_json(text: str) -> dict:
#     """Robustly extract a single JSON object from the LLM output."""
#     text = text.strip()

#     # 1) Try direct parse
#     try:
#         return json.loads(text)
#     except Exception:
#         pass

#     # 2) If there are ```json ... ``` fences, parse the inside
#     fenced = re.search(r"```(?:json)?(.*?)```", text, re.DOTALL)
#     if fenced:
#         inner = fenced.group(1).strip()
#         try:
#             return json.loads(inner)
#         except Exception:
#             text = inner

#     # 3) Try every {...} block as a candidate JSON object
#     candidates = re.findall(r"\{.*?\}", text, re.DOTALL)
#     for c in candidates:
#         c = c.strip()
#         try:
#             return json.loads(c)
#         except Exception:
#             continue

#     # 4) If multiple objects are glued together, split after each }
#     parts = re.split(r"(?<=\})\s*(?=\{)", text)
#     for p in parts:
#         p = p.strip()
#         if not p:
#             continue
#         try:
#             return json.loads(p)
#         except Exception:
#             continue

#     # 5) Fallback default if nothing could be parsed
#     return {
#         "compliant": "No",
#         "rephrased_requirement": "",
#         "confidence": 0.2,
#     }


# def call_llm(prompt: str) -> dict:
#     try:
#         response = ollama.chat(
#             model="llama3.1",
#             messages=[{"role": "user", "content": prompt}],
#             options={"temperature": 0.2},
#         )

#         # Extract text content
#         if isinstance(response, dict) and "message" in response and "content" in response["message"]:
#             text = response["message"]["content"]
#         elif hasattr(response, "message") and hasattr(response.message, "content"):
#             text = response.message.content
#         else:
#             text = str(response)

#         print("\n--- RAW LLM RESPONSE (truncated) ---")
#         print(text[:300].strip(), "...\n")

#         data = _parse_llm_json(text)

#         compliant = data.get("compliant", "No")
#         rephrased = data.get("rephrased_requirement", "")
#         confidence = data.get("confidence", 0.5)

#         if isinstance(rephrased, list):
#             rephrased = " ".join(
#                 r.strip() for r in rephrased if isinstance(r, str) and r.strip()
#             )

#         try:
#             confidence = float(confidence)
#         except (TypeError, ValueError):
#             confidence = 0.5

#         return {
#             "compliant": compliant,
#             "rephrased_requirement": rephrased,
#             "confidence": confidence,
#         }

#     except Exception as e:
#         print("❌ Error calling LLaMA:", e)
#         return {
#             "compliant": "No",
#             "rephrased_requirement": "",
#             "confidence": 0.0,
#         }


# def process_chunks(chunks: List[dict]) -> List[dict]:
#     """
#     Returns the "compliance_table" structure
#     """
#     compliance_table = []

#     for chunk in chunks:
#         prompt = f"""
# You are a Compliance Analyst at a Location Services firm (IT services Firm).
# Your task is to classify and extract mandatory technical requirements from the given RFP section.

# RFP section:
# ---
# {chunk['text']}
# ---

# Follow these rules carefully:

# 1. Decide if this section contains any mandatory technical requirements.
# 2. Ignore commercial, legal, ownership, disclaimers, marketing, or descriptive text.
# 3. If relevant content exists, rephrase each requirement in 1–2 concise sentences suitable for a compliance table.
# 4. Regardless of whether you say "Yes" or "No", include a confidence score between 0.0 and 1.0 that reflects **your certainty in your decision**.
#    - Example: "compliant": "No", "confidence": 0.95
#    - Example: "compliant": "Yes", "confidence": 0.55
# 5. Output strictly in JSON with no markdown, no explanations, no extra text.
# 6. Your JSON must have exactly these fields:
#    - compliant
#    - rephrased_requirement
#    - confidence
# 7. Return EXACTLY ONE JSON object and nothing else.
# """
#         response = call_llm(prompt)
#         print(f"{chunk['chunk_id']} -> {response}")

#         req = response.get("rephrased_requirement", "")
#         if isinstance(req, list):
#             req = " ".join(r.strip() for r in req if isinstance(r, str) and r.strip())

#         compliant = response.get("compliant", "No")
#         confidence = float(response.get("confidence", 0.0))

#         compliance_table.append(
#             {
#                 "requirement_id": f"RFP_{len(compliance_table)+1:03d}",
#                 "rephrased_requirement": req,
#                 "chunk_id": chunk["chunk_id"],
#                 "original_chunk": chunk["text"],
#                 "mandatory_optional": "",
#                 "compliant": compliant,
#                 "reference_evidence": "",
#                 "notes": "",
#                 "confidence": confidence,
#             }
#         )

#     return compliance_table


# # ============================================================
# # -------- MODELS used by API / frontend ---------------------
# # ============================================================

# class ComplianceRowOut(BaseModel):
#     id: str
#     chunkId: str
#     outlineNumber: str
#     text: str
#     rephrasedRequirement: str
#     compliant: str
#     mandatoryOptional: Optional[str] = ""
#     confidence: Optional[float] = 0.0


# class HierarchicalRowOut(BaseModel):
#     """Model for hierarchically split rows shown in frontend"""
#     id: str
#     chunkId: str
#     requirementId: str
#     outlineNumber: str
#     level: int
#     text: str
#     compliant: str
#     mandatoryOptional: Optional[str] = ""
#     confidence: Optional[float] = 0.0


# class RunResponse(BaseModel):
#     language: str  # "english" | "arabic"
#     rows: List[ComplianceRowOut]          # already hierarchical
#     splitRows: List[HierarchicalRowOut]   # same hierarchy but with level
#     pdfFileName: str                      # name of PDF to preview via /api/compliance/pdf/{pdfFileName}


# class ExportRow(BaseModel):
#     id: str
#     chunkId: str
#     outlineNumber: Optional[str] = None
#     text: str
#     rephrasedRequirement: Optional[str] = ""
#     compliant: str
#     mandatoryOptional: Optional[str] = ""
#     confidence: Optional[float] = 0.0


# class ExportRequest(BaseModel):
#     fileName: str
#     rows: List[ExportRow]


# class SplitRequest(BaseModel):
#     """Request to split selected rows into hierarchical structure"""
#     rows: List[ExportRow]


# class SplitResponse(BaseModel):
#     """Response containing hierarchically split rows"""
#     rows: List[HierarchicalRowOut]


# class HighlightRequest(BaseModel):
#     fileName: str          # PDF filename (e.g. from pdfFileName)
#     chunkText: str         # text to search and highlight
#     requirementId: Optional[str] = None   # optional, for logging/debug


# class HighlightResponse(BaseModel):
#     pdfFileName: str       # annotated PDF filename
#     pageNumber: Optional[int] = None      # first page where highlighted text was found


# # ============================================================
# # -------------- HIERARCHICAL SPLIT HELPERS ------------------
# # ============================================================

# pd.set_option("display.max_colwidth", None)

# BULLET_CHARS = (
#     r"\-"      # dash
#     "\u2022"   # •
#     "\u25CF"   # ●
#     "\u25E6"   # ◦
#     "\u25AA"   # ▪
#     "\u25AB"   # ▫
#     "\u2043"   # ⁃
#     "\u2219"   # ∙
#     "\u00B7"   # ·
#     "\u2023"   # ‣
#     "\u204C"   # ⁌
#     "\u204D"   # ⁍
#     "\u2218"   # ∘
#     "\u25C9"   # ◉
#     "\u25CB"   # ○
#     "\u25A0"   # ■
#     "\u25A1"   # □
#     "\u25B6"   # ▶
#     "\u25B8"   # ▸
#     "\uF0B7"   # Word bullet ()
#     "\u066d"   # ٭
#     "\u06d4"   # ۔
#     "\u06dd"   # ۝
#     "\u06de"   # ۞
# )

# BULLET_RE = re.compile(
#     rf"""^
#         (?P<indent>\s*)
#         (?:
#             [\-\*{BULLET_CHARS}]
#           | \d+[\.\)]
#           | [A-Za-z][\.\)]
#         )
#         \s+
#         (?P<text>.*\S.*)
#     $""",
#     re.VERBOSE,
# )

# CHAIN_RE = re.compile(r"^\s*((?:\d+\.)+\d+)\s+(.*\S.*)$")
# INLINE_SPLIT_REGEX = re.compile(rf"(?:[\s\.]+) ([{BULLET_CHARS}]) \s*", re.VERBOSE)


# def normalize_indent(s: str, tabsize: int = 4) -> int:
#     return len(s.expandtabs(tabsize))


# def is_section_header(line: str) -> bool:
#     return bool(re.match(r"^\s*\d+\.0(?:[.)])?\s+\S", line))


# def parse_chain(line: str):
#     m = CHAIN_RE.match(line)
#     if not m:
#         return None, line
#     chain_str, rest = m.group(1), m.group(2)
#     parts = tuple(int(p) for p in chain_str.split("."))
#     if len(parts) == 2 and parts[1] == 0:
#         return None, line
#     return parts, rest


# def split_inline_bullets(line: str):
#     s = line.strip()
#     if not s:
#         return []

#     parts = INLINE_SPLIT_REGEX.split(s)
#     if len(parts) == 1:
#         return []

#     out = []
#     if parts[0].strip():
#         out.append(("prefix", parts[0].strip()))

#     for i in range(1, len(parts), 2):
#         if i + 1 < len(parts):
#             segment_text = parts[i + 1].strip()
#             if segment_text:
#                 out.append(("bullet", segment_text))

#     return out


# def extract_hierarchical_items(chunk_text: str):
#     """
#     Take raw chunk text and:
#     - insert virtual newlines for section numbers (3.0., 4.1., etc.)
#       and inline bullets (•, , etc.) when the PDF came as one long line.
#     - then run header / bullet / chain logic line by line.
#     """
#     text = str(chunk_text).replace("\r\n", "\n")

#     # If text is basically one long line, inject newlines
#     if text.count("\n") < 3:
#         # Newline before section-like numbers: 3.0., 4.1, 4.1.2, etc.
#         text = re.sub(
#             r"(?<!^)(?<!\n)(\s+)((?:\d+\.0|\d+\.\d+(?:\.\d+)*)(?:[.)])?\s+)",
#             r"\n\2",
#             text,
#         )

#         # Newline before any bullet char (including Word's )
#         bullet_class = BULLET_CHARS
#         text = re.sub(
#             rf"\s*([{bullet_class}])\s*",
#             r"\n\1 ",
#             text,
#         )

#     lines = [ln.rstrip("\r\n") for ln in text.splitlines()]
#     header_text = None
#     items = []

#     def append_to_last(t: str):
#         if not items:
#             items.append((0, None, t.strip()))
#             return
#         ind, hint, prev = items[-1]
#         joiner = "" if prev.endswith("-") else " "
#         items[-1] = (ind, hint, (prev + joiner + t).strip())

#     for ln in lines:
#         if not ln.strip():
#             continue

#         # Section headers like "3.0. Objective ..."
#         if is_section_header(ln):
#             m = re.match(r"^\s*\d+\.0(?:[.)])?\s+(.+)$", ln)
#             if m:
#                 header_text = m.group(1).strip()
#             continue

#         # Number chains like "4.1", "4.1.2", ...
#         chain_parts, rest = parse_chain(ln)
#         if chain_parts:
#             first_num = str(chain_parts[0])
#             indent_guess = normalize_indent(ln[: ln.find(first_num)])
#             items.append((indent_guess, chain_parts, rest.strip()))
#             continue

#         # Bullets at line start
#         m = BULLET_RE.match(ln)
#         if m:
#             indent = normalize_indent(m.group("indent"))
#             text_part = m.group("text").strip()

#             chunks = split_inline_bullets(text_part)
#             if chunks:
#                 if chunks[0][0] == "prefix" and chunks[0][1].strip():
#                     items.append((indent, None, chunks[0][1].strip()))
#                     chunks = chunks[1:]
#                 for _, seg in chunks:
#                     if seg.strip():
#                         items.append((indent + 2, None, seg.strip()))
#             else:
#                 items.append((indent, None, text_part))
#             continue

#         # Fallback: inline bullets inside a normal line
#         chunks = split_inline_bullets(ln)
#         if chunks:
#             if chunks[0][0] == "prefix":
#                 if items:
#                     append_to_last(chunks[0][1])
#                 else:
#                     items.append((0, None, chunks[0][1].strip()))
#                 chunks = chunks[1:]
#             for _, seg in chunks:
#                 items.append((0, None, seg.strip()))
#         else:
#             append_to_last(ln.strip())

#     return header_text, items


# def build_hierarchy(base_num: str, header_text: Optional[str], items, min_indent_step: int = 2):
#     rows = []

#     if header_text:
#         rows.append(
#             {
#                 "outline_number": base_num,
#                 "level": 0,
#                 "text": header_text,
#             }
#         )

#     if not items:
#         return rows

#     counters = []
#     indent_levels = [items[0][0]]

#     def ensure_level(level: int):
#         while len(counters) < level:
#             counters.append(0)
#         while len(counters) > level:
#             counters.pop()

#     for indent, level_hint, text in items:
#         if level_hint:
#             # explicit numeric chain
#             level = len(level_hint)
#             ensure_level(level)
#             counters[-1] += 1
#         else:
#             # infer from indentation
#             cur = indent
#             if cur > indent_levels[-1] + (min_indent_step - 1):
#                 indent_levels.append(cur)
#             else:
#                 while len(indent_levels) > 1 and cur < indent_levels[-1] - (min_indent_step - 1):
#                     indent_levels.pop()
#                 indent_levels[-1] = cur
#             level = len(indent_levels)
#             ensure_level(level)
#             counters[-1] += 1

#         suffix = ".".join(str(c) for c in counters[:level])
#         rows.append(
#             {
#                 "outline_number": f"{base_num}.{suffix}",
#                 "level": level,
#                 "text": text,
#             }
#         )

#     return rows


# def numeric_sort_key(s):
#     if s is None:
#         return ()
#     parts = str(s).split(".")
#     return tuple(int(p) if p.isdigit() else 0 for p in parts)


# def build_hierarchical_df_from_rows(rows_in: List[ExportRow]) -> pd.DataFrame:
#     rows_out = []
#     chunk_index = 0

#     for record in rows_in:
#         chunk_index += 1
#         base_num = str(chunk_index)

#         chunk_id = record.chunkId or f"Chunk_{chunk_index:03d}"
#         chunk_text = record.text or ""
#         requirement_id = record.id
#         compliant = record.compliant
#         confidence = record.confidence
#         mandatory_optional = record.mandatoryOptional

#         header_text, items = extract_hierarchical_items(chunk_text)

#         if not header_text and not items:
#             rows_out.append(
#                 {
#                     "chunk_id": chunk_id,
#                     "requirement_id": requirement_id,
#                     "outline_number": base_num,
#                     "level": 0,
#                     "text": str(chunk_text).strip(),
#                     "compliant": compliant,
#                     "mandatory_optional": mandatory_optional,
#                     "confidence": confidence,
#                 }
#             )
#             continue

#         for row in build_hierarchy(base_num, header_text, items):
#             row.update(
#                 {
#                     "chunk_id": chunk_id,
#                     "requirement_id": requirement_id,
#                     "compliant": compliant,
#                     "mandatory_optional": mandatory_optional,
#                     "confidence": confidence,
#                 }
#             )
#             rows_out.append(row)

#     df = pd.DataFrame(
#         rows_out,
#         columns=[
#             "chunk_id",
#             "requirement_id",
#             "outline_number",
#             "level",
#             "text",
#             "compliant",
#             "mandatory_optional",
#             "confidence",
#         ],
#     )

#     if not df.empty:
#         df["sort_key"] = df["outline_number"].apply(numeric_sort_key)
#         df = df.sort_values(by=["sort_key"], kind="mergesort").drop(columns=["sort_key"])

#     return df


# def build_hierarchical_rows_for_frontend(rows_in: List[ExportRow]) -> List[HierarchicalRowOut]:
#     """
#     Build hierarchical rows for the frontend using the EXACT same logic
#     used previously for Excel.
#     """
#     df = build_hierarchical_df_from_rows(rows_in)
#     df = df.reset_index(drop=True)

#     rows_out: List[HierarchicalRowOut] = []

#     for idx, row in df.iterrows():
#         rows_out.append(
#             HierarchicalRowOut(
#                 id=f"{row['requirement_id']}_{idx}",
#                 chunkId=str(row["chunk_id"]),
#                 requirementId=str(row["requirement_id"]),
#                 outlineNumber=str(row["outline_number"]),
#                 level=int(row["level"]),
#                 text=str(row["text"] or "").strip(),
#                 compliant=str(row["compliant"] or "No"),
#                 mandatoryOptional=str(row.get("mandatory_optional") or ""),
#                 confidence=float(row.get("confidence") or 0.0),
#             )
#         )

#     return rows_out


# # ============================================================
# # ----------------------- API ROUTES -------------------------
# # ============================================================

# @app.get("/api/health")
# async def health():
#     return {"status": "ok"}


# @app.post("/api/compliance/run", response_model=RunResponse)
# async def run_compliance(file: UploadFile = File(...)):
#     """
#     Upload a PDF:
#     - Save to temp_uploads
#     - Auto OCR if needed (processed PDF might be different filename)
#     - Chunk
#     - Run LLaMA compliance extraction
#     - Split hierarchically BEFORE sending to frontend
#     - Return pdfFileName for frontend preview
#     """
#     if not file.filename.lower().endswith(".pdf"):
#         raise HTTPException(status_code=400, detail="Only PDF files are supported")

#     # Save to temp path
#     pdf_path = UPLOAD_DIR / file.filename
#     with pdf_path.open("wb") as f:
#         content = await file.read()
#         f.write(content)

#     # OCR if necessary (this is the file used for text extraction + preview)
#     processed_pdf = ocr_pdf_if_needed(str(pdf_path))
#     processed_name = Path(processed_pdf).name

#     # Chunk + process
#     chunks = create_chunks(processed_pdf)
#     print(f"✅ Total chunks created: {len(chunks)}")

#     compliance_table = process_chunks(chunks)
#     print(f"✅ Total compliance entries: {len(compliance_table)}")

#     # Map to ExportRow for hierarchical splitting
#     export_rows: List[ExportRow] = []
#     rephrased_by_req_id = {}

#     for rec in compliance_table:
#         req_id = rec.get("requirement_id") or ""
#         chunk_id = rec.get("chunk_id") or ""
#         original_text = rec.get("original_chunk", "") or ""
#         compliant = str(rec.get("compliant", "No"))
#         mandatory_optional = rec.get("mandatory_optional", "") or ""
#         confidence = float(rec.get("confidence", 0.0) or 0.0)
#         rephrased = rec.get("rephrased_requirement", "") or ""

#         rephrased_by_req_id[req_id] = rephrased

#         export_rows.append(
#             ExportRow(
#                 id=req_id,
#                 chunkId=chunk_id,
#                 outlineNumber=None,
#                 text=original_text,
#                 rephrasedRequirement=rephrased,
#                 compliant=compliant,
#                 mandatoryOptional=mandatory_optional,
#                 confidence=confidence,
#             )
#         )

#     # Hierarchical split (same logic used later for export)
#     hierarchical_rows: List[HierarchicalRowOut] = build_hierarchical_rows_for_frontend(export_rows)

#     # Build ComplianceRowOut from hierarchical rows
#     rows_out: List[ComplianceRowOut] = []
#     for hr in hierarchical_rows:
#         rephrased = rephrased_by_req_id.get(hr.requirementId, "")
#         rows_out.append(
#             ComplianceRowOut(
#                 id=f"{hr.requirementId}_{hr.outlineNumber}",
#                 chunkId=hr.chunkId,
#                 outlineNumber=hr.outlineNumber,
#                 text=hr.text,
#                 rephrasedRequirement=rephrased,
#                 compliant=hr.compliant,
#                 mandatoryOptional=hr.mandatoryOptional or "",
#                 confidence=hr.confidence or 0.0,
#             )
#         )

#     return RunResponse(
#         language="english",
#         rows=rows_out,
#         splitRows=hierarchical_rows,
#         pdfFileName=processed_name,  # frontend uses /api/compliance/pdf/{pdfFileName}
#     )


# @app.post("/api/compliance/split", response_model=SplitResponse)
# async def split_rows(payload: SplitRequest):
#     """
#     Receive selected rows from frontend and return them split hierarchically.
#     Useful if the user selects a subset of rows to re-split.
#     """
#     if not payload.rows:
#         raise HTTPException(status_code=400, detail="No rows provided")

#     hierarchical_rows = build_hierarchical_rows_for_frontend(payload.rows)
#     return SplitResponse(rows=hierarchical_rows)


# @app.post("/api/compliance/export-xlsx")
# async def export_xlsx(payload: ExportRequest):
#     """
#     Export EXACTLY the rows that the frontend sends (already split and ordered).
#     No additional hierarchical splitting is done here.

#     We:
#     - Use the same outlineNumber as in the frontend.
#     - Infer a simple 'level' from the outlineNumber (number of dots), only for display.
#     """
#     if not payload.rows:
#         raise HTTPException(status_code=400, detail="No rows provided")

#     rows_out = []

#     for r in payload.rows:
#         outline = r.outlineNumber or ""

#         # Infer level from outlineNumber: "1" -> 0, "1.1" -> 1, "1.1.1" -> 2, etc.
#         level = 0
#         if outline:
#             parts = [p for p in str(outline).split(".") if p != ""]
#             level = max(len(parts) - 1, 0)

#         rows_out.append(
#             {
#                 "chunk_id": r.chunkId,
#                 "requirement_id": r.id,
#                 "outline_number": outline,
#                 "level": level,
#                 "text": r.text,
#                 "compliant": r.compliant,
#                 "mandatory_optional": r.mandatoryOptional or "",
#                 "confidence": float(r.confidence or 0.0),
#             }
#         )

#     df = pd.DataFrame(
#         rows_out,
#         columns=[
#             "chunk_id",
#             "requirement_id",
#             "outline_number",
#             "level",
#             "text",
#             "compliant",
#             "mandatory_optional",
#             "confidence",
#         ],
#     )

#     if not df.empty:
#         df["sort_key"] = df["outline_number"].apply(numeric_sort_key)
#         df = df.sort_values(by=["sort_key"], kind="mergesort").drop(columns=["sort_key"])

#     # Write to in-memory buffer
#     output = io.BytesIO()
#     excel_saved = False

#     # Try xlsxwriter first, then openpyxl
#     for engine in ("xlsxwriter", "openpyxl"):
#         try:
#             with pd.ExcelWriter(output, engine=engine) as writer:
#                 df.to_excel(writer, index=False, sheet_name="Compliance")
#                 if engine == "xlsxwriter":
#                     wb = writer.book
#                     ws = writer.sheets["Compliance"]
#                     wrap = wb.add_format({"text_wrap": True, "valign": "top"})
#                     ws.set_column("A:A", 15, wrap)   # chunk_id
#                     ws.set_column("B:B", 20, wrap)   # requirement_id
#                     ws.set_column("C:C", 15, wrap)   # outline_number
#                     ws.set_column("D:D", 8, wrap)    # level
#                     ws.set_column("E:E", 100, wrap)  # text
#                     ws.set_column("F:F", 12, wrap)   # compliant
#                     ws.set_column("G:G", 18, wrap)   # mandatory_optional
#                     ws.set_column("H:H", 12, wrap)   # confidence
#             excel_saved = True
#             break
#         except Exception as e:
#             print(f"⚠️ Failed to write Excel with engine {engine}: {e}")
#             output.seek(0)
#             output.truncate(0)
#             continue

#     if not excel_saved:
#         raise HTTPException(
#             status_code=500,
#             detail="Unable to save Excel (install xlsxwriter or openpyxl)",
#         )

#     output.seek(0)

#     safe_name = re.sub(r"[^\w\-]+", "_", payload.fileName.replace(".pdf", ""))
#     filename = f"{safe_name}_compliance.xlsx"

#     headers = {
#         "Content-Disposition": f'attachment; filename="{filename}"'
#     }

#     return StreamingResponse(
#         output,
#         media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
#         headers=headers,
#     )


# @app.post("/api/compliance/highlight", response_model=HighlightResponse)
# async def highlight_chunk(payload: HighlightRequest):
#     """
#     Given a chunk text, create an annotated PDF with that chunk highlighted
#     and return the new PDF file name + first page number where it appears.
#     The frontend then loads /api/compliance/pdf/{pdfFileName}.
#     """
#     base_pdf_path = UPLOAD_DIR / payload.fileName

#     if not base_pdf_path.exists():
#         raise HTTPException(status_code=404, detail="Base PDF not found")

#     safe_req = (payload.requirementId or "sel").replace("/", "_")
#     annotated_name = f"{base_pdf_path.stem}_annot_{safe_req}.pdf"
#     annotated_path = UPLOAD_DIR / annotated_name

#     try:
#         doc = fitz.open(base_pdf_path)
#     except Exception as e:
#         print("❌ Error opening PDF for annotation:", e)
#         raise HTTPException(status_code=500, detail="Cannot open PDF")

#     text_to_find = payload.chunkText.strip()
#     if len(text_to_find) > 400:
#         text_to_find = text_to_find[:400]

#     first_page_num: Optional[int] = None
#     total_hits = 0

#     for page_index in range(len(doc)):
#         page = doc[page_index]
#         rects = page.search_for(text_to_find)

#         if not rects:
#             continue

#         if first_page_num is None:
#             first_page_num = page_index + 1  # 1-based for UI

#         for r in rects:
#             hl = page.add_highlight_annot(r)
#             hl.update()

#         total_hits += len(rects)

#     if total_hits == 0:
#         doc.close()
#         # No match found – just return original PDF and page 1
#         return HighlightResponse(pdfFileName=payload.fileName, pageNumber=1)

#     try:
#         doc.save(str(annotated_path), incremental=False, garbage=4)
#     finally:
#         doc.close()

#     return HighlightResponse(
#         pdfFileName=annotated_name,
#         pageNumber=first_page_num or 1,
#     )


# # ============================================================
# # Entry point
# # ============================================================

# if __name__ == "__main__":
#     import uvicorn

#     uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
#################################
#################################

# backend/main.py

#################################
#################################

# backend/main.py
from urllib.parse import quote
import io
import os
import re
import json
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

import pdfplumber
import pandas as pd
from PyPDF2 import PdfReader
import fitz  # PyMuPDF
import ollama
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel

# ============================================================
# FastAPI app + CORS
# ============================================================

app = FastAPI(title="Compliance Table Backend", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path("temp_uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

# ============================================================
# Shared regex / helpers (Arabic detection)
# ============================================================

ARABIC_CHARS_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")
LETTER_RE = re.compile(r"[\w\u0600-\u06FF]", re.UNICODE)


def has_arabic(text: str) -> bool:
    return bool(ARABIC_CHARS_RE.search(text or ""))


# ============================================================
# Serve PDFs for frontend preview
# ============================================================

@app.get("/api/compliance/pdf/{filename}")
async def get_pdf(filename: str):
    """
    Serve a PDF (original or OCR/annotated) from temp_uploads so the frontend
    can preview it using /api/compliance/pdf/{filename}.
    """
    pdf_path = UPLOAD_DIR / filename

    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF not found")

    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=pdf_path.name,
    )


# ============================================================
# ---------------- ENGLISH PIPELINE --------------------------
# ============================================================

# ---------- Scanned Detection + OCR ----------

def is_scanned_pdf(pdf_path: str, min_text_threshold: int = 50) -> bool:
    """
    Roughly determine if a PDF is scanned (image-based) by checking how much
    extractable text it has.
    If total text length < min_text_threshold => likely scanned.
    """
    try:
        reader = PdfReader(pdf_path)
        total_text_len = 0

        for page in reader.pages:
            txt = page.extract_text() or ""
            total_text_len += len(txt.strip())
            if total_text_len >= min_text_threshold:
                return False

        return True

    except Exception as e:
        print(f"⚠️ Could not inspect PDF for text, assuming NOT scanned. Error: {e}")
        return False


def ocr_pdf_if_needed(pdf_path: str) -> str:
    """
    If the PDF appears to be scanned (image-only), run OCR using ocrmypdf and
    return the path to the OCR-processed PDF. Otherwise, return original path.
    """
    if not is_scanned_pdf(pdf_path):
        print("✅ Detected searchable PDF (no OCR needed).")
        return pdf_path

    print("🔎 Detected scanned / image-only PDF. Running OCR with ocrmypdf...")

    base, ext = os.path.splitext(pdf_path)
    ocr_output = f"{base}_ocr{ext}"

    try:
        subprocess.run(
            ["ocrmypdf", "--force-ocr", "--deskew", pdf_path, ocr_output],
            check=True,
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


# ---------- Helper Functions for EN text / chunks ----------

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
    chunks: List[str] = []
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

    chunks: List[dict] = []
    for section_index, section in enumerate(sections, start=1):
        paragraphs = split_paragraphs(section)
        if not paragraphs:
            continue

        for para in paragraphs:
            if not is_meaningful(para):
                continue

            if len(para.split()) > 1500:
                window_chunks = sliding_window_split(para, max_words=1500, overlap=200)
                for wc in window_chunks:
                    chunks.append(
                        {
                            "chunk_id": f"Chunk_{len(chunks)+1:03d}",
                            "text": wc,
                        }
                    )
            else:
                chunks.append(
                    {
                        "chunk_id": f"Chunk_{len(chunks)+1:03d}",
                        "text": para,
                    }
                )
    return chunks


# ---------- Robust LLM JSON parsing + call (EN) ----------

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
            text = inner

    # 3) Try every {...} block as a candidate JSON object
    candidates = re.findall(r"\{.*?\}", text, re.DOTALL)
    for c in candidates:
        c = c.strip()
        try:
            return json.loads(c)
        except Exception:
            continue

    # 4) If multiple objects are glued together, split after each }
    parts = re.split(r"(?<=\})\s*(?=\{)", text)
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
            options={"temperature": 0.2},
        )

        # Extract text content
        if isinstance(response, dict) and "message" in response and "content" in response["message"]:
            text = response["message"]["content"]
        elif hasattr(response, "message") and hasattr(response.message, "content"):
            text = response.message.content
        else:
            text = str(response)

        print("\n--- RAW LLM RESPONSE (truncated, EN) ---")
        print(text[:300].strip(), "...\n")

        data = _parse_llm_json(text)

        compliant = data.get("compliant", "No")
        rephrased = data.get("rephrased_requirement", "")
        confidence = data.get("confidence", 0.5)

        if isinstance(rephrased, list):
            rephrased = " ".join(
                r.strip() for r in rephrased if isinstance(r, str) and r.strip()
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
        print("❌ Error calling LLaMA (english):", e)
        return {
            "compliant": "No",
            "rephrased_requirement": "",
            "confidence": 0.0,
        }


def process_chunks(chunks: List[dict]) -> List[dict]:
    """
    Returns the "compliance_table" structure for English pipeline
    """
    compliance_table: List[dict] = []

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
   - Example: "compliant": "No", "confidence": 0.95
   - Example: "compliant": "Yes", "confidence": 0.55
5. Output strictly in JSON with no markdown, no explanations, no extra text.
6. Your JSON must have exactly these fields:
   - compliant
   - rephrased_requirement
   - confidence
7. Return EXACTLY ONE JSON object and nothing else.
"""
        response = call_llm(prompt)
        print(f"{chunk['chunk_id']} -> {response}")

        req = response.get("rephrased_requirement", "")
        if isinstance(req, list):
            req = " ".join(r.strip() for r in req if isinstance(r, str) and r.strip())

        compliant = response.get("compliant", "No")
        confidence = float(response.get("confidence", 0.0))

        compliance_table.append(
            {
                "requirement_id": f"RFP_{len(compliance_table)+1:03d}",
                "rephrased_requirement": req,
                "chunk_id": chunk["chunk_id"],
                "original_chunk": chunk["text"],
                "mandatory_optional": "",
                "compliant": compliant,
                "reference_evidence": "",
                "notes": "",
                "confidence": confidence,
            }
        )

    return compliance_table


# ============================================================
# ---------------- ARABIC PIPELINE ---------------------------
# ============================================================

def detect_arabic_pdf(pdf_path: str, sample_pages: int = 3) -> bool:
    """
    Detect if a PDF is primarily Arabic by sampling the first few pages.
    """
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
        print(f"📊 Language detection: {ratio:.1%} Arabic")
        return ratio >= 0.15
    except Exception as e:
        print(f"⚠️ Detection error: {e}")
        return False


def fix_reversed_arabic(text: str) -> str:
    """
    Fix reversed Arabic lines as produced by some PDF extractors.
    Same logic you had in your final.py.
    """
    if not text or not has_arabic(text):
        return text

    fixed_lines: List[str] = []

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            fixed_lines.append("")
            continue

        if has_arabic(stripped):
            words = stripped.split()
            reversed_letters = [w[::-1] if has_arabic(w) else w for w in words]
            corrected = list(reversed(reversed_letters))
            fixed_lines.append(" ".join(corrected))
        else:
            fixed_lines.append(stripped)

    return "\n".join(fixed_lines)


def extract_text_best(pdf_path: str) -> str:
    """
    Try multiple text extraction methods (pdfplumber, PyMuPDF) and choose the
    one that returns the most letters (Arabic + Latin).
    """
    methods: List[Tuple[str, str]] = []

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
        print(f"⚠️ pdfplumber failed: {e}")

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
        print(f"⚠️ PyMuPDF failed: {e}")

    if not methods:
        raise RuntimeError("All extraction methods failed!")

    scored = [(name, txt, len(LETTER_RE.findall(txt))) for name, txt in methods]
    scored.sort(key=lambda x: x[2], reverse=True)

    best_method, best_text, score = scored[0]
    print(f"✅ Extraction method: {best_method} ({score:,} letters)")
    return best_text


def process_pdf_arabic(pdf_path: str) -> Tuple[str, bool]:
    """
    End-to-end Arabic PDF text extraction:
    - detect Arabic ratio
    - extract text with best method
    - fix reversed Arabic if needed
    Returns (text, is_arabic_pdf_flag)
    """
    print(f"\n{'='*70}")
    print(f"📄 Processing (arabic detector): {Path(pdf_path).name}")
    print(f"{'='*70}\n")

    is_arabic = detect_arabic_pdf(pdf_path)
    raw_text = extract_text_best(pdf_path)

    if not raw_text.strip():
        print("⚠️ No text extracted!")
        return "", is_arabic

    if is_arabic:
        print("🔧 Fixing reversed Arabic text where needed...")
        fixed_text = fix_reversed_arabic(raw_text)
        return fixed_text, True

    return raw_text, False


def clean_text_generic(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"\.{3,}", " ", text)
    text = re.sub(r"(?m)^[\t ]*\d+[\t ]*$", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[\t ]{2,}", " ", text)
    text = re.sub(r"[\u200b-\u200f\ufeff]", "", text)
    return text.strip()


def is_meaningful_generic(text: str, min_words: int = 5) -> bool:
    if not text:
        return False
    words = re.split(r"\s+", text.strip())
    words = [w for w in words if w]
    if len(words) < min_words:
        return False
    letter_count = len(LETTER_RE.findall(text))
    return letter_count >= min_words


# AR_NUM_PREFIX_RE = re.compile(r"(?<!\n)\s*-\s*(\d{1,3})\s+")
# AR_NUM_DOT_RE    = re.compile(r"(?<!\n)\s*(\d{1,3})\s*[\.\)]\s+")
# AR_CHAIN_RE      = re.compile(r"(?<!\n)\s*((?:\d+\.)+\d+)\s+")
# AR_SECTION_RE    = re.compile(r"(?<!\n)\s*(\d+\.0)\s*(?:[.)])?\s+")

# AR_NUM_PREFIX_RE = re.compile(r"(?<!\n)\s*-\s*(\d{1,3})\s+")
# AR_NUM_DOT_RE    = re.compile(r"(?<!\n)\s*(\d{1,3})\s*[\.\)]\s+")
# AR_CHAIN_RE      = re.compile(r"(?<!\n)\s*((?:\d+\.)+\d+)\s+")
# AR_SECTION_RE    = re.compile(r"(?<!\n)\s*(\d+\.0)\s*(?:[.)])?\s+")
# # Handles ".1", ".2" style OCR bullets (dot then number)
# AR_DOTNUM_RE = re.compile(r"(?m)(?<!\n)\s*\.\s*(\d{1,3})\s+")
# # Also handle Arabic-Indic digits if OCR outputs ١٢٣
# AR_DOTNUM_ARABIC_INDIC_RE = re.compile(r"(?m)(?<!\n)\s*\.\s*([٠-٩]{1,3})\s+")
# AR_TOPLEVEL_NUM_RE = re.compile(r"(?m)(?<!\n)\s*(\d{1,2})\s+(?=\S)")

# # 2) OCR bullets like: ".1 ..." ".2 ..."  (also supports Arabic-Indic digits .١ .٢)
# AR_DOTNUM_RE = re.compile(r"(?m)(?<!\n)\s*\.\s*([0-9]{1,3}|[٠-٩]{1,3})\s+")

# # 3) Arabic letter bullets: "أ-" "ب-" "ج-" ...
# Hyphen-number bullets: -1, -2 ...
AR_NUM_PREFIX_RE = re.compile(r"(?m)(?<!\n)\s*-\s*(\d{1,3})\s+")

# Number-dot/paren bullets: 1.  /  1)
AR_NUM_DOT_RE = re.compile(r"(?m)(?<!\n)\s*(\d{1,3})\s*[\.\)]\s+")

# Chained numbering: 4.1.2 ...
AR_CHAIN_RE = re.compile(r"(?m)(?<!\n)\s*((?:\d+\.)+\d+)\s+")

# Section-like numbers: 3.0
AR_SECTION_RE = re.compile(r"(?m)(?<!\n)\s*(\d+\.0)\s*(?:[.)])?\s+")

# OCR dot-number bullets: .1 or .١ or .٢ ...
AR_DOTNUM_RE = re.compile(r"(?m)(?<!\n)\s*\.\s*([0-9]{1,3}|[٠-٩]{1,3})\s+")

# Arabic letter bullets: أ- ب- ج- ...
AR_LETTER_BULLET_RE = re.compile(r"(?m)(?<!\n)\s*([أ-ي])\s*-\s+")


# def inject_newlines_for_numbered_lists(text: str) -> str:
#     if not text:
#         return ""

#     t = text.replace("\r\n", "\n").replace("\r", "\n")
#     t = re.sub(r"[\u200b-\u200f\ufeff]", "", t)
#     t = re.sub(r"[ \t]{2,}", " ", t)

#     # Inject newline before "-1"
#     t = AR_NUM_PREFIX_RE.sub(r"\n-\1 ", t)

#     # Inject newline before "1." / "1)"
#     t = AR_NUM_DOT_RE.sub(r"\n\1. ", t)

#     # Inject newline before ".1" (OCR bullet style)
#     t = AR_DOTNUM_RE.sub(r"\n.\1 ", t)
#     t = AR_DOTNUM_ARABIC_INDIC_RE.sub(r"\n.\1 ", t)

#     # Inject newline before chained numbering "4.1.2"
#     t = AR_CHAIN_RE.sub(r"\n\1 ", t)

#     # Inject newline before section-like "3.0"
#     t = AR_SECTION_RE.sub(r"\n\1 ", t)

#     t = re.sub(r"\n{3,}", "\n\n", t).strip()
#     return t
def inject_newlines_for_numbered_lists(text: str) -> str:
    """
    Normalize OCR-style Arabic chunks that come as one long line by injecting
    newlines before common list / section markers.

    Handles:
    - -1 -2 ...
    - 1. / 1)
    - .1 / .١ / .٢ ...
    - 4.1.2 ...
    - 3.0 ...
    - top-level headings like: "3 أهلية ..." (guarded to avoid dates like 21/4/1441هـ)
    - أ- ب- ...
    """
    if not text:
        return ""

    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[\u200b-\u200f\ufeff]", "", t)   # remove zero-width chars
    t = re.sub(r"[ \t]{2,}", " ", t).strip()

    # ------------------------------------------------------------------
    # 1) Top-level headings: " 3 أهلية ..." (but avoid dates 21/4/1441هـ)
    # We only split when number is followed by Arabic letters.
    # ------------------------------------------------------------------
    t = re.sub(
        r"(?m)(?<!\n)\s+(\d{1,2})\s+(?=[\u0600-\u06FF])",
        r"\n\1 ",
        t,
    )

    # ------------------------------------------------------------------
    # 2) Lists / bullets
    # ------------------------------------------------------------------
    # -1, -2, ...
    t = AR_NUM_PREFIX_RE.sub(r"\n-\1 ", t)

    # 1.  or  1)
    t = AR_NUM_DOT_RE.sub(r"\n\1. ", t)

    # .1  or  .١  (OCR)
    t = AR_DOTNUM_RE.sub(r"\n.\1 ", t)

    # أ- ب- ج- ...
    t = AR_LETTER_BULLET_RE.sub(r"\n\1- ", t)

    # 4.1.2 ...
    t = AR_CHAIN_RE.sub(r"\n\1 ", t)

    # 3.0 ...
    t = AR_SECTION_RE.sub(r"\n\1 ", t)

    # ------------------------------------------------------------------
    # 3) Cleanup
    # ------------------------------------------------------------------
    # compress excessive newlines
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t




# def create_chunks_arabic(pdf_path: str) -> List[dict]:
#     """
#     Arabic-specific chunking using your production logic:
#     - process_pdf_arabic (best extractor + reverse fix)
#     - clean_text_generic
#     - inject newlines for OCR numbered lists / bullets (fix "one long line")
#     - split into paragraphs
#     - keep only meaningful ones
#     """
#     text, is_arabic_pdf = process_pdf_arabic(pdf_path)
#     if not text:
#         return []

#     text = clean_text_generic(text)
#     text = inject_newlines_for_numbered_lists(text)

# # ✅ If we now have list items, split by lines that start with "-<num>"
#     if re.search(r"(?m)^\s*(?:-\d+|\.\d+|\d+[.)])\s+", text) or re.search(r"(?m)^\s*\.[٠-٩]+", text):
#         paragraphs = [p.strip() for p in re.split(r"(?m)(?=^\s*(?:-\d+|\.\d+|\d+[.)])\s+|^\s*\.[٠-٩]+)", text) if p.strip()]
#     else:
#         paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

#     chunks: List[dict] = []
#     for para in paragraphs:
#         if is_meaningful_generic(para):
#             chunks.append(
#                 {
#                     "chunk_id": f"Chunk_{len(chunks)+1:03d}",
#                     "text": para,
#                     "word_count": len(para.split()),
#                     "is_arabic": is_arabic_pdf,
#                 }
#             )

#     print(f"✅ Created {len(chunks)} meaningful Arabic chunks")
#     return chunks
def create_chunks_arabic(pdf_path: str) -> List[dict]:
    """
    Arabic-specific chunking:
    - process_pdf_arabic (best extractor + reverse fix)
    - clean_text_generic
    - inject newlines for OCR numbered lists / bullets / headings
    - split into items using start-markers (English-like behavior)
    - keep only meaningful ones
    """
    text, is_arabic_pdf = process_pdf_arabic(pdf_path)
    if not text:
        return []

    text = clean_text_generic(text)
    text = inject_newlines_for_numbered_lists(text)

    # Split on ANY "item start" marker (lookahead keeps the marker with the item)
    item_start_re = re.compile(
        r"(?m)(?=^\s*(?:"
        r"-\d{1,3}\s+"                              # -1
        r"|[0-9]{1,3}[.)]\s+"                       # 1. or 1)
        r"|\.\s*(?:[0-9]{1,3}|[٠-٩]{1,3})\s+"       # .1 or .١
        r"|[أ-ي]\s*-\s+"                            # أ- ب-
        r"|\d{1,2}\s+[\u0600-\u06FF]"               # 1 تعريفات / 2 تعريف...
        r"|(?:\d+\.)+\d+\s+"                        # 4.1.2
        r"|\d+\.0\s+"                               # 3.0
        r"))"
    )

    parts = [p.strip() for p in item_start_re.split(text) if p and p.strip()]

    # If split produced nothing useful (edge case), fall back to paragraph split
    if not parts:
        parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    chunks: List[dict] = []
    for para in parts:
        if is_meaningful_generic(para):
            chunks.append(
                {
                    "chunk_id": f"Chunk_{len(chunks)+1:03d}",
                    "text": para,
                    "word_count": len(para.split()),
                    "is_arabic": is_arabic_pdf,
                }
            )

    print(f"✅ Created {len(chunks)} meaningful Arabic chunks")
    return chunks


SYSTEM_PROMPT_AR = """
You are a Compliance Analyst at an IT Location Services firm.
You read sections of an RFP (often in Arabic) and decide whether they contain
mandatory technical requirements relevant to IT / systems / solution design.
if it is related to bidders submission process , bidding process , bidding competition , bidding compaitability , bidders eligibility ,bidders relationship , bidders and regulations (import systems), bid pricing process ,  Contracts and contractors ,bidding documents , bidding Regulations or governemnt regulations or anything related to bidders and governements not  like that label it with **No**.

You must always answer with exactly one JSON object with the fields:
- compliant: "Yes" or "No"
- rephrased_requirement: short English summary (or empty string if No)
- confidence: float between 0.0 and 1.0
"""

def safe_parse_llm_json_ar(text: str) -> dict:
    text = (text or "").strip()
    try:
        return json.loads(text)
    except Exception:
        pass

    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        candidate = m.group(0).strip()
        try:
            return json.loads(candidate)
        except Exception:
            pass

    return {"compliant": "No", "rephrased_requirement": "", "confidence": 0.2}


def normalize_compliant(v) -> str:
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, str):
        return "Yes" if v.strip().lower() in ["yes", "y", "true", "1", "نعم", "yes."] else "No"
    if isinstance(v, (int, float)):
        return "Yes" if v != 0 else "No"
    return "No"


def ask_llm_arabic(text: str) -> dict:
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
    try:
        response = ollama.chat(
            model="llama3.1",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT_AR},
                {"role": "user", "content": user_prompt},
            ],
            options={"temperature": 0.0, "format": "json"},
        )

        if isinstance(response, dict) and "message" in response and "content" in response["message"]:
            content = response["message"]["content"]
        elif hasattr(response, "message") and hasattr(response.message, "content"):
            content = response.message.content
        else:
            content = str(response)

        print("── LLM RAW JSON (arabic) ──")
        print(content[:300])
        print("──────────────────────────\n")

        try:
            data = json.loads(content)
        except Exception:
            data = safe_parse_llm_json_ar(content)

        compliant = normalize_compliant(data.get("compliant", "No"))
        rephrased = data.get("rephrased_requirement", "")
        confidence = float(data.get("confidence", 0.5))
        return {
            "compliant": compliant,
            "rephrased_requirement": rephrased,
            "confidence": confidence,
        }
    except Exception as e:
        print("❌ Error calling LLaMA (arabic):", e)
        return {
            "compliant": "No",
            "rephrased_requirement": "",
            "confidence": 0.0,
        }


def process_chunks_arabic(chunks: List[dict]) -> List[dict]:
    out: List[dict] = []
    for ch in chunks:
        r = ask_llm_arabic(ch["text"])
        out.append(
            {
                "requirement_id": f"RFP_{len(out)+1:03d}",
                "chunk_id": ch["chunk_id"],
                "original_chunk": ch["text"],
                "rephrased_requirement": r["rephrased_requirement"],
                "compliant": r["compliant"],
                "mandatory_optional": "",
                "reference_evidence": "",
                "notes": "",
                "confidence": r["confidence"],
            }
        )
    return out


# ============================================================
# -------- MODELS used by API / frontend ---------------------
# ============================================================

class ComplianceRowOut(BaseModel):
    id: str
    chunkId: str
    outlineNumber: str
    text: str
    rephrasedRequirement: str
    compliant: str
    mandatoryOptional: Optional[str] = ""
    confidence: Optional[float] = 0.0


class HierarchicalRowOut(BaseModel):
    """Model for hierarchically split rows shown in frontend"""
    id: str
    chunkId: str
    requirementId: str
    outlineNumber: str
    level: int
    text: str
    compliant: str
    mandatoryOptional: Optional[str] = ""
    confidence: Optional[float] = 0.0


class RunResponse(BaseModel):
    language: str  # "english" | "arabic"
    rows: List[ComplianceRowOut]          # already hierarchical
    splitRows: List[HierarchicalRowOut]   # same hierarchy but with level
    pdfFileName: str                      # name of PDF to preview via /api/compliance/pdf/{pdfFileName}


class ExportRow(BaseModel):
    id: str
    chunkId: str
    outlineNumber: Optional[str] = None
    text: str
    rephrasedRequirement: Optional[str] = ""
    compliant: str
    mandatoryOptional: Optional[str] = ""
    confidence: Optional[float] = 0.0


class ExportRequest(BaseModel):
    fileName: str
    rows: List[ExportRow]


class SplitRequest(BaseModel):
    """Request to split selected rows into hierarchical structure"""
    rows: List[ExportRow]


class SplitResponse(BaseModel):
    """Response containing hierarchically split rows"""
    rows: List[HierarchicalRowOut]


class HighlightRequest(BaseModel):
    fileName: str          # PDF filename (e.g. from pdfFileName)
    chunkText: str         # text to search and highlight
    requirementId: Optional[str] = None   # optional, for logging/debug


class HighlightResponse(BaseModel):
    pdfFileName: str       # annotated PDF filename
    pageNumber: Optional[int] = None      # first page where highlighted text was found


# ============================================================
# -------------- HIERARCHICAL SPLIT HELPERS ------------------
# ============================================================

pd.set_option("display.max_colwidth", None)

BULLET_CHARS = (
    r"\-"      # dash
    "\u2022"   # •
    "\u25CF"   # ●
    "\u25E6"   # ◦
    "\u25AA"   # ▪
    "\u25AB"   # ▫
    "\u2043"   # ⁃
    "\u2219"   # ∙
    "\u00B7"   # ·
    "\u2023"   # ‣
    "\u204C"   # ⁌
    "\u204D"   # ⁍
    "\u2218"   # ∘
    "\u25C9"   # ◉
    "\u25CB"   # ○
    "\u25A0"   # ■
    "\u25A1"   # □
    "\u25B6"   # ▶
    "\u25B8"   # ▸
    "\uF0B7"   # Word bullet ()
    "\u066d"   # ٭
    "\u06d4"   # ۔
    "\u06dd"   # ۝
    "\u06de"   # ۞
)

BULLET_RE = re.compile(
    rf"""
        ^
        (?P<indent>\s*)
        (?:
            [\-\*{BULLET_CHARS}]
          | \d+[.\)]
          | [A-Za-z][.\)]
        )
        \s+
        (?P<text>.*\S.*)
    $
    """,
    re.VERBOSE,
)

CHAIN_RE = re.compile(r"^\s*((?:\d+\.)+\d+)\s+(.*\S.*)$")
INLINE_SPLIT_REGEX = re.compile(rf"(?:[\s\.]+) ([{BULLET_CHARS}]) \s*", re.VERBOSE)


def normalize_indent(s: str, tabsize: int = 4) -> int:
    return len(s.expandtabs(tabsize))


def is_section_header(line: str) -> bool:
    return bool(re.match(r"^\s*\d+\.0(?:[.)])?\s+\S", line))


def parse_chain(line: str):
    m = CHAIN_RE.match(line)
    if not m:
        return None, line
    chain_str, rest = m.group(1), m.group(2)
    parts = tuple(int(p) for p in chain_str.split("."))
    if len(parts) == 2 and parts[1] == 0:
        return None, line
    return parts, rest


def split_inline_bullets(line: str):
    s = line.strip()
    if not s:
        return []

    parts = INLINE_SPLIT_REGEX.split(s)
    if len(parts) == 1:
        return []

    out = []
    if parts[0].strip():
        out.append(("prefix", parts[0].strip()))

    for i in range(1, len(parts), 2):
        if i + 1 < len(parts):
            segment_text = parts[i + 1].strip()
            if segment_text:
                out.append(("bullet", segment_text))

    return out


def extract_hierarchical_items(chunk_text: str):
    """
    Take raw chunk text and:
    - insert virtual newlines for section numbers (3.0., 4.1., etc.)
      and inline bullets (•, , etc.) when the PDF came as one long line.
    - then run header / bullet / chain logic line by line.
    """
    text = str(chunk_text).replace("\r\n", "\n")

    # If text is basically one long line, inject newlines
    if text.count("\n") < 3:
        # Newline before section-like numbers: 3.0., 4.1, 4.1.2, etc.
        text = re.sub(
            r"(?<!^)(?<!\n)(\s+)((?:\d+\.0|\d+\.\d+(?:\.\d+)*)(?:[.)])?\s+)",
            r"\n\2",
            text,
        )

        # Newline before any bullet char (including Word's )
        bullet_class = BULLET_CHARS
        text = re.sub(
            rf"\s*([{bullet_class}])\s*",
            r"\n\1 ",
            text,
        )

    lines = [ln.rstrip("\r\n") for ln in text.splitlines()]
    header_text = None
    items = []

    def append_to_last(t: str):
        if not items:
            items.append((0, None, t.strip()))
            return
        ind, hint, prev = items[-1]
        joiner = "" if prev.endswith("-") else " "
        items[-1] = (ind, hint, (prev + joiner + t).strip())

    for ln in lines:
        if not ln.strip():
            continue

        # Section headers like "3.0. Objective ..."
        if is_section_header(ln):
            m = re.match(r"^\s*\d+\.0(?:[.)])?\s+(.+)$", ln)
            if m:
                header_text = m.group(1).strip()
            continue

        # Number chains like "4.1", "4.1.2", ...
        chain_parts, rest = parse_chain(ln)
        if chain_parts:
            first_num = str(chain_parts[0])
            indent_guess = normalize_indent(ln[: ln.find(first_num)])
            items.append((indent_guess, chain_parts, rest.strip()))
            continue

        # Bullets at line start
        m = BULLET_RE.match(ln)
        if m:
            indent = normalize_indent(m.group("indent"))
            text_part = m.group("text").strip()

            chunks = split_inline_bullets(text_part)
            if chunks:
                if chunks[0][0] == "prefix" and chunks[0][1].strip():
                    items.append((indent, None, chunks[0][1].strip()))
                    chunks = chunks[1:]
                for _, seg in chunks:
                    if seg.strip():
                        items.append((indent + 2, None, seg.strip()))
            else:
                items.append((indent, None, text_part))
            continue

        # Fallback: inline bullets inside a normal line
        chunks = split_inline_bullets(ln)
        if chunks:
            if chunks[0][0] == "prefix":
                if items:
                    append_to_last(chunks[0][1])
                else:
                    items.append((0, None, chunks[0][1].strip()))
                chunks = chunks[1:]
            for _, seg in chunks:
                items.append((0, None, seg.strip()))
        else:
            append_to_last(ln.strip())

    return header_text, items


def build_hierarchy(base_num: str, header_text: Optional[str], items, min_indent_step: int = 2):
    rows = []

    if header_text:
        rows.append(
            {
                "outline_number": base_num,
                "level": 0,
                "text": header_text,
            }
        )

    if not items:
        return rows

    counters: List[int] = []
    indent_levels = [items[0][0]]

    def ensure_level(level: int):
        nonlocal counters
        while len(counters) < level:
            counters.append(0)
        while len(counters) > level:
            counters.pop()

    for indent, level_hint, text in items:
        if level_hint:
            # explicit numeric chain
            level = len(level_hint)
            ensure_level(level)
            counters[-1] += 1
        else:
            # infer from indentation
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
        rows.append(
            {
                "outline_number": f"{base_num}.{suffix}",
                "level": level,
                "text": text,
            }
        )

    return rows


def numeric_sort_key(s):
    if s is None:
        return ()
    parts = str(s).split(".")
    return tuple(int(p) if p.isdigit() else 0 for p in parts)


def build_hierarchical_df_from_rows(rows_in: List[ExportRow]) -> pd.DataFrame:
    rows_out = []
    chunk_index = 0

    for record in rows_in:
        chunk_index += 1
        base_num = str(chunk_index)

        chunk_id = record.chunkId or f"Chunk_{chunk_index:03d}"
        chunk_text = record.text or ""
        requirement_id = record.id
        compliant = record.compliant
        confidence = record.confidence
        mandatory_optional = record.mandatoryOptional

        header_text, items = extract_hierarchical_items(chunk_text)

        if not header_text and not items:
            rows_out.append(
                {
                    "chunk_id": chunk_id,
                    "requirement_id": requirement_id,
                    "outline_number": base_num,
                    "level": 0,
                    "text": str(chunk_text).strip(),
                    "compliant": compliant,
                    "mandatory_optional": mandatory_optional,
                    "confidence": confidence,
                }
            )
            continue

        for row in build_hierarchy(base_num, header_text, items):
            row.update(
                {
                    "chunk_id": chunk_id,
                    "requirement_id": requirement_id,
                    "compliant": compliant,
                    "mandatory_optional": mandatory_optional,
                    "confidence": confidence,
                }
            )
            rows_out.append(row)

    df = pd.DataFrame(
        rows_out,
        columns=[
            "chunk_id",
            "requirement_id",
            "outline_number",
            "level",
            "text",
            "compliant",
            "mandatory_optional",
            "confidence",
        ],
    )

    if not df.empty:
        df["sort_key"] = df["outline_number"].apply(numeric_sort_key)
        df = df.sort_values(by=["sort_key"], kind="mergesort").drop(columns=["sort_key"])

    return df


def build_hierarchical_rows_for_frontend(rows_in: List[ExportRow]) -> List[HierarchicalRowOut]:
    """
    Build hierarchical rows for the frontend using the EXACT same logic
    used previously for Excel.
    """
    df = build_hierarchical_df_from_rows(rows_in)
    df = df.reset_index(drop=True)

    rows_out: List[HierarchicalRowOut] = []

    for idx, row in df.iterrows():
        rows_out.append(
            HierarchicalRowOut(
                id=f"{row['requirement_id']}_{idx}",
                chunkId=str(row["chunk_id"]),
                requirementId=str(row["requirement_id"]),
                outlineNumber=str(row["outline_number"]),
                level=int(row["level"]),
                text=str(row["text"] or "").strip(),
                compliant=str(row["compliant"] or "No"),
                mandatoryOptional=str(row.get("mandatory_optional") or ""),
                confidence=float(row.get("confidence") or 0.0),
            )
        )

    return rows_out


# ============================================================
# ----------------------- API ROUTES -------------------------
# ============================================================

@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/compliance/run", response_model=RunResponse)
async def run_compliance(file: UploadFile = File(...)):
    """
    Upload a PDF:
    - Save to temp_uploads
    - Detect language (Arabic vs non-Arabic)
    - Auto OCR if needed for English
    - Chunk
    - Run LLaMA compliance extraction (EN or AR)
    - Split hierarchically BEFORE sending to frontend
    - Return pdfFileName for frontend preview
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    # Save to temp path
    pdf_path = UPLOAD_DIR / file.filename
    with pdf_path.open("wb") as f:
        content = await file.read()
        f.write(content)

    # Language detection on the original PDF (pre-OCR)
    is_arabic = detect_arabic_pdf(str(pdf_path))

    if is_arabic:
        print("🌐 Detected ARABIC RFP → using Arabic pipeline")
        processed_pdf = str(pdf_path)
        language = "arabic"
        chunks = create_chunks_arabic(processed_pdf)
        compliance_table = process_chunks_arabic(chunks)
    else:
        print("🌐 Detected NON-ARABIC RFP → using English pipeline")
        processed_pdf = ocr_pdf_if_needed(str(pdf_path))
        language = "english"
        chunks = create_chunks(processed_pdf)
        print(f"✅ Total EN chunks created: {len(chunks)}")
        compliance_table = process_chunks(chunks)

    processed_name = Path(processed_pdf).name
    print(f"✅ Total compliance entries: {len(compliance_table)}")

    # Map to ExportRow for hierarchical splitting
    export_rows: List[ExportRow] = []
    rephrased_by_req_id: dict = {}

    for rec in compliance_table:
        req_id = rec.get("requirement_id") or ""
        chunk_id = rec.get("chunk_id") or ""
        original_text = rec.get("original_chunk", "") or ""
        compliant = str(rec.get("compliant", "No"))
        mandatory_optional = rec.get("mandatory_optional", "") or ""
        confidence = float(rec.get("confidence", 0.0) or 0.0)
        rephrased = rec.get("rephrased_requirement", "") or ""

        rephrased_by_req_id[req_id] = rephrased

        export_rows.append(
            ExportRow(
                id=req_id,
                chunkId=chunk_id,
                outlineNumber=None,
                text=original_text,
                rephrasedRequirement=rephrased,
                compliant=compliant,
                mandatoryOptional=mandatory_optional,
                confidence=confidence,
            )
        )

    # Hierarchical split (same logic used later for export)
    hierarchical_rows: List[HierarchicalRowOut] = build_hierarchical_rows_for_frontend(export_rows)

    # Build ComplianceRowOut from hierarchical rows
    rows_out: List[ComplianceRowOut] = []
    for hr in hierarchical_rows:
        rephrased = rephrased_by_req_id.get(hr.requirementId, "")
        rows_out.append(
            ComplianceRowOut(
                id=f"{hr.requirementId}_{hr.outlineNumber}",
                chunkId=hr.chunkId,
                outlineNumber=hr.outlineNumber,
                text=hr.text,
                rephrasedRequirement=rephrased,
                compliant=hr.compliant,
                mandatoryOptional=hr.mandatoryOptional or "",
                confidence=hr.confidence or 0.0,
            )
        )

    return RunResponse(
        language=language,
        rows=rows_out,
        splitRows=hierarchical_rows,
        pdfFileName=processed_name,  # frontend uses /api/compliance/pdf/{pdfFileName}
    )


@app.post("/api/compliance/split", response_model=SplitResponse)
async def split_rows(payload: SplitRequest):
    """
    Receive selected rows from frontend and return them split hierarchically.
    Useful if the user selects a subset of rows to re-split.
    """
    if not payload.rows:
        raise HTTPException(status_code=400, detail="No rows provided")

    hierarchical_rows = build_hierarchical_rows_for_frontend(payload.rows)
    return SplitResponse(rows=hierarchical_rows)


@app.post("/api/compliance/export-xlsx")
async def export_xlsx(payload: ExportRequest):
    """
    Export EXACTLY the rows that the frontend sends (already split and ordered).
    No additional hierarchical splitting is done here.

    We:
    - Use the same outlineNumber as in the frontend.
    - Infer a simple 'level' from the outlineNumber (number of dots), only for display.
    """
    if not payload.rows:
        raise HTTPException(status_code=400, detail="No rows provided")

    rows_out = []

    for r in payload.rows:
        outline = r.outlineNumber or ""

        # Infer level from outlineNumber: "1" -> 0, "1.1" -> 1, "1.1.1" -> 2, etc.
        level = 0
        if outline:
            parts = [p for p in str(outline).split(".") if p != ""]
            level = max(len(parts) - 1, 0)

        rows_out.append(
            {
                "chunk_id": r.chunkId,
                "requirement_id": r.id,
                "outline_number": outline,
                "level": level,
                "text": r.text,
                "compliant": r.compliant,
                "mandatory_optional": r.mandatoryOptional or "",
                "confidence": float(r.confidence or 0.0),
            }
        )

    df = pd.DataFrame(
        rows_out,
        columns=[
            "chunk_id",
            "requirement_id",
            "outline_number",
            "level",
            "text",
            "compliant",
            "mandatory_optional",
            "confidence",
        ],
    )

    if not df.empty:
        df["sort_key"] = df["outline_number"].apply(numeric_sort_key)
        df = df.sort_values(by=["sort_key"], kind="mergesort").drop(columns=["sort_key"])

    # Write to in-memory buffer
    output = io.BytesIO()
    excel_saved = False

    # Try xlsxwriter first, then openpyxl
    for engine in ("xlsxwriter", "openpyxl"):
        try:
            with pd.ExcelWriter(output, engine=engine) as writer:
                df.to_excel(writer, index=False, sheet_name="Compliance")
                if engine == "xlsxwriter":
                    wb = writer.book
                    ws = writer.sheets["Compliance"]
                    wrap = wb.add_format({"text_wrap": True, "valign": "top"})
                    ws.set_column("A:A", 15, wrap)   # chunk_id
                    ws.set_column("B:B", 20, wrap)   # requirement_id
                    ws.set_column("C:C", 15, wrap)   # outline_number
                    ws.set_column("D:D", 8, wrap)    # level
                    ws.set_column("E:E", 100, wrap)  # text
                    ws.set_column("F:F", 12, wrap)   # compliant
                    ws.set_column("G:G", 18, wrap)   # mandatory_optional
                    ws.set_column("H:H", 12, wrap)   # confidence
            excel_saved = True
            break
        except Exception as e:
            print(f"⚠️ Failed to write Excel with engine {engine}: {e}")
            output.seek(0)
            output.truncate(0)
            continue

    if not excel_saved:
        raise HTTPException(
            status_code=500,
            detail="Unable to save Excel (install xlsxwriter or openpyxl)",
        )

    output.seek(0)

    base = payload.fileName
    base = re.sub(r"\.pdf$", "", base, flags=re.IGNORECASE)
    base = re.sub(r"\s+", "_", base).strip()

# ASCII-only fallback for headers (prevents Latin-1 encoding errors)
    ascii_base = re.sub(r"[^A-Za-z0-9_-]+", "_", base).strip("_")
    if not ascii_base:
        ascii_base = "compliance"

    ascii_filename = f"{ascii_base}_compliance.xlsx"

# Optional: preserve the original (possibly Arabic) name via RFC 5987
    utf8_filename = f"{base}_compliance.xlsx"
    utf8_quoted = quote(utf8_filename)

    headers = {
    "Content-Disposition": (
        f'attachment; filename="{ascii_filename}"; '
        f"filename*=UTF-8''{utf8_quoted}"
    )
}

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


@app.post("/api/compliance/highlight", response_model=HighlightResponse)
async def highlight_chunk(payload: HighlightRequest):
    """
    Given a chunk text, create an annotated PDF with that chunk highlighted
    and return the new PDF file name + first page number where it appears.
    The frontend then loads /api/compliance/pdf/{pdfFileName}.
    """
    base_pdf_path = UPLOAD_DIR / payload.fileName

    if not base_pdf_path.exists():
        raise HTTPException(status_code=404, detail="Base PDF not found")

    safe_req = (payload.requirementId or "sel").replace("/", "_")
    annotated_name = f"{base_pdf_path.stem}_annot_{safe_req}.pdf"
    annotated_path = UPLOAD_DIR / annotated_name

    try:
        doc = fitz.open(base_pdf_path)
    except Exception as e:
        print("❌ Error opening PDF for annotation:", e)
        raise HTTPException(status_code=500, detail="Cannot open PDF")

    text_to_find = payload.chunkText.strip()
    if len(text_to_find) > 400:
        text_to_find = text_to_find[:400]

    first_page_num: Optional[int] = None
    total_hits = 0

    for page_index in range(len(doc)):
        page = doc[page_index]
        rects = page.search_for(text_to_find)

        if not rects:
            continue

        if first_page_num is None:
            first_page_num = page_index + 1  # 1-based for UI

        for r in rects:
            hl = page.add_highlight_annot(r)
            hl.update()

        total_hits += len(rects)

    if total_hits == 0:
        doc.close()
        # No match found – just return original PDF and page 1
        return HighlightResponse(pdfFileName=payload.fileName, pageNumber=1)

    try:
        doc.save(str(annotated_path), incremental=False, garbage=4)
    finally:
        doc.close()

    return HighlightResponse(
        pdfFileName=annotated_name,
        pageNumber=first_page_num or 1,
    )


# ============================================================
# Entry point
# ============================================================

if __name__ == "__main__":
    import uvicorn

    #uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
    uvicorn.run("main:app", host="127.0.0.1", port=8000)







