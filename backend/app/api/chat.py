import asyncio
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, status

from app.core.config import Settings, get_settings
from app.schemas.chat import AuthUser, ChatRequest, ChatResponse, ScopeClassification
from app.services.auth import get_current_user
from app.services.gemini import GeminiService
from app.services.retrieval import RetrievalService
from app.services.supabase import SupabaseService


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    user: AuthUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> ChatResponse:
    if len(request.message) > settings.max_chat_message_length:
        raise HTTPException(status_code=422, detail="Message is too long.")

    supabase = SupabaseService(settings)
    gemini = GeminiService(settings)

    try:
        owns_conversation = await supabase.conversation_belongs_to_user(
            request.conversation_id,
            user.id,
        )
    except httpx.HTTPError as exc:
        logger.warning("Supabase conversation lookup failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Conversation storage is temporarily unavailable.",
        ) from exc

    if not owns_conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    retrieval = RetrievalService(settings, gemini, supabase)
    scope, result = await asyncio.gather(
        _classify_scope_safely(gemini, request.message),
        retrieval.retrieve(request.message),
    )

    if scope == ScopeClassification.OUT_OF_SCOPE:
        answer = (
            "I'm designed to answer questions about RA 10173, RA 10175, RA 8792, "
            "RA 9470, RA 10844, RA 11032, and RA 11930. I can't answer questions "
            "outside these supported Philippine digital laws."
        )
        message_id = await _save_message_safely(
            supabase, request.conversation_id, user.id, answer, []
        )
        return ChatResponse(answer=answer, scope=scope, sources=[], message_id=message_id)

    if not result.context:
        answer = (
            "I couldn't find sufficient information in the available official Philippine "
            "legal sources to answer this confidently."
        )
    else:
        try:
            answer = await gemini.answer(request.message, result.context, result.sources)
        except Exception as exc:
            logger.warning("Gemini answer generation failed: %s", type(exc).__name__)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="The AI service could not generate an answer right now.",
            ) from exc

    message_id = await _save_message_safely(
        supabase,
        request.conversation_id,
        user.id,
        answer,
        result.sources,
    )
    return ChatResponse(
        answer=answer,
        scope=scope,
        sources=result.sources,
        message_id=message_id,
    )


async def _classify_scope_safely(
    gemini: GeminiService,
    message: str,
) -> ScopeClassification:
    try:
        return await gemini.classify_scope(message)
    except Exception as exc:
        logger.warning("Gemini classification failed: %s", type(exc).__name__)
        return ScopeClassification.DIGITAL_LAW_RELATED


async def _save_message_safely(
    supabase: SupabaseService,
    conversation_id,
    user_id,
    answer,
    sources,
):
    try:
        return await supabase.save_assistant_message(
            conversation_id,
            user_id,
            answer,
            sources,
        )
    except httpx.HTTPError as exc:
        logger.warning("Assistant message persistence failed: %s", type(exc).__name__)
        return None
