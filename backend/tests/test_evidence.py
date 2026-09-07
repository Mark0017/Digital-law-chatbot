import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

import httpx
from google.genai import types
from fastapi import HTTPException

from app.api.chat import _classify_scope_safely, chat
from app.core.config import Settings
from app.schemas.chat import AuthUser, ChatRequest, ScopeClassification, Source
from app.services.evidence import RelevanceAssessment, UNABLE_TO_VERIFY, focused_queries, source_tier
from app.services.gemini import GeminiService
from app.services.supabase import SupabaseService


def response(chunks, supports):
    return NS(candidates=[NS(grounding_metadata=NS(
        grounding_chunks=[NS(web=NS(uri=url, title=title, domain="privacy.gov.ph"))
                          for url, title in chunks],
        grounding_supports=[NS(segment=NS(text=text), grounding_chunk_indices=indices)
                            for text, indices in supports],
    ))])


class EvidencePolicyTests(unittest.TestCase):
    def test_authority_domains_are_validated_from_url(self):
        for url in ("https://privacy.gov.ph.evil.com/page", "https://evil.com/privacy.gov.ph",
                    "https://privacy.gov.ph@evil.com", "http://privacy.gov.ph/page",
                    "https://privacy.gov.ph:abc", "https://privacy.gov.ph:3000"):
            with self.subTest(url=url):
                self.assertIsNone(source_tier(url))
        self.assertEqual(source_tier("https://privacy.gov.ph/page"), 1)
        self.assertEqual(source_tier("https://elibrary.judiciary.gov.ph/page"), 2)
        self.assertEqual(source_tier("https://lawphil.net/page"), 2)

    def test_queries_optimize_biometrics_and_control_domains(self):
        queries = focused_queries("Can Acme collect employee John Smith's fingerprint?")
        self.assertEqual(len(queries), 1)
        self.assertTrue(queries[0].startswith("site:privacy.gov.ph "))
        self.assertIn("biometric", queries[0])
        self.assertIn("employee", queries[0])
        self.assertNotIn("John", queries[0])
        self.assertEqual(len(focused_queries("NPC Circular 2024-02", tier=2)), 2)


class GroundingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.gemini = object.__new__(GeminiService)
        self.gemini.model = self.gemini.classifier_model = self.gemini.fallback_model = "test-model"

    async def test_only_supported_authoritative_segments_survive(self):
        result = response(
            [("https://privacy.gov.ph/rights/", "Rights"), ("https://blog.example/law", "Blog")],
            [("Verified rights passage.", [0]), ("Unsupported blog assertion.", [1]),
             ("Mixed untrusted assertion.", [0, 1])],
        )
        context, sources = await self.gemini._grounded_evidence(result, 1)
        self.assertIn("Verified rights passage.", context)
        self.assertNotIn("assertion", context)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].origin, "web")

    async def test_missing_supports_do_not_turn_whole_response_into_evidence(self):
        result = response([("https://privacy.gov.ph/", "NPC")], [])
        self.assertEqual(await self.gemini._grounded_evidence(result, 1), ("", []))

    async def test_tier_one_excludes_non_npc_even_when_official(self):
        result = response([("https://lawphil.net/statute", "Lawphil")], [("Law text", [0])])
        self.assertEqual(await self.gemini._grounded_evidence(result, 1), ("", []))
        self.assertEqual(len((await self.gemini._grounded_evidence(result, 2))[1]), 1)

    async def test_google_redirect_resolves_to_actual_publisher(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(
            302, headers={"location": "https://privacy.gov.ph/actual-source/"}))
        client = httpx.AsyncClient(transport=transport)
        with patch("app.services.gemini.httpx.AsyncClient", return_value=client):
            resolved = await self.gemini._resolve_grounding_url(
                "https://vertexaisearch.cloud.google.com/grounding-api-redirect/test")
        self.assertEqual(resolved, "https://privacy.gov.ph/actual-source/")

    async def test_google_redirect_cannot_send_requests_to_untrusted_host(self):
        requests = []
        def handler(request):
            requests.append(str(request.url))
            return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch("app.services.gemini.httpx.AsyncClient", return_value=client):
            resolved = await self.gemini._resolve_grounding_url(
                "https://vertexaisearch.cloud.google.com/grounding-api-redirect/test")
        self.assertIsNone(resolved)
        self.assertEqual(len(requests), 1)

    async def test_structured_answer_emits_validated_citation_markers(self):
        self.gemini._generate_content = AsyncMock(return_value=NS(text=json.dumps({
            "claims": [{"text": "The document describes access rights.", "citations": [1]}]})))
        answer = await self.gemini.answer("Rights?", "Access rights.", [Source(title="PDF", origin="knowledge_base")])
        self.assertEqual(answer, "The document describes access rights. [1]")

    async def test_json_schema_uses_the_compatible_gemini_config_field(self):
        generate = Mock(return_value=NS(text="{}"))
        self.gemini.client = NS(models=NS(generate_content=generate))
        await self.gemini._generate_content(
            "Test", models=("test-model",), thinking_level=types.ThinkingLevel.MINIMAL,
            max_output_tokens=100, response_schema=RelevanceAssessment,
        )
        config = generate.call_args.kwargs["config"]
        self.assertEqual(config.response_mime_type, "application/json")
        self.assertIsNone(config.response_schema)
        self.assertIn("is_sufficient", config.response_json_schema["properties"])

    async def test_fabricated_citation_or_missing_citation_is_rejected(self):
        for claim in (
            {"text": "Invented claim.", "citations": [99]},
            {"text": "Uncited claim.", "citations": []},
            {"text": "Claim [99].", "citations": [1]},
            {"text": "See https://fake.example", "citations": [1]},
        ):
            self.gemini._generate_content = AsyncMock(return_value=NS(text=json.dumps({"claims": [claim]})))
            answer = await self.gemini.answer("Rights?", "Evidence", [Source(title="PDF")])
            self.assertEqual(answer, UNABLE_TO_VERIFY)

    async def test_malformed_relevance_and_out_of_range_sources_fail_closed(self):
        for payload in ("not json", '{"is_sufficient": true}', json.dumps({
            "is_sufficient": True, "relevant_sources": [99],
            "requires_current_web": False, "reason": "Invalid citation"})):
            self.gemini._generate_content = AsyncMock(return_value=NS(text=payload))
            with self.assertRaises(ValueError):
                await self.gemini.evaluate_relevance("Rights?", "Evidence", [Source(title="PDF")])


class ScopeRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_classifier_outage_does_not_allow_unrelated_search(self):
        gemini = NS(classify_scope=AsyncMock(side_effect=RuntimeError("quota")))
        with self.assertRaises(HTTPException) as caught:
            await _classify_scope_safely(gemini, "Who won the NBA game?")
        self.assertEqual(caught.exception.status_code, 503)

    async def test_out_of_scope_stops_before_retrieval(self):
        uid = "00000000-0000-0000-0000-000000000001"
        gemini = NS(classify_scope=AsyncMock(return_value=ScopeClassification.OUT_OF_SCOPE))
        supabase = NS(conversation_belongs_to_user=AsyncMock(return_value=True),
                      save_assistant_message=AsyncMock(return_value=None))
        with patch("app.api.chat.GeminiService", return_value=gemini), \
             patch("app.api.chat.SupabaseService", return_value=supabase), \
             patch("app.api.chat.RetrievalService") as retrieval:
            result = await chat(ChatRequest(conversation_id=uid, message="Who won the NBA game?"),
                                AuthUser(id=uid), NS(max_chat_message_length=4000))
        retrieval.assert_not_called()
        self.assertIn("specialize in Philippine data privacy", result.answer)


class DatabaseAuthorityTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_authoritative_active_documents_are_returned(self):
        first = "00000000-0000-0000-0000-000000000001"
        second = "00000000-0000-0000-0000-000000000002"
        def handler(request):
            if request.method == "POST":
                return httpx.Response(200, json=[{"document_id": first}, {"document_id": second}])
            self.assertEqual(request.url.params["is_authoritative"], "eq.true")
            self.assertEqual(request.url.params["is_active"], "eq.true")
            return httpx.Response(200, json=[{"id": first, "is_authoritative": True,
                                             "publication_date": "2025-01-01"}])
        settings = Settings(_env_file=None, gemini_api_key="test", supabase_secret_key="test",
                            supabase_url="https://example.supabase.co")
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch("app.services.supabase.httpx.AsyncClient", return_value=client):
            chunks = await SupabaseService(settings).match_document_chunks([0.1], 0.68, 8)
        self.assertEqual(len(chunks), 1)
        self.assertTrue(chunks[0]["is_authoritative"])
        self.assertEqual(chunks[0]["publication_date"], "2025-01-01")
