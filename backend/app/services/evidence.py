"""Evidence policy shared by retrieval and generation; no external calls."""

import re
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field


UNABLE_TO_VERIFY = (
    "I could not verify this from the available knowledge base or authoritative "
    "Philippine sources. Please check the latest National Privacy Commission "
    "issuance before relying on this information."
)

PDF_UNABLE_TO_VERIFY = (
    "I could not verify an answer from your uploaded Data Privacy Act PDF. "
    "This question uses the uploaded PDF only; no web sources were used."
)


def is_privacy_pdf_question(question: str) -> bool:
    normalized = " ".join(re.findall(r"[a-z0-9]+", question.lower()))
    if re.search(r"\b(?:r a|ra|republic act)(?: no)?\s*10173\b|\bdata privacy act\b", normalized):
        return True
    if re.search(r"\b(?:npc|national privacy commission|circular|advisory|irr)\b", normalized):
        return False
    if re.search(r"\b(?:r a|ra|republic act)(?: no)?\s*\d+\b", normalized):
        return False
    return bool(re.search(r"\b(?:data privacy|dataprivacy|datapricavy)\b|\b(?:section|sec)\s+\d+\b", normalized))


class RelevanceAssessment(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    is_sufficient: bool
    relevant_sources: list[int]
    requires_current_web: bool
    reason: str


class CitedClaim(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    text: str = Field(min_length=1)
    citations: list[int] = Field(min_length=1)


class CitedAnswer(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    claims: list[CitedClaim]


def source_tier(url: str) -> int | None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or parsed.username or parsed.password or port not in (None, 443):
        return None
    if host == "privacy.gov.ph" or host.endswith(".privacy.gov.ph"):
        return 1
    if host == "gov.ph" or host.endswith(".gov.ph"):
        return 2
    # Lawphil is an institutional legal repository, not a government agency.
    if host in {"lawphil.net", "www.lawphil.net"}:
        return 2
    return None


def focused_queries(question: str, tier: int = 1) -> list[str]:
    """Bounded, domain-controlled queries. Never forward names/emails/URLs verbatim."""
    topics = (
        (r"fingerprint|biometric|facial recognition", "biometric fingerprint personal information"),
        (r"employ|company|workplace", "employee employer personal data"),
        (r"student|school", "student school personal data"),
        (r"cctv|camera", "CCTV surveillance privacy"),
        (r"breach|cybersecurity|hack", "personal data breach notification security"),
        (r"consent|lawful|collect|process", "consent lawful processing"),
        (r"rights?|access|eras|portability", "data subject rights"),
        (r"penalt|punish|fine|imprison", "Data Privacy Act penalties"),
        (r"officer|\bdpo\b", "data protection officer requirements"),
        (r"impact|\bpia\b", "privacy impact assessment"),
        (r"cross.border|transfer|abroad", "cross border data transfers"),
        (r"notice", "privacy notice"),
        (r"circular", "NPC circular"),
        (r"compliance\s+checks?", "Guidelines on Compliance Checks"),
        (r"advisor", "NPC advisory"),
        (r"decision|case", "NPC decision"),
        (r"memorand", "NPC memorandum"),
        (r"irr|implementing", "Data Privacy Act implementing rules regulations"),
    )
    selected = [terms for pattern, terms in topics if re.search(pattern, question, re.I)]
    identifiers = re.findall(r"\b(?:20)?\d{2}[-–]\d{1,3}\b|\b10173\b", question)
    current = bool(re.search(r"latest|newest|recent|current|amend|newly|changes|today|this year", question, re.I))
    terms = " ".join(selected[:4] or ["Data Privacy Act Philippines guidance"])
    terms += " " + " ".join(identifiers[:3])
    if current:
        terms += " latest current amendments"
    domains = ["privacy.gov.ph"] if tier == 1 else ["gov.ph", "lawphil.net"]
    return [f"site:{domain} Philippines {terms.strip()}" for domain in domains]
