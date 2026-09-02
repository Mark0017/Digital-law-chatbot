import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import app.services.retrieval as retrieval_module
from app.services.retrieval import OfficialLegalSource, RetrievalService


class RetrievalSelectionTests(unittest.TestCase):
    def test_keeps_complete_section_for_data_subject_rights(self) -> None:
        source = """
SEC. 15.
Extension of Privileged Communication.
Evidence gathered on privileged information is inadmissible.
SEC. 16.
Rights of the Data Subject.
The data subject is entitled to:
(a) Be informed whether personal information has been processed;
(b) Be furnished information about the processing;
(c) Have reasonable access to processed personal information;
(d) Dispute inaccuracies and have them corrected;
(e) Suspend, withdraw, block, remove, or destroy personal information; and
(f) Be indemnified for damages.
SEC. 17.
Transmissibility of Rights of the Data Subject.
Lawful heirs and assigns may invoke these rights.
""".strip()

        result = RetrievalService._select_relevant_passages(
            "What rights do data subjects have under RA 10173?",
            source,
        )

        self.assertIn("SEC. 16.", result)
        self.assertIn("(a) Be informed", result)
        self.assertIn("(f) Be indemnified", result)

    def test_selects_relevant_article_from_article_based_law(self) -> None:
        source = """
ARTICLE 1. Short Title.
This Act shall be known by its short title.
ARTICLE 2. General Policy.
The State shall protect consumers from deceptive sales practices.
ARTICLE 3. Definitions.
Consumer means a natural person who purchases goods or services.
ARTICLE 4. Consumer Records.
A provider shall handle consumer records and personal information fairly.
ARTICLE 5. Penalties.
The applicable penalties are provided here.
""".strip()

        result = RetrievalService._select_relevant_passages(
            "How does the law protect consumer personal information?",
            source,
        )

        self.assertIn("ARTICLE 4. Consumer Records.", result)
        self.assertIn("personal information fairly", result)


class RetrievalOptimizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_skips_embedding_when_knowledge_base_is_empty(self) -> None:
        retrieval_module._knowledge_base_cache = None
        settings = SimpleNamespace(
            retrieval_match_threshold=0.68,
            retrieval_match_count=8,
            judiciary_ra_10173_url=(
                "https://elibrary.judiciary.gov.ph/thebookshelf/showdocs/2/50253"
            ),
            judiciary_ra_10175_url="https://example.com/ra-10175",
            judiciary_ra_8792_url="https://example.com/ra-8792",
            judiciary_ra_9470_url="https://example.com/ra-9470",
            judiciary_ra_10844_url="https://example.com/ra-10844",
            judiciary_ra_11032_url="https://example.com/ra-11032",
            judiciary_ra_11930_url="https://example.com/ra-11930",
        )
        gemini = SimpleNamespace(embed_question=AsyncMock())
        supabase = SimpleNamespace(
            has_ready_documents=AsyncMock(return_value=False),
        )
        service = RetrievalService(settings, gemini, supabase)
        service._get_official_source_text = AsyncMock(
            return_value="SEC. 16.\nRights of the Data Subject.\n(a) Be informed."
        )

        result = await service.retrieve("What rights do data subjects have?")

        supabase.has_ready_documents.assert_awaited_once()
        gemini.embed_question.assert_not_awaited()
        self.assertIn("SEC. 16.", result.context)
        retrieval_module._knowledge_base_cache = None

    async def test_uses_dynamic_official_source_for_unregistered_act(self) -> None:
        retrieval_module._knowledge_base_cache = None
        settings = SimpleNamespace(
            retrieval_match_threshold=0.68,
            retrieval_match_count=8,
            judiciary_ra_10173_url="https://example.com/ra-10173",
            judiciary_ra_10175_url="https://example.com/ra-10175",
            judiciary_ra_8792_url="https://example.com/ra-8792",
            judiciary_ra_9470_url="https://example.com/ra-9470",
            judiciary_ra_10844_url="https://example.com/ra-10844",
            judiciary_ra_11032_url="https://example.com/ra-11032",
            judiciary_ra_11930_url="https://example.com/ra-11930",
        )
        gemini = SimpleNamespace(embed_question=AsyncMock())
        supabase = SimpleNamespace(has_ready_documents=AsyncMock(return_value=False))
        service = RetrievalService(settings, gemini, supabase)
        dynamic_source = OfficialLegalSource(
            source_type="RA_7394",
            title="Republic Act No. 7394",
            url="https://elibrary.judiciary.gov.ph/thebookshelf/showdocs/2/22263",
            markers=(),
        )
        service._resolve_dynamic_source = AsyncMock(return_value=dynamic_source)
        service._get_official_source_text = AsyncMock(
            return_value="ARTICLE 2. Declaration of Basic Policy. Consumer protection."
        )

        result = await service.retrieve(
            "What consumer-data protections are in RA 7394?"
        )

        service._resolve_dynamic_source.assert_awaited_once_with("7394")
        self.assertEqual(result.sources[0].title, "Republic Act No. 7394")
        self.assertIn("Consumer protection", result.context)
        retrieval_module._knowledge_base_cache = None

    def test_parses_only_exact_official_republic_act_result(self) -> None:
        payload = {
            "data": [
                [
                    "IRR of REPUBLIC ACT NO. 7394",
                    "1992-01-01",
                    "<a href='https://elibrary.judiciary.gov.ph/thebookshelf/showdocs/2/1'>IRR</a>",
                ],
                [
                    "REPUBLIC ACT NO. 7394",
                    "1992-04-13",
                    "<a href='https://elibrary.judiciary.gov.ph/thebookshelf/showdocs/2/22263'>Consumer Act</a>",
                ],
            ]
        }

        source = RetrievalService._parse_dynamic_source("7394", payload)

        self.assertIsNotNone(source)
        self.assertEqual(source.source_type, "RA_7394")
        self.assertEqual(
            source.url,
            "https://elibrary.judiciary.gov.ph/thebookshelf/showdocs/2/22263",
        )

    def test_rejects_non_official_dynamic_result_url(self) -> None:
        payload = {
            "data": [[
                "REPUBLIC ACT NO. 7394",
                "1992-04-13",
                "<a href='https://example.com/fake-law'>Consumer Act</a>",
            ]]
        }

        self.assertIsNone(RetrievalService._parse_dynamic_source("7394", payload))

    def test_selects_each_supported_act_by_number(self) -> None:
        settings = SimpleNamespace(
            judiciary_ra_10173_url="https://example.com/ra-10173",
            judiciary_ra_10175_url="https://example.com/ra-10175",
            judiciary_ra_8792_url="https://example.com/ra-8792",
            judiciary_ra_9470_url="https://example.com/ra-9470",
            judiciary_ra_10844_url="https://example.com/ra-10844",
            judiciary_ra_11032_url="https://example.com/ra-11032",
            judiciary_ra_11930_url="https://example.com/ra-11930",
        )
        service = RetrievalService(settings, SimpleNamespace(), SimpleNamespace())
        sources = service._official_sources()

        for number in ("10173", "10175", "8792", "9470", "10844", "11032", "11930"):
            with self.subTest(number=number):
                selected = service._select_official_sources(f"Explain RA {number}", sources)
                self.assertEqual(len(selected), 1)
                self.assertEqual(selected[0].source_type, f"RA_{number}")

    def test_selects_multiple_explicit_acts_for_comparison(self) -> None:
        settings = SimpleNamespace(
            judiciary_ra_10173_url="https://example.com/ra-10173",
            judiciary_ra_10175_url="https://example.com/ra-10175",
            judiciary_ra_8792_url="https://example.com/ra-8792",
            judiciary_ra_9470_url="https://example.com/ra-9470",
            judiciary_ra_10844_url="https://example.com/ra-10844",
            judiciary_ra_11032_url="https://example.com/ra-11032",
            judiciary_ra_11930_url="https://example.com/ra-11930",
        )
        service = RetrievalService(settings, SimpleNamespace(), SimpleNamespace())

        selected = service._select_official_sources(
            "Compare RA 10173 and Republic Act 10175",
            service._official_sources(),
        )

        self.assertEqual(
            [source.source_type for source in selected],
            ["RA_10173", "RA_10175"],
        )


if __name__ == "__main__":
    unittest.main()
