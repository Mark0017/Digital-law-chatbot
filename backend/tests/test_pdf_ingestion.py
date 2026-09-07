import hashlib
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import httpx

from app.core.config import Settings
from app.services.evidence import PDF_UNABLE_TO_VERIFY, RelevanceAssessment, is_privacy_pdf_question
from app.services.ingestion import PdfIngestionService, extract_pdf_chunks
from app.services.retrieval import RetrievalService


def settings():
    return Settings(_env_file=None, gemini_api_key="test", supabase_secret_key="test",
                    supabase_url="https://example.supabase.co")


class PdfExtractionTests(unittest.TestCase):
    def test_section_crossing_pages_keeps_start_page_and_complete_text(self):
        pages = [NS(extract_text=lambda: "SECTION 2. Declaration of Policy.\nFirst part."),
                 NS(extract_text=lambda: "Continued policy.\nSECTION 3. Definitions.\nTerms.")]
        with patch("app.services.ingestion.PdfReader", return_value=NS(is_encrypted=False, pages=pages)):
            chunks = extract_pdf_chunks(b"test")
        self.assertEqual(len(chunks), 2)
        self.assertIn("Continued policy.", chunks[0]["content"])
        self.assertEqual(chunks[0]["page_number"], 1)
        self.assertEqual(chunks[0]["metadata"]["end_page"], 2)
        self.assertEqual(chunks[1]["page_number"], 2)

    def test_image_only_pdf_requires_ocr(self):
        with patch("app.services.ingestion.PdfReader", return_value=NS(
                is_encrypted=False, pages=[NS(extract_text=lambda: "")])):
            with self.assertRaisesRegex(ValueError, "OCR"):
                extract_pdf_chunks(b"test")


class PdfIngestionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.payload = b"%PDF-fixture"
        self.events = []
        self.ready = False
        self.chunks = [{"chunk_index": i, "content": text, "page_number": 1,
                        "section": f"Section {i + 1}.", "metadata": {}}
                       for i, text in enumerate(["Data Privacy Act of 2012", "Declaration of Policy."])]

    def handle(self, request):
        if "/storage/" in request.url.path:
            return httpx.Response(200, content=self.payload)
        if request.method == "GET":
            if self.ready:
                return httpx.Response(200, json=[{"id": "doc", "processing_status": "ready",
                                                 "content_hash": hashlib.sha256(self.payload).hexdigest()}])
            return httpx.Response(200, json=[])
        body = json.loads(request.content)
        self.events.append((request.url.path, body))
        return httpx.Response(201, json=[{"id": "doc"}])

    async def run_ingestion(self, gemini):
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.handle))
        with patch("app.services.ingestion.httpx.AsyncClient", return_value=client), \
             patch("app.services.ingestion.extract_pdf_chunks", return_value=self.chunks):
            return await PdfIngestionService(settings(), gemini).ingest("Data_Privacy_Act_RA10173.pdf")

    async def test_embeds_each_chunk_and_marks_ready_only_after_chunk_writes(self):
        gemini = NS(embed_question=AsyncMock(return_value=[0.1] * 768))
        result = await self.run_ingestion(gemini)
        self.assertEqual(gemini.embed_question.await_count, 2)
        self.assertEqual(result["chunks"], 2)
        self.assertEqual(self.events[0][1]["processing_status"], "processing")
        self.assertEqual(self.events[1][0], "/rest/v1/document_chunks")
        self.assertEqual(len(self.events[1][1][0]["embedding"]), 768)
        self.assertEqual(self.events[-1][1]["processing_status"], "ready")

    async def test_failed_embedding_cannot_mark_document_ready(self):
        with self.assertRaises(ValueError):
            await self.run_ingestion(NS(embed_question=AsyncMock(return_value=[0.1])))
        self.assertEqual(self.events[-1][1]["processing_status"], "failed")
        self.assertFalse(any(path.endswith("document_chunks") for path, _ in self.events))

    async def test_repeating_same_pdf_is_idempotent(self):
        self.ready = True
        gemini = NS(embed_question=AsyncMock())
        result = await self.run_ingestion(gemini)
        self.assertEqual(result["status"], "already_ready")
        gemini.embed_question.assert_not_awaited()
        self.assertEqual(self.events, [])


class PdfOnlyRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = settings()
        self.gemini = NS(
            embed_question=AsyncMock(return_value=[0.1]), search_official_web=AsyncMock(),
            evaluate_relevance=AsyncMock(return_value=RelevanceAssessment(
                is_sufficient=True, relevant_sources=[1], requires_current_web=False, reason="Exact section")),
        )
        self.supabase = NS(match_document_chunks=AsyncMock(return_value=[]),
                           get_pdf_chunks=AsyncMock(return_value=[{
                               "content": "SECTION 2. Declaration of Policy. Uploaded policy text.",
                               "source_title": "Data_Privacy_Act_RA10173.pdf", "document_id": "doc",
                               "is_authoritative": True, "page_number": 1, "section": "Section 2.",
                           }]))
        self.service = RetrievalService(self.settings, self.gemini, self.supabase)
        self.service._knowledge_base_is_ready = AsyncMock(return_value=True)
        self.service._direct_privacy_evidence = AsyncMock()

    async def test_exact_section_is_retrieved_even_when_vector_results_empty(self):
        result = await self.service.retrieve("SECTION 2 of Declaration of Policy?")
        self.supabase.get_pdf_chunks.assert_awaited_once_with(self.settings.primary_privacy_pdf_path, [2])
        self.assertEqual(result.sources[0].origin, "knowledge_base")
        self.assertEqual(result.sources[0].page, 1)
        self.gemini.search_official_web.assert_not_awaited()
        self.service._direct_privacy_evidence.assert_not_awaited()

    async def test_missing_pdf_does_not_fall_back_to_web(self):
        self.supabase.get_pdf_chunks.return_value = []
        result = await self.service.retrieve("Explain the Data Privacy Act of 2012")
        self.assertEqual(result.answer, PDF_UNABLE_TO_VERIFY)
        self.gemini.search_official_web.assert_not_awaited()

    async def test_embedding_outage_still_allows_exact_pdf_lookup(self):
        self.gemini.embed_question.side_effect = RuntimeError("Quota")
        result = await self.service.retrieve("Section 2?")
        self.assertEqual(result.sources[0].origin, "knowledge_base")
        self.gemini.search_official_web.assert_not_awaited()

    async def test_current_rules_do_not_claim_old_pdf_is_current(self):
        result = await self.service.retrieve("Latest amendments to RA 10173?")
        self.assertEqual(result.answer, PDF_UNABLE_TO_VERIFY)
        self.gemini.search_official_web.assert_not_awaited()

    def test_dpa_mentions_route_to_pdf_while_npc_guidance_stays_hybrid(self):
        for question in ("RA10173", "R.A. 10173", "Data Privacy Act of 2012", "data privacy", "Section 2?"):
            self.assertTrue(is_privacy_pdf_question(question), question)
        for question in ("Latest NPC circular on data privacy?", "What is RA 8792?", "NBA game?"):
            self.assertFalse(is_privacy_pdf_question(question), question)
