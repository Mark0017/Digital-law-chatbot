import unittest
from types import SimpleNamespace

from app.schemas.chat import ScopeClassification
from app.services.gemini import GeminiService


class GeminiRoutingTests(unittest.TestCase):
    def test_classifies_ra_10173_question_locally(self) -> None:
        result = GeminiService.classify_scope_locally(
            "What rights do data subjects have under RA 10173?"
        )

        self.assertEqual(result, ScopeClassification.DIGITAL_LAW_RELEVANT)

    def test_classifies_practical_privacy_question_locally(self) -> None:
        result = GeminiService.classify_scope_locally(
            "What should we do after a personal data breach?"
        )

        self.assertEqual(result, ScopeClassification.DIGITAL_LAW_RELATED)

    def test_other_laws_require_privacy_scope_evaluation(self) -> None:
        questions = (
            "What is illegal access under RA 10175?",
            "Are electronic signatures valid under RA 8792?",
            "What records are covered by RA 9470?",
            "What are the DICT's powers under RA 10844?",
            "What is automatic approval under RA 11032?",
            "What conduct is prohibited by RA 11930?",
        )

        for question in questions:
            with self.subTest(question=question):
                result = GeminiService.classify_scope_locally(question)
                self.assertIsNone(result)

    def test_classifies_unregistered_republic_act_for_official_lookup(self) -> None:
        result = GeminiService.classify_scope_locally(
            "What consumer-data protections are in RA 7394?"
        )

        self.assertEqual(result, ScopeClassification.DIGITAL_LAW_RELATED)

    def test_leaves_unrelated_question_for_model_classification(self) -> None:
        result = GeminiService.classify_scope_locally("How do I bake bread?")

        self.assertIsNone(result)

    def test_accepts_grounding_only_from_official_government_domains(self) -> None:
        response = SimpleNamespace(
            candidates=[SimpleNamespace(
                grounding_metadata=SimpleNamespace(
                    grounding_chunks=[SimpleNamespace(web=SimpleNamespace(
                        domain="privacy.gov.ph",
                        title="Advisories & Circulars - National Privacy Commission",
                        uri="https://privacy.gov.ph/pips-and-pics/advisories-circulars/",
                    ))],
                    grounding_supports=[SimpleNamespace(
                        grounding_chunk_indices=[0],
                    )],
                )
            )]
        )

        sources = GeminiService._extract_official_grounding_sources(response)

        self.assertEqual(len(sources), 1)
        self.assertEqual(
            sources[0].title,
            "Advisories & Circulars - National Privacy Commission",
        )

    def test_rejects_grounding_that_uses_non_government_sources(self) -> None:
        response = SimpleNamespace(
            candidates=[SimpleNamespace(
                grounding_metadata=SimpleNamespace(
                    grounding_chunks=[SimpleNamespace(web=SimpleNamespace(
                        domain="example.com",
                        title="Unofficial summary",
                        uri="https://example.com/npc-circular",
                    ))],
                    grounding_supports=[SimpleNamespace(
                        grounding_chunk_indices=[0],
                    )],
                )
            )]
        )

        self.assertEqual(
            GeminiService._extract_official_grounding_sources(response),
            [],
        )


if __name__ == "__main__":
    unittest.main()
