import asyncio
import ssl

import httpx
import truststore
from bs4 import BeautifulSoup

from app.core.config import get_settings
from app.services.gemini import GeminiService
from app.services.retrieval import RetrievalService


async def main() -> None:
    settings = get_settings()
    gemini = GeminiService(settings)

    try:
        classification = await gemini.classify_scope(
            "What rights do data subjects have under RA 10173?"
        )
        print(f"Gemini classification: {classification.value}")
    except Exception as exc:
        print(f"Gemini generation check failed: {type(exc).__name__}")

    try:
        embedding = await gemini.embed_question("rights of a data subject")
        print(f"Gemini embedding dimensions: {len(embedding)}")
    except Exception as exc:
        print(f"Gemini embedding check failed: {type(exc).__name__}")

    headers = {"apikey": settings.supabase_secret_key.get_secret_value()}
    async with httpx.AsyncClient(
        timeout=20,
        follow_redirects=True,
        verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
    ) as client:
        supabase_response = await client.get(
            f"{settings.supabase_url.rstrip('/')}/rest/v1/documents",
            headers=headers,
            params={"select": "id", "limit": "1"},
        )
        official_response = await client.get(settings.judiciary_ra_10173_url)

    print(f"Supabase Data API status: {supabase_response.status_code}")
    print(f"Official law source status: {official_response.status_code}")
    if official_response.is_success:
        source_text = BeautifulSoup(official_response.text, "html.parser").get_text(" ")
        normalized_source = " ".join(source_text.upper().split())
        has_data_subject_rights = (
            "RIGHTS OF THE DATA SUBJECT" in normalized_source
            and ("SEC. 16" in normalized_source or "SECTION 16" in normalized_source)
        )
        print(f"Official source contains Section 16 rights: {has_data_subject_rights}")
        selected = RetrievalService._select_relevant_passages(
            "What rights do data subjects have under RA 10173?",
            "\n".join(
                " ".join(line.split())
                for line in BeautifulSoup(
                    official_response.text,
                    "html.parser",
                ).get_text("\n", strip=True).splitlines()
                if line.strip()
            ),
        )
        print(f"Retriever keeps complete Section 16: {'(f) Be indemnified' in selected}")


if __name__ == "__main__":
    asyncio.run(main())
