"""
Normalize raw feed docs into structured Event pydantic models.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone, timedelta
from typing import Any

import structlog
from dateutil import parser as dtparser
from pydantic import BaseModel, field_validator, ValidationError

log = structlog.get_logger(__name__)

BODY_MAX = 8192
MIN_TITLE_LEN = 10
MIN_BODY_LEN = 20
MAX_AGE_DAYS = 14


class RawDoc(BaseModel):
    source: str
    theme: str
    url: str
    title: str
    body: str = ""
    published_at: datetime | None = None


class Event(BaseModel):
    id: str
    source: str
    theme: str
    url: str
    title: str
    body: str
    published_at: str   # ISO8601
    fetched_at: str     # ISO8601
    raw_hash: str

    @field_validator("title")
    @classmethod
    def title_nonempty(cls, v: str) -> str:
        v = v.strip()
        if len(v) < MIN_TITLE_LEN:
            raise ValueError(f"title too short: {v!r}")
        return v


class Score(BaseModel):
    event_id: str
    theme: str
    scored_at: str
    salience: int
    direction: int
    rationale: str
    model: str

    @field_validator("salience")
    @classmethod
    def salience_range(cls, v: int) -> int:
        return max(0, min(100, v))

    @field_validator("direction")
    @classmethod
    def direction_valid(cls, v: int) -> int:
        if v not in (-1, 0, 1):
            return 0
        return v


def _clean_text(html_or_text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html_or_text or "")
    text = re.sub(r"&[a-z]+;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _parse_dt(raw: Any) -> datetime | None:
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw.astimezone(timezone.utc) if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    try:
        return dtparser.parse(str(raw)).astimezone(timezone.utc)
    except Exception:
        return None


def normalize_event(doc: RawDoc) -> Event | None:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=MAX_AGE_DAYS)
    pub = doc.published_at or now
    if pub < cutoff:
        log.debug("event_too_old", source=doc.source, pub=pub.isoformat())
        return None

    body = _clean_text(doc.body)[:BODY_MAX]
    if len(body) < MIN_BODY_LEN and len(doc.title.strip()) < MIN_TITLE_LEN:
        return None

    raw_hash = hashlib.sha256(
        f"{doc.title.strip()[:256]}|{body[:512]}".encode()
    ).hexdigest()

    eid = hashlib.sha256(
        f"{doc.source}|{doc.url}|{pub.isoformat()}".encode()
    ).hexdigest()

    try:
        return Event(
            id=eid,
            source=doc.source,
            theme=doc.theme,
            url=doc.url,
            title=doc.title.strip(),
            body=body,
            published_at=pub.isoformat(),
            fetched_at=now.isoformat(),
            raw_hash=raw_hash,
        )
    except ValidationError as exc:
        log.warning("normalize_failed", source=doc.source, err=str(exc))
        return None


def normalize_batch(docs: list[RawDoc]) -> list[Event]:
    events = []
    seen: set[str] = set()
    for doc in docs:
        ev = normalize_event(doc)
        if ev is None:
            continue
        if ev.raw_hash in seen:
            continue
        seen.add(ev.raw_hash)
        events.append(ev)
    return events
