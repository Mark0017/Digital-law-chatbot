import asyncio
import re
import ssl
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
import truststore
from bs4 import BeautifulSoup

from app.core.config import Settings
from app.schemas.chat import Source
from app.services.gemini import GeminiService
from app.services.supabase import SupabaseService


_official_text_cache: dict[str, str] = {}
_official_text_locks: dict[str, asyncio.Lock] = {}
_knowledge_base_cache: tuple[float, bool] | None = None
_knowledge_base_lock = asyncio.Lock()
_KNOWLEDGE_BASE_CACHE_SECONDS = 30


@dataclass
class RetrievalResult:
    context: str
    sources: list[Source]


@dataclass(frozen=True)
class OfficialLegalSource:
    source_type: str
    title: str
    url: str
    markers: tuple[str, ...]


_dynamic_source_cache: dict[str, OfficialLegalSource] = {}
_dynamic_source_locks: dict[str, asyncio.Lock] = {}


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
        explicit_numbers = self._extract_ra_numbers(question)
        chunks: list[dict] = []
        try:
            if await self._knowledge_base_is_ready():
                embedding = await self.gemini.embed_question(question)
                chunks = await self.supabase.match_document_chunks(
                    embedding,
                    self.settings.retrieval_match_threshold,
                    self.settings.retrieval_match_count,
                )
        except (httpx.HTTPError, RuntimeError):
            chunks = []

        chunk_source_types = {chunk.get("source_type") for chunk in chunks}
        chunks_cover_explicit_acts = all(
            f"RA_{number}" in chunk_source_types for number in explicit_numbers
        )
        if chunks and chunks_cover_explicit_acts:
            context_parts: list[str] = []
            sources: list[Source] = []
            seen_sources: set[tuple[str, str | None, str]] = set()
            for index, chunk in enumerate(chunks, start=1):
                context_parts.append(
                    f"[Source {index}: {chunk['source_title']}]\n{chunk['content']}"
                )
                url = chunk.get("source_url") or self.settings.npc_dpa_url
                source_key = (chunk["source_title"], chunk.get("section"), url)
                if source_key not in seen_sources:
                    seen_sources.add(source_key)
                    sources.append(
                        Source(
                            title=chunk["source_title"],
                            section=chunk.get("section"),
                            url=url,
                            page=chunk.get("page_number"),
                        )
                    )
            return RetrievalResult(context="\n\n".join(context_parts), sources=sources)

        official_sources = self._select_official_sources(
            question,
            self._official_sources(),
        )
        selected_numbers = {
            source.source_type.removeprefix("RA_") for source in official_sources
        }
        missing_numbers = [
            number for number in explicit_numbers if number not in selected_numbers
        ]
        if missing_numbers:
            dynamic_sources = await asyncio.gather(
                *(self._resolve_dynamic_source(number) for number in missing_numbers)
            )
            official_sources.extend(
                source for source in dynamic_sources if source is not None
            )
        if not official_sources:
            return RetrievalResult(context="", sources=[])

        source_texts = await asyncio.gather(
            *(self._get_official_source_text(source) for source in official_sources)
        )
        context_parts: list[str] = []
        sources: list[Source] = []
        for source, source_text in zip(official_sources, source_texts, strict=True):
            relevant_text = self._select_relevant_passages(question, source_text)
            if not relevant_text:
                continue
            source_number = len(sources) + 1
            context_parts.append(
                f"[Source {source_number}: {source.title}]\n{relevant_text}"
            )
            sources.append(Source(title=source.title, url=source.url))

        return RetrievalResult(context="\n\n".join(context_parts), sources=sources)

    async def _resolve_dynamic_source(
        self,
        ra_number: str,
    ) -> OfficialLegalSource | None:
        if ra_number in _dynamic_source_cache:
            return _dynamic_source_cache[ra_number]

        source_lock = _dynamic_source_locks.setdefault(ra_number, asyncio.Lock())
        async with source_lock:
            if ra_number in _dynamic_source_cache:
                return _dynamic_source_cache[ra_number]

            source: OfficialLegalSource | None = None
            try:
                async with self._official_http_client() as client:
                    index_response = await client.get(
                        self.settings.judiciary_republic_acts_url
                    )
                    index_response.raise_for_status()
                    csrf_match = re.search(
                        r"['\"]csrf_test_name['\"]\s*:\s*['\"]([^'\"]+)['\"]",
                        index_response.text,
                    )
                    if csrf_match is None:
                        return None

                    search_response = await client.post(
                        self.settings.judiciary_republic_acts_search_url,
                        data={
                            "csrf_test_name": csrf_match.group(1),
                            "draw": "1",
                            "start": "0",
                            "length": "25",
                            "search[value]": ra_number,
                            "search[regex]": "false",
                        },
                    )
                    search_response.raise_for_status()
                    source = self._parse_dynamic_source(
                        ra_number,
                        search_response.json(),
                    )
            except (httpx.HTTPError, ValueError, TypeError):
                source = None

            if source is not None:
                _dynamic_source_cache[ra_number] = source
            return source

    @staticmethod
    def _parse_dynamic_source(
        ra_number: str,
        payload: object,
    ) -> OfficialLegalSource | None:
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            return None

        exact_label = re.compile(
            rf"^REPUBLIC\s+ACT\s+NO\.?\s*{re.escape(ra_number)}$",
            re.IGNORECASE,
        )
        for row in payload["data"]:
            if not isinstance(row, list) or len(row) < 3:
                continue
            label = " ".join(str(row[0]).split())
            if exact_label.fullmatch(label) is None:
                continue

            link = BeautifulSoup(str(row[2]), "html.parser").find("a", href=True)
            if link is None:
                continue
            url = str(link["href"])
            parsed_url = urlparse(url)
            if (
                parsed_url.scheme != "https"
                or parsed_url.hostname != "elibrary.judiciary.gov.ph"
                or not parsed_url.path.startswith("/thebookshelf/showdocs/2/")
            ):
                continue
            return OfficialLegalSource(
                source_type=f"RA_{ra_number}",
                title=f"Republic Act No. {ra_number}",
                url=url,
                markers=(),
            )
        return None

    @staticmethod
    def _official_http_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
            headers={"User-Agent": "DigitalLawPH-Assistant/1.0"},
        )

    async def _get_official_source_text(self, source: OfficialLegalSource) -> str:
        if source.url in _official_text_cache:
            return _official_text_cache[source.url]

        source_lock = _official_text_locks.setdefault(source.url, asyncio.Lock())
        async with source_lock:
            if source.url in _official_text_cache:
                return _official_text_cache[source.url]
            try:
                async with self._official_http_client() as client:
                    response = await client.get(source.url)
                response.raise_for_status()
            except httpx.HTTPError:
                return ""

            soup = BeautifulSoup(response.text, "html.parser")
            for element in soup(["script", "style", "nav", "footer", "form", "noscript"]):
                element.decompose()
            units = [
                " ".join(line.split())
                for line in soup.get_text("\n", strip=True).splitlines()
                if len(" ".join(line.split())) >= 2
            ]
            source_text = "\n".join(units)
            _official_text_cache[source.url] = source_text
            return source_text

    def _official_sources(self) -> tuple[OfficialLegalSource, ...]:
        return (
            OfficialLegalSource(
                source_type="RA_10173",
                title="Republic Act No. 10173 - Data Privacy Act of 2012",
                url=self.settings.judiciary_ra_10173_url,
                markers=(
                    "data privacy", "personal data", "personal information",
                    "data subject", "consent", "privacy notice", "dpo",
                ),
            ),
            OfficialLegalSource(
                source_type="RA_10175",
                title="Republic Act No. 10175 - Cybercrime Prevention Act of 2012",
                url=self.settings.judiciary_ra_10175_url,
                markers=(
                    "cybercrime", "illegal access", "illegal interception",
                    "data interference", "system interference", "cyber libel",
                    "computer related", "misuse of devices",
                ),
            ),
            OfficialLegalSource(
                source_type="RA_8792",
                title="Republic Act No. 8792 - Electronic Commerce Act of 2000",
                url=self.settings.judiciary_ra_8792_url,
                markers=(
                    "electronic commerce", "e commerce", "electronic document",
                    "electronic data", "electronic signature", "digital signature",
                    "electronic transaction",
                ),
            ),
            OfficialLegalSource(
                source_type="RA_9470",
                title="Republic Act No. 9470 - National Archives of the Philippines Act of 2007",
                url=self.settings.judiciary_ra_9470_url,
                markers=(
                    "national archives", "public archive", "public record",
                    "archival record", "records management", "government record",
                ),
            ),
            OfficialLegalSource(
                source_type="RA_10844",
                title="Republic Act No. 10844 - DICT Act of 2015",
                url=self.settings.judiciary_ra_10844_url,
                markers=(
                    "department of information and communications technology",
                    "dict", "ict governance", "ict infrastructure",
                    "digital infrastructure", "national ict",
                ),
            ),
            OfficialLegalSource(
                source_type="RA_11032",
                title="Republic Act No. 11032 - Ease of Doing Business Act of 2018",
                url=self.settings.judiciary_ra_11032_url,
                markers=(
                    "ease of doing business", "government service", "red tape",
                    "citizen charter", "automatic approval", "processing time",
                    "business one stop shop",
                ),
            ),
            OfficialLegalSource(
                source_type="RA_11930",
                title="Republic Act No. 11930 - Anti-OSAEC and Anti-CSAEM Act",
                url=self.settings.judiciary_ra_11930_url,
                markers=(
                    "osaec", "csaem", "online sexual abuse", "online child exploitation",
                    "child sexual abuse material", "grooming", "child exploitation",
                ),
            ),
        )

    @staticmethod
    def _extract_ra_numbers(question: str) -> list[str]:
        return list(dict.fromkeys(
            re.findall(
                r"(?:ra|republic\s+act)\s*(\d{4,5})",
                question.lower(),
            )
        ))

    @staticmethod
    def _select_official_sources(
        question: str,
        sources: tuple[OfficialLegalSource, ...],
    ) -> list[OfficialLegalSource]:
        lowered = question.lower()
        explicit_numbers = RetrievalService._extract_ra_numbers(lowered)
        if explicit_numbers:
            source_by_number = {
                source.source_type.removeprefix("RA_"): source
                for source in sources
            }
            return [
                source_by_number[number]
                for number in explicit_numbers
                if number in source_by_number
            ]

        normalized = " ".join(re.findall(r"[a-z0-9]+", lowered))
        scored = [
            (
                sum(1 for marker in source.markers if marker in normalized),
                index,
                source,
            )
            for index, source in enumerate(sources)
        ]
        matches = [item for item in scored if item[0] > 0]
        return [
            source
            for _score, _index, source in sorted(
                matches,
                key=lambda item: (-item[0], item[1]),
            )[:3]
        ]

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
