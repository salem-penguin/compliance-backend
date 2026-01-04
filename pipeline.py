# app/pipeline.py
from typing import List
from .models import ComplianceRow
from . import final      # Arabic pipeline (final.py)
from . import comp       # English pipeline (comp.py)


def run_compliance_pipeline(pdf_path: str) -> List[ComplianceRow]:
    """
    Master pipeline:
    - detect Arabic using final.detect_arabic_pdf
    - if Arabic  → final.run_arabic_pipeline
    - if English → comp.run_english_pipeline
    - يرجّع List[ComplianceRow] للـ frontend
    """

    is_arabic = final.detect_arabic_pdf(pdf_path)

    if is_arabic:
        print("🌐 Detected: ARABIC RFP → Arabic pipeline")
        raw_results = final.run_arabic_pipeline(pdf_path)
    else:
        print("🌐 Detected: NON-ARABIC (likely English) → English pipeline")
        raw_results = comp.run_english_pipeline(pdf_path)

    rows: List[ComplianceRow] = []

    for idx, r in enumerate(raw_results, start=1):
        rows.append(
            ComplianceRow(
                id=str(idx),
                chunkId=r.get("chunk_id", f"Chunk_{idx:03d}"),
                outlineNumber=r.get("outline_number", str(idx)),
                text=r.get("original_chunk", ""),
                rephrasedRequirement=r.get("rephrased_requirement", ""),
                compliant=r.get("compliant", "No"),
                mandatoryOptional=r.get("mandatory_optional", "Not specified"),
                confidence=float(r.get("confidence", 0.5)),
                pageNumber=None,
            )
        )

    return rows
