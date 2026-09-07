import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.core.config import Settings
from app.schemas.chat import Source
from app.services.evidence import RelevanceAssessment, UNABLE_TO_VERIFY
from app.services.retrieval import RetrievalResult, RetrievalService


def assessment(sufficient=False, relevant=(), current=False):
    return RelevanceAssessment(
        is_sufficient=sufficient, relevant_sources=list(relevant),
        requires_current_web=current, reason="Test evidence assessment",
    )


def chunk(**overrides):
    return dict(
        document_id="00000000-0000-0000-0000-000000000001",
        source_title="Uploaded privacy PDF", content="SEC. 16. Data subject rights.",
        similarity=0.92, is_authoritative=True, page_number=4, section="Section 16",
        source_url=None, publication_date="2020-01-01", **overrides,
    )


class HybridRetrievalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = Settings(_env_file=None, gemini_api_key="test",
                                 supabase_url="https://example.supabase.co",
                                 supabase_secret_key="test")
        self.gemini = SimpleNamespace(
            embed_question=AsyncMock(return_value=[0.1]),
            evaluate_relevance=AsyncMock(return_value=assessment()),
            search_official_web=AsyncMock(return_value=("", [])),
        )
        self.supabase = SimpleNamespace(match_document_chunks=AsyncMock(return_value=[chunk()]))
        self.service = RetrievalService(self.settings, self.gemini, self.supabase)
        self.service._knowledge_base_is_ready = AsyncMock(return_value=True)
        self.service._direct_privacy_evidence = AsyncMock(return_value=RetrievalResult("", []))

    def web(self, text="Official NPC guidance.", url="https://privacy.gov.ph/guidance/"):
        return (f"[Source 1: Web Source - NPC guidance]\n{text}",
                [Source(title="NPC guidance", url=url, origin="web")])

    async def test_sufficient_pdf_never_searches_web(self):
        self.gemini.evaluate_relevance.return_value = assessment(True, [1])
        result = await self.service.retrieve("What rights do data subjects have?")
        self.gemini.embed_question.assert_awaited_once()
        self.supabase.match_document_chunks.assert_awaited_once()
        self.gemini.search_official_web.assert_not_awaited()
        self.assertEqual(result.sources[0].origin, "knowledge_base")
        self.assertIsNone(result.sources[0].url)
        self.assertEqual(result.sources[0].page, 4)
        self.assertIn("Section 16", result.sources[0].section)

    async def test_high_similarity_unrelated_rights_do_not_answer_penalties(self):
        self.gemini.evaluate_relevance.side_effect = [assessment(), assessment(True, [1])]
        self.gemini.search_official_web.return_value = self.web("Penalty provision.")
        result = await self.service.retrieve("What penalties apply to mishandling personal information?")
        self.gemini.search_official_web.assert_awaited_once_with(
            "What penalties apply to mishandling personal information?", tier=1)
        self.assertEqual([s.origin for s in result.sources], ["web"])
        self.assertNotIn("Data subject rights", result.context)

    async def test_empty_database_uses_npc_first_without_embedding(self):
        self.service._knowledge_base_is_ready.return_value = False
        self.gemini.evaluate_relevance.return_value = assessment(True, [1])
        self.gemini.search_official_web.return_value = self.web()
        result = await self.service.retrieve("Can an employer collect fingerprints?")
        self.gemini.embed_question.assert_not_awaited()
        self.assertEqual(result.sources[0].origin, "web")
        self.gemini.search_official_web.assert_awaited_once_with(
            "Can an employer collect fingerprints?", tier=1)

    async def test_current_question_retrieves_vectors_before_web_and_fuses(self):
        events = []
        async def embed(question):
            events.append("vector")
            return [0.1]
        async def search(question, tier=1):
            events.append("web")
            return self.web("Newer NPC guidance differs from the uploaded PDF.")
        self.gemini.embed_question.side_effect = embed
        self.gemini.search_official_web.side_effect = search
        self.gemini.evaluate_relevance.side_effect = [
            assessment(True, [1]), assessment(True, [1, 2], True)]
        result = await self.service.retrieve("What is the latest NPC guidance on data subject rights?")
        self.assertEqual(events, ["vector", "web"])
        self.assertEqual([s.origin for s in result.sources], ["knowledge_base", "web"])
        self.assertIn("[Source 1:", result.context)
        self.assertIn("[Source 2:", result.context)
        self.assertIsNone(result.answer)

    async def test_semantic_recency_check_triggers_web_without_keyword(self):
        self.gemini.evaluate_relevance.side_effect = [
            assessment(True, [1], True), assessment(True, [1, 2], True)]
        self.gemini.search_official_web.return_value = self.web()
        await self.service.retrieve("Must my company register its DPO?")
        self.gemini.search_official_web.assert_awaited_once()

    async def test_partial_pdf_and_web_jointly_answer_question(self):
        self.gemini.evaluate_relevance.side_effect = [
            assessment(False, [1]), assessment(True, [1, 2])]
        self.gemini.search_official_web.return_value = self.web()
        result = await self.service.retrieve("Explain privacy rights and biometric processing.")
        self.assertEqual(len(result.sources), 2)

    async def test_expands_to_government_only_after_npc_is_insufficient(self):
        self.supabase.match_document_chunks.return_value = []
        self.gemini.search_official_web.side_effect = [
            self.web("General unrelated information."),
            self.web("Relevant legal provision.", "https://lawphil.net/statutes/law.html")]
        self.gemini.evaluate_relevance.side_effect = [assessment(), assessment(True, [1])]
        result = await self.service.retrieve("What penalties apply to personal data misuse?")
        self.assertEqual([c.kwargs["tier"] for c in self.gemini.search_official_web.await_args_list], [1, 2])
        self.assertIn("lawphil.net", str(result.sources[0].url))
        self.assertNotIn("unrelated", result.context)

    async def test_web_failure_does_not_present_old_pdf_as_current(self):
        self.gemini.evaluate_relevance.return_value = assessment(True, [1])
        result = await self.service.retrieve("What are the latest data subject rules?")
        self.assertEqual(result.answer, UNABLE_TO_VERIFY)
        self.assertEqual(result.sources, [])

    async def test_evaluator_failure_does_not_treat_results_as_sufficient(self):
        self.gemini.evaluate_relevance.side_effect = RuntimeError("quota unavailable")
        self.gemini.search_official_web.return_value = self.web()
        result = await self.service.retrieve("What are the privacy requirements?")
        self.assertEqual(result.answer, UNABLE_TO_VERIFY)

    async def test_embedding_failure_still_tries_web(self):
        self.gemini.embed_question.side_effect = RuntimeError("embedding unavailable")
        self.gemini.search_official_web.return_value = self.web()
        self.gemini.evaluate_relevance.return_value = assessment(True, [1])
        result = await self.service.retrieve("What rights do data subjects have?")
        self.assertEqual(result.sources[0].origin, "web")

    async def test_disabled_web_stops_all_external_source_fetches(self):
        self.settings.web_search_enabled = False
        result = await self.service.retrieve("What is the latest NPC circular?")
        self.gemini.search_official_web.assert_not_awaited()
        self.service._direct_privacy_evidence.assert_not_awaited()
        self.assertEqual(result.answer, UNABLE_TO_VERIFY)

    async def test_direct_official_fetch_must_pass_evaluation(self):
        self.supabase.match_document_chunks.return_value = []
        context, sources = self.web("Relevant directly fetched text.")
        self.service._direct_privacy_evidence.return_value = RetrievalResult(context, sources)
        self.gemini.evaluate_relevance.return_value = assessment(True, [1])
        result = await self.service.retrieve("What rights do data subjects have?")
        self.assertIn("directly fetched", result.context)

    def test_filters_untrusted_and_below_threshold_chunks(self):
        untrusted = {**chunk(), "is_authoritative": False}
        weak = {**chunk(), "similarity": 0.2}
        invalid = {**chunk(), "similarity": float("nan")}
        result = self.service._chunks_to_evidence([untrusted, weak, invalid])
        self.assertEqual(result.sources, [])

    def test_duplicate_chunks_keep_source_numbers_consistent(self):
        result = self.service._chunks_to_evidence([chunk(), chunk()])
        self.assertEqual(len(result.sources), 1)
        self.assertEqual(result.context.count("[Source 1:"), 2)
        self.assertNotIn("[Source 2:", result.context)

    def test_different_pages_have_distinct_citations(self):
        result = self.service._chunks_to_evidence([chunk(), {**chunk(), "page_number": 5}])
        retained = self.service._retain_sources(result, [2])
        self.assertEqual(retained.sources[0].page, 5)
        self.assertIn("[Source 1:", retained.context)
        self.assertNotIn("[Source 2:", retained.context)

    def test_preserves_complete_legal_sections(self):
        text = "SEC. 15. Other.\nOther text.\nSEC. 16. Data subject rights.\n(a) Access.\n(b) Correction.\nSEC. 17. Other.\nOther text."
        selected = self.service._select_relevant_passages("What are data subject rights?", text)
        self.assertIn("(a) Access.", selected)
        self.assertIn("(b) Correction.", selected)


if __name__ == "__main__":
    unittest.main()
