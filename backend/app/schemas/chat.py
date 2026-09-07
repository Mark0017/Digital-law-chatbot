from enum import StrEnum
from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, field_validator


class ScopeClassification(StrEnum):
    DIGITAL_LAW_RELEVANT = "DIGITAL_LAW_RELEVANT"
    DIGITAL_LAW_RELATED = "DIGITAL_LAW_RELATED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class ChatRequest(BaseModel):
    conversation_id: UUID
    message: str = Field(min_length=1, max_length=4000)

    @field_validator("message")
    @classmethod
    def strip_message(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Message cannot be empty")
        return cleaned


class Source(BaseModel):
    title: str
    section: str | None = None
    url: HttpUrl | None = None
    page: int | None = None
    origin: Literal["knowledge_base", "web"] = "web"
    document_id: str | None = None
    publication_date: date | None = None
    retrieved_at: date | None = None


class ChatResponse(BaseModel):
    answer: str
    scope: ScopeClassification
    sources: list[Source]
    message_id: UUID | None = None


class AuthUser(BaseModel):
    id: UUID
    email: str | None = None
