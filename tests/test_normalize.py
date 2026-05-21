"""Tests for normalize.py"""
import pytest
from datetime import datetime, timezone, timedelta

from iggavestment.normalize import (
    RawDoc, normalize_event, normalize_batch, Event
)


def _recent():
    return datetime.now(timezone.utc) - timedelta(hours=1)


def test_normalize_basic():
    doc = RawDoc(
        source="test", theme="bio",
        url="https://example.com/1",
        title="FDA approves novel cancer drug for rare indication",
        body="The FDA approved a new drug for treatment of rare cancer. Significant milestone for XBI.",
        published_at=_recent(),
    )
    ev = normalize_event(doc)
    assert ev is not None
    assert ev.theme == "bio"
    assert ev.source == "test"
    assert isinstance(ev.id, str) and len(ev.id) == 64


def test_normalize_drops_old_event():
    doc = RawDoc(
        source="test", theme="bio",
        url="https://example.com/old",
        title="Very old FDA news article from way back",
        body="This is old news that should be filtered out by the age cutoff.",
        published_at=datetime.now(timezone.utc) - timedelta(days=30),
    )
    assert normalize_event(doc) is None


def test_normalize_drops_short_title():
    doc = RawDoc(
        source="test", theme="bio",
        url="https://example.com/short",
        title="FDA",   # too short
        body="Very long body text that meets the minimum body length requirement for normalization.",
        published_at=_recent(),
    )
    ev = normalize_event(doc)
    assert ev is None


def test_normalize_batch_dedup():
    doc1 = RawDoc(
        source="test", theme="tech_ai",
        url="https://example.com/nvda",
        title="NVDA Q1 beats revenue expectations for fiscal year 2027",
        body="NVIDIA reported record quarterly revenue beating analyst expectations. Data center segment up 70 percent.",
        published_at=_recent(),
    )
    # Identical doc — should be deduped
    doc2 = RawDoc(
        source="test2", theme="tech_ai",
        url="https://example.com/nvda-copy",
        title="NVDA Q1 beats revenue expectations for fiscal year 2027",
        body="NVIDIA reported record quarterly revenue beating analyst expectations. Data center segment up 70 percent.",
        published_at=_recent(),
    )
    events = normalize_batch([doc1, doc2])
    assert len(events) == 1


def test_normalize_batch_different_docs():
    docs = [
        RawDoc(source="a", theme="space", url=f"https://example.com/{i}",
               title=f"Space news headline number {i} about RKLB or ASTS launch",
               body=f"Detailed body text about rocket launches and satellite deployments number {i}.",
               published_at=_recent())
        for i in range(5)
    ]
    events = normalize_batch(docs)
    assert len(events) == 5
