import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from app.core.config import Settings
from app.services.evidence import focused_queries
from app.services.gemini import GeminiService
from app.services.npc import parse_index, match_issuances, identity_question
from app.services.retrieval import RetrievalService


URL = "https://privacy.gov.ph/pips-and-pics/advisories-circulars/"
INDEX = """
* **NPC Circular 18-03 -**[Rules on Mediation](https://privacy.gov.ph/mediation.pdf)
* **NPC Circular 18-02 -**[Guidelines on Compliance Checks](https://privacy.gov.ph/compliance.pdf)
* **NPC Advisory No. 2018-02 -**[Different Advisory Title](https://privacy.gov.ph/advisory.pdf)
* **NPC Circular 18-01 -**[Rules on Advisory Opinions](https://privacy.gov.ph/opinions.pdf)
"""


class NpcCatalogTests(unittest.TestCase):
    def test_short_number_does_not_match_advisory_or_neighbour(self):
        entries = parse_index(INDEX, URL)
        for query in ("what is NPC Circular 18-02?", "NPC Circular No. 2018-02"):
            matches = match_issuances(query, entries)
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0].title, "Guidelines on Compliance Checks")

    def test_title_only_lookup_works_beyond_old_20000_character_cutoff(self):
        entries = parse_index("Index introduction " * 1500 + INDEX, URL)
        matches = match_issuances("what npc number is Guidelines on Compliance Checks?", entries)
        self.assertEqual(matches[0].identifier, "NPC Circular No. 18-02")
        self.assertTrue(identity_question("what npc number is Guidelines on Compliance Checks?", matches[0]))

    def test_handles_full_anchor_and_rejects_unofficial_link(self):
        text = """
        [NPC Circular No. 18-02 - Guidelines on Compliance Checks](https://privacy.gov.ph/checks/)
        NPC Circular No. 99-01 - [Untrusted fake title](https://evil.example/fake.pdf)
        """
        entries = parse_index(text, URL)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].title, "Guidelines on Compliance Checks")

    def test_unknown_or_partial_title_is_not_invented(self):
        entries = parse_index(INDEX, URL)
        self.assertEqual(match_issuances("NPC Circular 18-99", entries), [])
        self.assertEqual(match_issuances("What are compliance rules?", entries), [])

    def test_substantive_question_cannot_be_answered_from_title_alone(self):
        entry = match_issuances("NPC Circular 18-02", parse_index(INDEX, URL))[0]
        self.assertFalse(identity_question("What penalties apply under NPC Circular 18-02?", entry))
        self.assertFalse(identity_question("what is NPC Circular 18-02 and is it still valid?", entry))

    def test_search_queries_keep_number_and_title(self):
        self.assertIn("18-02", focused_queries("what is NPC Circular 18-02?")[0])
        self.assertIn("Guidelines on Compliance Checks", focused_queries(
            "what npc number is Guidelines on Compliance Checks?")[0])

    def test_title_lookup_is_classified_without_gemini_quota(self):
        self.assertIsNotNone(GeminiService.classify_scope_locally(
            "what npc number is Guidelines on Compliance Checks?"))


class NpcRetrievalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = Settings(_env_file=None, gemini_api_key="test", supabase_secret_key="test",
                                 supabase_url="https://example.supabase.co")
        self.gemini = NS(embed_question=AsyncMock(side_effect=RuntimeError("quota")),
                         search_official_web=AsyncMock(side_effect=RuntimeError("quota")),
                         evaluate_relevance=AsyncMock(side_effect=RuntimeError("quota")))
        self.service = RetrievalService(self.settings, self.gemini, NS())
        self.service._knowledge_base_is_ready = AsyncMock(return_value=True)
        self.service._get_official_source_text = AsyncMock(return_value=INDEX)

    async def test_both_reported_questions_work_without_generation_or_search_quota(self):
        for question in ("what is NPC Circular 18-02?", "what npc number is Guidelines on Compliance Checks?"):
            result = await self.service.retrieve(question)
            self.assertIn("NPC Circular No. 18-02", result.answer)
            self.assertIn("Guidelines on Compliance Checks", result.answer)
            self.assertEqual(str(result.sources[0].url), self.settings.npc_issuances_url)
            self.assertEqual(result.sources[0].origin, "web")
        self.gemini.search_official_web.assert_not_awaited()
        self.gemini.evaluate_relevance.assert_not_awaited()

    async def test_substantive_query_follows_document_link_and_keeps_provenance(self):
        self.service._get_official_source_text.side_effect = [INDEX, "SECTION 4. Modes of Compliance Checks.\nOfficial text."]
        result = await self.service._direct_privacy_evidence("Explain the modes under NPC Circular 18-02", fresh=False)
        self.assertIsNone(result.answer)
        self.assertEqual(len(result.sources), 2)
        self.assertEqual(str(result.sources[1].url), "https://privacy.gov.ph/compliance.pdf")
        self.assertIn("Official text", result.context)

    async def test_disabled_web_prevents_direct_catalog_lookup(self):
        self.settings.web_search_enabled = False
        result = await self.service.retrieve("what is NPC Circular 18-02?")
        self.service._get_official_source_text.assert_not_awaited()
        self.assertEqual(result.sources, [])
