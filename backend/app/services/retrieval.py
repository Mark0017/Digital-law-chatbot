import asyncio
import logging
import io
import re
import ssl
import time
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlparse

import httpx
import truststore
from bs4 import BeautifulSoup
from pypdf import PdfReader

from app.core.config import Settings
from app.schemas.chat import Source
from app.services.gemini import GeminiService
from app.services.supabase import SupabaseService
from app.services.evidence import RelevanceAssessment, UNABLE_TO_VERIFY, PDF_UNABLE_TO_VERIFY, is_privacy_pdf_question
from app.services.evidence import source_tier
from app.services.npc import parse_index, match_issuances, identity_question


_official_text_cache: dict[str, tuple[float, str]] = {}
_official_text_locks: dict[str, asyncio.Lock] = {}
_knowledge_base_cache: tuple[float, bool] | None = None
_knowledge_base_lock = asyncio.Lock()
_KNOWLEDGE_BASE_CACHE_SECONDS = 30
logger = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    context: str
    sources: list[Source]
    answer: str | None = None


@dataclass(frozen=True)
class OfficialLegalSource:
    source_type: str
    title: str
    url: str
    markers: tuple[str, ...]


class RetrievalService:
    def __init__(
        self,
        settings: Settings,
        gemini: GeminiService,
        supabase: SupabaseService,
    ) -> None:
        self.settings = settings
        self.gemini = gemini
        self.supabase = supabase

    async def retrieve(self, question: str) -> RetrievalResult:
        pdf_only = is_privacy_pdf_question(question)
        search_question = f"Data Privacy Act of 2012 (RA 10173): {question}" if pdf_only else question
        chunks: list[dict] = []
        try:
            if await self._knowledge_base_is_ready():
                embedding = await self.gemini.embed_question(search_question)
                filters = {"source_types": ["RA_10173"]} if pdf_only else {}
                chunks = await self.supabase.match_document_chunks(
                    embedding,
                    self.settings.retrieval_match_threshold,
                    self.settings.retrieval_match_count,
                    **filters,
                )
        except Exception as exc:
            logger.warning("Vector retrieval unavailable: %s", type(exc).__name__)

        if pdf_only:
            return await self._retrieve_from_primary_pdf(search_question, chunks)

        knowledge = self._chunks_to_evidence(chunks)
        assessment = await self._assess(question, knowledge)
        current = self._requires_current_web_search(question) or assessment.requires_current_web
        knowledge = self._retain_sources(knowledge, assessment.relevant_sources)
        logger.info("Privacy retrieval: chunks=%d relevant_sources=%d sufficient=%s current=%s",
                    len(chunks), len(knowledge.sources), assessment.is_sufficient, current)
        if assessment.is_sufficient and not current:
            return knowledge
        if not self.settings.web_search_enabled:
            return RetrievalResult("", [], UNABLE_TO_VERIFY)

        # Search the complete official issuance catalog before relying on Gemini's
        # search quota. Index identity lookups are directly verifiable facts.
        direct = await self._direct_privacy_evidence(question, fresh=current)
        if direct.answer:
            return direct
        combined = self._merge_evidence(knowledge, direct)
        if direct.context:
            evaluation = await self._assess(question, combined)
            if evaluation.is_sufficient and any(
                combined.sources[i - 1].origin == "web" for i in evaluation.relevant_sources
            ):
                return self._retain_sources(combined, evaluation.relevant_sources)
            combined = self._retain_sources(combined, evaluation.relevant_sources)

        # Search NPC first, then expand to government/Lawphil only if insufficient.
        # Partial, relevant PDF evidence is retained for fusion and conflict analysis.
        for tier in (1, 2):
            web = await self._search_official_web(question, tier=tier)
            combined = self._merge_evidence(combined, web)
            evaluation = await self._assess(question, combined)
            has_web = any(
                combined.sources[i - 1].origin == "web" for i in evaluation.relevant_sources
            )
            if evaluation.is_sufficient and has_web:
                logger.info("Privacy retrieval: route=%s tier=%d",
                            "hybrid" if knowledge.sources else "web", tier)
                return self._retain_sources(combined, evaluation.relevant_sources)
            combined = self._retain_sources(combined, evaluation.relevant_sources)

        logger.info("Privacy retrieval: route=unable_to_verify")
        return RetrievalResult("", [], UNABLE_TO_VERIFY)

    async def _retrieve_from_primary_pdf(self, question: str, chunks: list[dict]) -> RetrievalResult:
        if self._requires_current_web_search(question):
            return RetrievalResult("", [], PDF_UNABLE_TO_VERIFY)
        chunks = [c for c in chunks if c.get("storage_path") == self.settings.primary_privacy_pdf_path]
        numbers = [int(n) for n in re.findall(r"\b(?:section|sec\.?)\s+(\d+)\b", question, re.I)]
        evidence = self._chunks_to_evidence(chunks)
        if evidence.context and not numbers:
            assessment = await self._assess(question, evidence)
            if assessment.is_sufficient and not assessment.requires_current_web:
                return self._retain_sources(evidence, assessment.relevant_sources)
        # Exact section lookups should not miss a provision due to a vector threshold.
        # The selected small PDF can also supply missing context without a web fallback.
        try:
            chunks = await self.supabase.get_pdf_chunks(self.settings.primary_privacy_pdf_path, numbers)
        except Exception as exc:
            logger.warning("Primary PDF lookup unavailable: %s", type(exc).__name__)
            return RetrievalResult("", [], PDF_UNABLE_TO_VERIFY)
        evidence = self._chunks_to_evidence(chunks, exact_pdf=True)
        assessment = await self._assess(question, evidence)
        if assessment.is_sufficient and not assessment.requires_current_web:
            return self._retain_sources(evidence, assessment.relevant_sources)
        return RetrievalResult("", [], PDF_UNABLE_TO_VERIFY)

    async def _assess(self, question: str, result: RetrievalResult) -> RelevanceAssessment:
        if result.context and result.sources:
            try:
                return await self.gemini.evaluate_relevance(question, result.context, result.sources)
            except Exception as exc:
                logger.warning("Evidence evaluation unavailable: %s", type(exc).__name__)
        return RelevanceAssessment(is_sufficient=False, relevant_sources=[],
                                   requires_current_web=False, reason="No verified sufficient evidence")

    def _chunks_to_evidence(self, chunks: list[dict], exact_pdf: bool = False) -> RetrievalResult:
        sources: list[Source] = []
        parts: list[str] = []
        seen: dict[tuple, int] = {}
        for chunk in chunks[:200 if exact_pdf else self.settings.retrieval_match_count]:
            try:
                similarity = float(chunk.get("similarity", -1))
                if (not chunk.get("is_authoritative") or not chunk.get("content", "").strip()
                        or (not exact_pdf and not self.settings.retrieval_match_threshold <= similarity <= 1)):
                    continue
                source = Source(
                    title=chunk["source_title"], url=chunk.get("source_url") or None,
                    section=chunk.get("section"), page=chunk.get("page_number"),
                    origin="knowledge_base", document_id=chunk["document_id"],
                    publication_date=chunk.get("publication_date"),
                )
                key = (source.document_id, source.section, source.page, str(source.url))
                if key not in seen:
                    sources.append(source)
                    seen[key] = len(sources)
                confidence = "Selected uploaded PDF text" if exact_pdf else f"Similarity: {similarity:.3f}"
                parts.append(f"[Source {seen[key]}: Knowledge Base Source - {source.title}]\n"
                             f"{confidence}\n{chunk['content'][:12000]}")
            except (ValueError, TypeError, KeyError):
                continue
        return RetrievalResult("\n\n".join(parts), sources)

    @staticmethod
    def _retain_sources(result: RetrievalResult, indices: list[int]) -> RetrievalResult:
        indices = sorted(set(indices))
        mapping = {old: new for new, old in enumerate(indices, 1)}
        parts = []
        for block in re.split(r"(?=\[Source \d+:)", result.context):
            match = re.match(r"\[Source (\d+):", block)
            if match and int(match.group(1)) in mapping:
                parts.append(re.sub(r"^\[Source \d+:", f"[Source {mapping[int(match.group(1))]}:", block))
        return RetrievalResult("\n\n".join(parts), [result.sources[i - 1] for i in indices])

    @staticmethod
    def _merge_evidence(left: RetrievalResult, right: RetrievalResult) -> RetrievalResult:
        offset = len(left.sources)
        context = re.sub(r"\[Source (\d+):", lambda m: f"[Source {int(m.group(1)) + offset}:", right.context)
        return RetrievalResult("\n\n".join(filter(None, [left.context, context])), left.sources + right.sources)

    async def _direct_privacy_evidence(self, question: str, fresh: bool) -> RetrievalResult:
        source = OfficialLegalSource("RA_10173", "Data Privacy Act of 2012",
                                     self.settings.npc_dpa_url, ())
        if re.search(r"\bnpc\b|national privacy commission|circular|advisory|issuance|compliance checks", question, re.I):
            source = OfficialLegalSource("NPC_ISSUANCES", "NPC Advisories and Circulars",
                                         self.settings.npc_issuances_url, ())
        text = await self._get_official_source_text(source, fresh=fresh)
        if source.source_type == "NPC_ISSUANCES":
            matches = match_issuances(question, parse_index(text, source.url))
            if matches:
                parts = []
                sources = []
                for entry in matches:
                    sources.append(Source(title=f"NPC index: {entry.identifier} - {entry.title}",
                                          url=source.url, origin="web", retrieved_at=date.today()))
                    parts.append(f"[Source {len(sources)}: Web Source - NPC issuance index]\n"
                                 f"The official index lists {entry.identifier}: {entry.title}.\n"
                                 f"Linked document: {entry.url}")
                    if len(matches) == 1 and identity_question(question, entry):
                        return RetrievalResult("\n\n".join(parts), sources,
                            f"{entry.identifier} is titled “{entry.title},” according to the "
                            "National Privacy Commission's official issuance index. [1]")
                    document = OfficialLegalSource("NPC_ISSUANCE", f"{entry.identifier} - {entry.title}", entry.url, ())
                    body = await self._get_official_source_text(document, fresh=fresh)
                    if body:
                        sources.append(Source(title=document.title, url=entry.url, origin="web", retrieved_at=date.today()))
                        parts.append(f"[Source {len(sources)}: Web Source - {document.title}]\n"
                                     + self._select_relevant_passages(question, body))
                return RetrievalResult("\n\n".join(parts), sources)
        text = self._select_relevant_passages(question, text)
        if not text:
            return RetrievalResult("", [])
        return RetrievalResult(f"[Source 1: Web Source - {source.title}]\n{text}", [
            Source(title=source.title, url=source.url, origin="web", retrieved_at=date.today())
        ])

    async def _search_official_web(self, question: str, tier: int = 1) -> RetrievalResult:
        if not getattr(self.settings, "web_search_enabled", True):
            return RetrievalResult(context="", sources=[])
        search = getattr(self.gemini, "search_official_web", None)
        if search is None:
            return RetrievalResult(context="", sources=[])
        try:
            context, sources = await search(question, tier=tier)
        except Exception as exc:
            logger.warning("Official web fallback failed: %s", type(exc).__name__)
            return RetrievalResult(context="", sources=[])
        return RetrievalResult(context=context, sources=sources)

    @staticmethod
    def _requires_current_web_search(question: str) -> bool:
        normalized = " ".join(re.findall(r"[a-z0-9]+", question.lower()))
        freshness_markers = (
            "latest", "newest", "most recent", "currently", "current",
            "recently released", "as of today", "this year", "new circular",
            "new advisory", "new issuance",
            "recent", "amendment", "amended", "newly issued", "changes",
            "updated", "new guidance", "new rules",
        )
        return any(marker in normalized for marker in freshness_markers)

    @classmethod
    def _is_latest_npc_circular_question(cls, question: str) -> bool:
        normalized = " ".join(re.findall(r"[a-z0-9]+", question.lower()))
        asks_about_npc = (
            re.search(r"\bnpc\b", normalized) is not None
            or "national privacy" in normalized
        )
        return (
            asks_about_npc
            and "circular" in normalized
            and cls._requires_current_web_search(normalized)
        )

    @staticmethod
    def _official_http_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
            headers={"User-Agent": "DigitalLawPH-Assistant/1.0"},
        )

    async def _get_official_source_text(self, source: OfficialLegalSource, fresh: bool = False) -> str:
        cached = _official_text_cache.get(source.url)
        if not fresh and cached and time.monotonic() - cached[0] < 300:
            return cached[1]

        source_lock = _official_text_locks.setdefault(source.url, asyncio.Lock())
        async with source_lock:
            cached = _official_text_cache.get(source.url)
            if not fresh and cached and time.monotonic() - cached[0] < 300:
                return cached[1]
            try:
                async with self._official_http_client() as client:
                    response = await client.get(source.url)
                response.raise_for_status()
                if source_tier(str(response.url)) is None or len(response.content) > self.settings.max_upload_bytes:
                    return ""
            except httpx.HTTPError:
                source_text = await self._get_official_text_through_gateway(source)
                if not source_text:
                    return ""
            else:
                if response.content.startswith(b"%PDF-"):
                    try:
                        reader = PdfReader(io.BytesIO(response.content))
                        source_text = "\n\n".join(
                            f"Page {i}\n{page.extract_text() or ''}" for i, page in enumerate(reader.pages, 1)
                        )
                    except Exception as exc:
                        logger.warning("Official PDF extraction failed: %s", type(exc).__name__)
                        return ""
                    _official_text_cache[source.url] = (time.monotonic(), source_text)
                    return source_text
                soup = BeautifulSoup(response.text, "html.parser")
                for element in soup(
                    ["script", "style", "nav", "footer", "form", "noscript"]
                ):
                    element.decompose()
                for anchor in soup.find_all("a", href=True):
                    anchor.replace_with(f"[{anchor.get_text(' ', strip=True)}]({anchor['href']})")
                units = [
                    " ".join(line.split())
                    for line in soup.get_text("\n", strip=True).splitlines()
                    if len(" ".join(line.split())) >= 2
                ]
                source_text = "\n".join(units)
            _official_text_cache[source.url] = (time.monotonic(), source_text)
            return source_text

    async def _get_official_text_through_gateway(
        self,
        source: OfficialLegalSource,
    ) -> str:
        parsed_source_url = urlparse(source.url)
        hostname = (parsed_source_url.hostname or "").lower()
        if (
            parsed_source_url.scheme != "https"
            or (hostname != "gov.ph" and not hostname.endswith(".gov.ph"))
        ):
            return ""

        gateway = getattr(self.settings, "official_text_gateway_url", None)
        if not gateway:
            return ""
        gateway = str(gateway).strip().rstrip("/")
        if urlparse(gateway).scheme != "https":
            return ""

        try:
            async with self._official_http_client() as client:
                response = await client.get(f"{gateway}/{source.url}")
            response.raise_for_status()
        except httpx.HTTPError:
            return ""

        if len(response.content) > 1_000_000:
            return ""
        source_header = re.search(
            r"(?im)^URL Source:\s*(https?://\S+)\s*$",
            response.text[:2_000],
        )
        if (
            source_header is None
            or source_header.group(1).rstrip("/") != source.url.rstrip("/")
        ):
            return ""
        return response.text

    async def _knowledge_base_is_ready(self) -> bool:
        global _knowledge_base_cache

        now = time.monotonic()
        if (
            _knowledge_base_cache is not None
            and now - _knowledge_base_cache[0] < _KNOWLEDGE_BASE_CACHE_SECONDS
        ):
            return _knowledge_base_cache[1]

        async with _knowledge_base_lock:
            now = time.monotonic()
            if (
                _knowledge_base_cache is not None
                and now - _knowledge_base_cache[0] < _KNOWLEDGE_BASE_CACHE_SECONDS
            ):
                return _knowledge_base_cache[1]

            is_ready = await self.supabase.has_ready_documents()
            _knowledge_base_cache = (now, is_ready)
            return is_ready

    @staticmethod
    def _select_relevant_passages(question: str, page_text: str) -> str:
        if not page_text:
            return ""

        stop_words = {
            "about", "act", "after", "also", "does", "explain", "from", "have",
            "into", "law", "overview", "philippine", "republic", "should",
            "summarize", "that", "their", "there", "these", "this", "under",
            "what", "when", "where", "which", "with", "would",
        }
        passages = [line for line in page_text.splitlines() if line]
        terms: set[str] = set()
        for word in re.findall(r"[a-z0-9]+", question.lower()):
            if len(word) < 3 or word in stop_words or word.isdigit():
                continue
            terms.add(word)
            if word.endswith("ies") and len(word) > 4:
                terms.add(f"{word[:-3]}y")
            elif word.endswith("s") and not word.endswith("ss"):
                terms.add(word[:-1])

        section_pattern = re.compile(
            r"^[^A-Za-z0-9]{0,3}(?:"
            r"SEC(?:TION)?\.?\s+\d+(?:\.\d+)*\.?"
            r"|ARTICLE\s+\d+[A-Z]?(?:\.\d+)*\.?"
            r"|CHAPTER\s+(?:[IVXLCDM]+|\d+)"
            r"|RULE\s+(?:[IVXLCDM]+|\d+)"
            r")",
            re.IGNORECASE,
        )
        sections: list[list[str]] = []
        current_section: list[str] = []
        for passage in passages:
            if section_pattern.match(passage) and current_section:
                sections.append(current_section)
                current_section = []
            current_section.append(passage)
        if current_section:
            sections.append(current_section)

        scored: list[tuple[int, int, list[str]]] = []
        for index, section in enumerate(sections):
            section_text = "\n".join(section)
            lowered = section_text.lower()
            heading = " ".join(section[:3]).lower()
            score = sum(min(lowered.count(term), 5) for term in terms)
            score += 3 * sum(1 for term in terms if term in heading)
            if score:
                scored.append((score, index, section))

        if not scored:
            return "\n\n".join(
                "\n".join(section)
                for section in sections[:5]
            )[:20_000]

        top_sections = sorted(scored, key=lambda item: (-item[0], item[1]))[:4]
        selected_sections = sorted(top_sections, key=lambda item: item[1])
        return "\n\n".join(
            "\n".join(section)
            for _score, _index, section in selected_sections
        )[:20_000]
