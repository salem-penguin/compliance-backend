# app/models.py
from typing import Optional, List
from pydantic import BaseModel


class ComplianceRow(BaseModel):
    id: str
    chunkId: str
    outlineNumber: str
    text: str
    rephrasedRequirement: str
    compliant: str          # "Yes" | "No" | "Partial"
    mandatoryOptional: str  # "Mandatory" | "Optional" | "Not specified"
    confidence: float
    pageNumber: Optional[int] = None


class ComplianceTableResponse(BaseModel):
    fileName: str
    rows: List[ComplianceRow]
