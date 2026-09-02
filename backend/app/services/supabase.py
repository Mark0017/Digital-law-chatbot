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
    ) -> list[dict[str, Any]]:
        body = {
            "query_embedding": embedding,
            "match_threshold": threshold,
            "match_count": count,
            "filter_source_types": None,
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{self.base_url}/rest/v1/rpc/match_document_chunks",
                headers=self.headers,
                json=body,
            )
        response.raise_for_status()
        return response.json()

    async def has_ready_documents(self) -> bool:
        params = {
            "select": "id",
            "is_active": "eq.true",
            "processing_status": "eq.ready",
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
