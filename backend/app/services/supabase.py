from typing import Any
from uuid import UUID

import httpx

from app.core.config import Settings
from app.schemas.chat import Source


class SupabaseService:
    def __init__(self, settings: Settings) -> None:
        self.base_url = settings.supabase_url.rstrip("/")
        self.headers = {
            "apikey": settings.supabase_secret_key.get_secret_value(),
            "Content-Type": "application/json",
        }

    async def conversation_belongs_to_user(self, conversation_id: UUID, user_id: UUID) -> bool:
        params = {
            "id": f"eq.{conversation_id}",
            "user_id": f"eq.{user_id}",
            "select": "id",
            "limit": "1",
        }
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{self.base_url}/rest/v1/conversations",
                headers=self.headers,
                params=params,
            )
        response.raise_for_status()
        return bool(response.json())

    async def match_document_chunks(
        self,
        embedding: list[float],
        threshold: float,
        count: int,
        source_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        body = {
            "query_embedding": embedding,
            "match_threshold": threshold,
            "match_count": count,
            "filter_source_types": source_types,
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{self.base_url}/rest/v1/rpc/match_document_chunks",
                headers=self.headers,
                json=body,
            )
            response.raise_for_status()
            chunks = response.json()
            if not chunks:
                return []
            document_ids = list(dict.fromkeys(str(UUID(c["document_id"])) for c in chunks))
            documents_response = await client.get(
                f"{self.base_url}/rest/v1/documents",
                headers=self.headers,
                params={
                    "id": f"in.({','.join(document_ids)})",
                    "select": "id,is_authoritative,publication_date,document_date,storage_path",
                    "is_authoritative": "eq.true",
                    "is_active": "eq.true",
                    "processing_status": "eq.ready",
                },
            )
            documents_response.raise_for_status()
        documents = {d["id"]: d for d in documents_response.json()}
        return [
            {**chunk, "is_authoritative": True,
             "storage_path": documents[chunk["document_id"]].get("storage_path"),
             "publication_date": documents[chunk["document_id"]].get("publication_date")
                 or documents[chunk["document_id"]].get("document_date")}
            for chunk in chunks if chunk["document_id"] in documents
        ]

    async def get_pdf_chunks(self, storage_path: str, section_numbers: list[int]) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(headers=self.headers, timeout=20) as client:
            response = await client.get(f"{self.base_url}/rest/v1/documents", params={
                "select": "id,title,source_type,storage_path,publication_date",
                "storage_path": f"eq.{storage_path}", "source_type": "eq.RA_10173",
                "processing_status": "eq.ready", "is_active": "eq.true", "is_authoritative": "eq.true",
            })
            response.raise_for_status()
            documents = response.json()
            if not documents:
                return []
            document = documents[0]
            params = {"select": "content,section,page_number,chunk_index,document_id,metadata",
                      "document_id": f"eq.{document['id']}", "order": "chunk_index", "limit": "200"}
            if section_numbers:
                params["or"] = "(" + ",".join(f"section.ilike.Section {n}.*" for n in section_numbers) + ")"
            response = await client.get(f"{self.base_url}/rest/v1/document_chunks", params=params)
            response.raise_for_status()
        return [{**chunk, "source_title": document["title"], "source_type": document["source_type"],
                 "storage_path": document["storage_path"], "is_authoritative": True,
                 "publication_date": document.get("publication_date")} for chunk in response.json()]

    async def has_ready_documents(self) -> bool:
        params = {
            "select": "id",
            "is_active": "eq.true",
            "processing_status": "eq.ready",
            "is_authoritative": "eq.true",
            "limit": "1",
        }
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{self.base_url}/rest/v1/documents",
                headers=self.headers,
                params=params,
            )
        response.raise_for_status()
        return bool(response.json())

    async def save_assistant_message(
        self,
        conversation_id: UUID,
        user_id: UUID,
        content: str,
        sources: list[Source],
    ) -> UUID | None:
        headers = {**self.headers, "Prefer": "return=representation"}
        body = {
            "conversation_id": str(conversation_id),
            "user_id": str(user_id),
            "role": "assistant",
            "content": content,
            "sources": [source.model_dump(mode="json") for source in sources],
        }
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{self.base_url}/rest/v1/messages",
                headers=headers,
                json=body,
            )
        response.raise_for_status()
        rows = response.json()
        return UUID(rows[0]["id"]) if rows else None
