"""Core data structures shared by all pipeline stages.

Flow:  PDF ──ingest──► Document ──classify──► ClassificationResult
                       Document + DocTypeSpec ──extract──► [Candidate] ──merge──► [FieldResult] + data(dict)
       everything ──► DocumentResult (→ output JSON + trace JSON)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

BBox = tuple[float, float, float, float]  # (x0, top, x1, bottom) in PDF points, origin = top-left of the page


@dataclass
class Word:
    text: str
    bbox: BBox


@dataclass
class Page:
    number: int  # 1-based
    text: str
    words: list[Word] = field(default_factory=list)
    width: float = 0.0
    height: float = 0.0
    source: Literal["text", "ocr", "empty"] = "text"
    ocr_engine: str | None = None


@dataclass
class Document:
    path: Path
    pages: list[Page]

    @property
    def text(self) -> str:
        return "\n\n".join(p.text for p in self.pages)

    @property
    def first_page_text(self) -> str:
        return self.pages[0].text if self.pages else ""

    @property
    def is_scanned(self) -> bool:
        return any(p.source == "ocr" for p in self.pages)

    def text_with_page_markers(self, max_pages: int | None = None) -> str:
        """Text with `[Strona N]` markers — used in LLM prompts so the model can cite pages."""
        pages = self.pages if max_pages is None else self.pages[:max_pages]
        return "\n\n".join(f"[Strona {p.number}]\n{p.text}" for p in pages)


@dataclass
class Evidence:
    """Where a value was found — the basis of extraction transparency (shown in the UI)."""

    page: int | None
    snippet: str
    bbox: BBox | None = None


@dataclass
class Candidate:
    """A proposed value for one leaf field, produced by one extractor."""

    path: str  # concrete path, e.g. "Odwolujacy[0].nip"  (see paths.py)
    value: Any
    method: str  # "rules:pattern" | "rules:label" | "llm" | "derived" | ...
    confidence: float  # 0..1
    evidence: Evidence | None = None
    note: str = ""


@dataclass
class FieldResult:
    """Final decision for one leaf field (what went into the JSON and why)."""

    path: str
    value: Any
    raw_value: Any = None
    method: str | None = None
    confidence: float = 0.0
    evidence: Evidence | None = None
    candidates: list[Candidate] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


@dataclass
class ClassificationResult:
    doc_type: str | None  # None → document not recognised (no JSON is written)
    confidence: float
    method: str  # "rules" | "llm" | "none"
    scores: dict[str, float] = field(default_factory=dict)
    reason: str = ""


@dataclass
class DocumentResult:
    source: Path
    classification: ClassificationResult
    data: dict[str, Any] | None = None  # canonical JSON (None if skipped)
    fields: list[FieldResult] = field(default_factory=list)
    pages: int = 0
    scanned: bool = False
    timings: dict[str, float] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    # the ingested document (pages, words with bboxes) — used by the UI to highlight sources; not serialized
    document: Document | None = field(default=None, repr=False)

    @property
    def skipped(self) -> bool:
        return self.data is None

    def to_trace(self) -> dict[str, Any]:
        """Serializable trace (written to traces/…/*.trace.json and returned by the API)."""
        return {
            "source": str(self.source),
            "pages": self.pages,
            "scanned": self.scanned,
            "classification": asdict(self.classification),
            "fields": [asdict(f) for f in self.fields],
            "timings": self.timings,
            "errors": self.errors,
        }
