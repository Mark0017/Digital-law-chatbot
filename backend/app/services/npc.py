"""Find issuances in the fetched NPC index, without model-generated URLs."""

import re
from dataclasses import dataclass
from urllib.parse import urljoin

from app.services.evidence import source_tier


ISSUANCE = re.compile(
    r"NPC\s+(Circular|Advisory)(?:\s+No\.?)?\s+((?:20)?\d{2})\s*[-–]\s*(\d{1,3})",
    re.I,
)


def normalized(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


@dataclass(frozen=True)
class NpcIssuance:
    kind: str
    year: int
    number: int
    identifier: str
    title: str
    url: str


def parse_index(text: str, index_url: str) -> list[NpcIssuance]:
    entries = []
    seen = set()
    matches = list(ISSUANCE.finditer(text))
    for position, match in enumerate(matches):
        end = matches[position + 1].start() if position + 1 < len(matches) else len(text)
        tail = text[match.end():min(end, match.end() + 1000)]
        # Skip appendices/amendments as separate documents when matching a base ID.
        if re.match(r"\s*(?:Appendix|Annex|Amendment)\b", tail, re.I):
            continue
        link = re.search(r"\[([^\]]+)\]\(([^\s)]+)\)", tail)
        if link:
            title, url = link.group(1), urljoin(index_url, link.group(2))
        else:
            # HTML-to-markdown may wrap the entire identifier AND title in a link.
            link = re.match(r"\s*[-:–]?\s*([^\]\n]+)\]\(([^\s)]+)\)", tail)
            if not link:
                continue
            title, url = link.group(1), urljoin(index_url, link.group(2))
        if source_tier(url) != 1:
            continue
        year = int(match.group(2))
        if year < 100:
            year += 2000
        number = int(match.group(3))
        kind = match.group(1).title()
        key = (kind, year, number, url)
        if key in seen:
            continue
        seen.add(key)
        entries.append(NpcIssuance(kind, year, number,
            f"NPC {kind} No. {match.group(2)}-{number:02d}",
            " ".join(title.strip(" *-:").split()), url))
    return entries


def match_issuances(question: str, entries: list[NpcIssuance]) -> list[NpcIssuance]:
    identifier = ISSUANCE.search(question)
    if identifier:
        year = int(identifier.group(2))
        year = year + 2000 if year < 100 else year
        return [e for e in entries if e.kind.lower() == identifier.group(1).lower()
                and e.year == year and e.number == int(identifier.group(3))][:3]
    query = normalized(question)
    # A title must actually occur in the question, not merely share generic words.
    return [e for e in entries if len(normalized(e.title).split()) >= 3
            and normalized(e.title) in query][:3]


def identity_question(question: str, entry: NpcIssuance) -> bool:
    question = question.strip().rstrip("?. ")
    id_match = ISSUANCE.search(question)
    if id_match:
        return (not question[id_match.end():].strip()
                and normalized(question[:id_match.start()]) in {"what is", "whats", "identify", "name"})
    title = normalized(entry.title)
    question = normalized(question)
    return any(question == f"{prefix} {title}" for prefix in (
        "what npc number is", "what circular number is", "which npc circular is",
        "what npc circular is", "what is the npc circular number for",
    ))
