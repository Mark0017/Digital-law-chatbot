import asyncio
import sys
import time

from app.core.config import get_settings
from app.services.gemini import GeminiService
from app.services.retrieval import RetrievalService
from app.services.supabase import SupabaseService


async def main() -> None:
    question = " ".join(sys.argv[1:]).strip()
    if not question:
        question = "What rights do data subjects have under RA 10173?"
    settings = get_settings()
    gemini = GeminiService(settings)
    supabase = SupabaseService(settings)
    retrieval = RetrievalService(settings, gemini, supabase)

    started = time.perf_counter()
    scope = await gemini.classify_scope(question)
    classification_seconds = time.perf_counter() - started

    started = time.perf_counter()
    result = await retrieval.retrieve(question)
    cold_retrieval_seconds = time.perf_counter() - started

    started = time.perf_counter()
    await retrieval.retrieve(question)
    warm_retrieval_seconds = time.perf_counter() - started

    started = time.perf_counter()
    answer = await gemini.answer(question, result.context, result.sources)
    answer_seconds = time.perf_counter() - started

    print(f"Scope: {scope.value} ({classification_seconds:.3f}s)")
    print(f"Cold retrieval: {cold_retrieval_seconds:.3f}s")
    print(f"Warm retrieval: {warm_retrieval_seconds:.3f}s")
    print(f"Answer generation: {answer_seconds:.3f}s")
    print(f"Answer characters: {len(answer)}")
    print(f"Sources: {', '.join(source.title for source in result.sources)}")


if __name__ == "__main__":
    asyncio.run(main())
