import asyncio
from types import SimpleNamespace

from app.core.config import get_settings
from app.services.retrieval import RetrievalService


async def main() -> None:
    settings = get_settings()
    retrieval = RetrievalService(
        settings,
        SimpleNamespace(),
        SimpleNamespace(),
    )
    sources = retrieval._official_sources()
    texts = await asyncio.gather(
        *(retrieval._get_official_source_text(source) for source in sources)
    )
    questions = {
        "RA_10173": "What rights do data subjects have under RA 10173?",
        "RA_10175": "What is illegal access under RA 10175?",
        "RA_8792": "When is an electronic document valid under RA 8792?",
        "RA_9470": "Which public records are covered by RA 9470?",
        "RA_10844": "What are the DICT's powers under RA 10844?",
        "RA_11032": "What is automatic approval under RA 11032?",
        "RA_11930": "What acts are prohibited under RA 11930?",
    }

    for source, source_text in zip(sources, texts, strict=True):
        has_sections = "SEC." in source_text.upper() or "SECTION" in source_text.upper()
        selected_text = retrieval._select_relevant_passages(
            questions[source.source_type],
            source_text,
        )
        is_ready = bool(source_text and has_sections and selected_text)
        print(
            f"{source.source_type}: "
            f"{'ready' if is_ready else 'failed'} "
            f"({len(source_text):,} source characters; "
            f"{len(selected_text):,} retrieved)"
        )
        if not is_ready:
            raise RuntimeError(f"Source validation failed for {source.source_type}")


if __name__ == "__main__":
    asyncio.run(main())
