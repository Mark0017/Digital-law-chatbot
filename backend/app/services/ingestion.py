"""Process an operator-selected PDF already in the private Supabase bucket."""

import asyncio
import hashlib
import io
import math
import re
from urllib.parse import quote

import httpx
from pypdf import PdfReader

from app.core.config import Settings
from app.services.gemini import GeminiService
from app.services.supabase import SupabaseService


def extract_pdf_chunks(data: bytes, max_chars: int = 6000) -> list[dict]:
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        raise ValueError("Decrypt the PDF before ingestion")
    sections = []
    lines: list[str] = []
    section = None
    first_page = 1
    last_page = 1
    for page_number, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        for line in text.splitlines():
            line = " ".join(line.split())
            if not line or re.fullmatch(r"Republic Act No\. 10173.*Page \d+ of \d+", line, re.I):
                continue
            heading = re.match(r"^(?:SECTION|SEC\.)\s+(\d+)\.\s*(.*)", line, re.I)
            if heading:
                if lines:
                    sections.append((section, first_page, last_page, "\n".join(lines)))
                lines = []
                section = f"Section {heading.group(1)}. {heading.group(2)}".strip()
            if not lines:
                first_page = page_number
            last_page = page_number
            lines.append(line)
    if lines:
        sections.append((section, first_page, last_page, "\n".join(lines)))
    chunks = []
    for section, page, end_page, text in sections:
        start = 0
        while start < len(text):
            end = min(start + max_chars, len(text))
            if end < len(text):
                boundary = text.rfind(" ", start + max_chars // 2, end)
                if boundary > start:
                    end = boundary
            content = text[start:end].strip()
            if content:
                chunks.append({"chunk_index": len(chunks), "content": content,
                               "section": section, "page_number": page,
                               "metadata": {"end_page": end_page}})
            if end == len(text):
                break
            start = max(start + 1, end - min(300, max_chars // 4))
    if not chunks:
        raise ValueError("PDF has no extractable text; OCR is required before ingestion")
    return chunks


class PdfIngestionService:
    def __init__(self, settings: Settings, gemini: GeminiService):
        self.settings = settings
        self.gemini = gemini
        self.supabase = SupabaseService(settings)

    async def ingest(self, storage_path: str) -> dict:
        if not storage_path.lower().endswith(".pdf") or ".." in storage_path.split("/"):
            raise ValueError("Provide an exact PDF object path within npc-documents")
        if self.settings.embedding_dimension != 768:
            raise ValueError("The existing Supabase vector schema requires 768 dimensions")
        key = self.settings.supabase_secret_key.get_secret_value()
        headers = {**self.supabase.headers, "Authorization": f"Bearer {key}"}
        base = self.supabase.base_url
        async with httpx.AsyncClient(headers=headers, timeout=30) as client:
            response = await client.get(f"{base}/storage/v1/object/authenticated/npc-documents/{quote(storage_path, safe='/')}")
            response.raise_for_status()
            data = response.content
            if len(data) > self.settings.max_upload_bytes:
                raise ValueError("PDF exceeds MAX_UPLOAD_BYTES")
            if not data.startswith(b"%PDF-"):
                raise ValueError("Storage object is not a PDF")
            digest = hashlib.sha256(data).hexdigest()
            chunks = extract_pdf_chunks(data)
            all_text = "\n".join(c["content"] for c in chunks)
            if not re.search(r"(?:REPUBLIC ACT\s+NO\.?\s*10173|Data Privacy Act of 2012)", all_text, re.I):
                raise ValueError("This importer is for the selected RA 10173 PDF")
            response = await client.get(f"{base}/rest/v1/documents", params={
                "select": "id,content_hash,processing_status,storage_path",
                "storage_path": f"eq.{storage_path}",
            })
            response.raise_for_status()
            existing = response.json()
            if not existing:
                response = await client.get(f"{base}/rest/v1/documents", params={
                    "select": "id,content_hash,processing_status,storage_path",
                    "content_hash": f"eq.{digest}",
                })
                response.raise_for_status()
                existing = response.json()
            if existing:
                document = existing[0]
                if document["content_hash"] != digest:
                    raise ValueError("This storage path was already indexed with different content; use a new path")
                if document["processing_status"] == "ready":
                    return {"document_id": document["id"], "status": "already_ready", "chunks": len(chunks)}
                document_id = document["id"]
                response = await client.patch(f"{base}/rest/v1/documents", params={"id": f"eq.{document_id}"},
                                              json={"processing_status": "processing", "processing_error": None})
            else:
                response = await client.post(f"{base}/rest/v1/documents", headers={"Prefer": "return=representation"}, json={
                    "title": storage_path.rsplit("/", 1)[-1], "source_type": "RA_10173",
                    "storage_path": storage_path, "content_hash": digest,
                    "is_authoritative": True, "processing_status": "processing",
                    "description": "User-selected RA 10173 knowledge-base PDF. Answers cite this uploaded copy.",
                })
                response.raise_for_status()
                document_id = response.json()[0]["id"]
            response.raise_for_status()
            try:
                semaphore = asyncio.Semaphore(3)

                async def embed_chunk(chunk):
                    async with semaphore:
                        # embedding-2 combines a list of contents into one embedding.
                        # Each independently searchable chunk needs its own request.
                        return await self.gemini.embed_question(chunk["content"])

                for start in range(0, len(chunks), 16):
                    batch = chunks[start:start + 16]
                    vectors = await asyncio.gather(*(embed_chunk(chunk) for chunk in batch))
                    rows = []
                    for chunk, vector in zip(batch, vectors, strict=True):
                        values = list(vector)
                        if len(values) != 768 or not all(math.isfinite(x) for x in values) or not any(values):
                            raise ValueError("Embedding service returned an invalid vector")
                        rows.append({**chunk, "document_id": document_id, "embedding": values,
                                     "metadata": {**chunk["metadata"], "embedding_model": self.settings.embedding_model}})
                    response = await client.post(f"{base}/rest/v1/document_chunks",
                        params={"on_conflict": "document_id,chunk_index"},
                        headers={"Prefer": "resolution=merge-duplicates"}, json=rows)
                    response.raise_for_status()
                response = await client.patch(f"{base}/rest/v1/documents", params={"id": f"eq.{document_id}"},
                                              json={"processing_status": "ready", "processing_error": None})
                response.raise_for_status()
            except Exception as exc:
                await client.patch(f"{base}/rest/v1/documents", params={"id": f"eq.{document_id}"},
                                   json={"processing_status": "failed", "processing_error": type(exc).__name__})
                raise
        return {"document_id": document_id, "status": "ready", "chunks": len(chunks)}
