"""Run from backend: python -m scripts.ingest_pdf --storage-path Data_Privacy_Act_RA10173.pdf"""

import argparse
import asyncio
import json

from app.core.config import get_settings
from app.services.gemini import GeminiService
from app.services.ingestion import PdfIngestionService


async def main():
    parser = argparse.ArgumentParser(description="Index the selected RA 10173 PDF from npc-documents")
    parser.add_argument("--storage-path", required=True)
    args = parser.parse_args()
    gemini = GeminiService(get_settings())
    try:
        result = await PdfIngestionService(get_settings(), gemini).ingest(args.storage_path)
        print(json.dumps(result))
    except Exception as exc:
        print(f"Ingestion failed: {type(exc).__name__}; status={getattr(exc, 'code', None)}")
        if isinstance(exc, ValueError):
            print(str(exc))
        raise SystemExit(1) from None
    finally:
        gemini.client.close()


if __name__ == "__main__":
    asyncio.run(main())
