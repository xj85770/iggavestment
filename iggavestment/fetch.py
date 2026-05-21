"""
Feed pollers: RSS, JSON APIs, HTML scrapers.
3 retries with exponential backoff; on final fail → mark stale.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import feedparser
import httpx
import structlog
from bs4 import BeautifulSoup
from dateutil import parser as dtparser

from .config import FeedSpec, FEED_REGISTRY, LOG_DIR, UA
from .normalize import RawDoc

log = structlog.get_logger(__name__)

TIMEOUT = 30.0
RETRY_DELAYS = (1, 4, 16)


def _parse_dt(raw: Any) -> datetime | None:
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw
    try:
        return dtparser.parse(str(raw)).astimezone(timezone.utc)
    except Exception:
        return None


def _stale_flag(source: str) -> Path:
    return LOG_DIR / f"stale-{source}.flag"


async def fetch_feed(client: httpx.AsyncClient, feed: FeedSpec) -> list[RawDoc]:
    for attempt, delay in enumerate((*RETRY_DELAYS, None)):
        try:
            r = await client.get(feed.url, timeout=TIMEOUT, headers={"User-Agent": UA})
            r.raise_for_status()
            docs = _parse_response(feed, r)
            _stale_flag(feed.source).unlink(missing_ok=True)
            log.info("feed_ok", source=feed.source, n=len(docs))
            return docs
        except Exception as exc:
            if delay is None:
                _stale_flag(feed.source).touch()
                log.error("feed_failed", source=feed.source, err=str(exc))
                return []
            log.warning("feed_retry", source=feed.source, attempt=attempt, err=str(exc))
            await asyncio.sleep(delay)
    return []


def _parse_response(feed: FeedSpec, r: httpx.Response) -> list[RawDoc]:
    if feed.kind == "rss":
        return _parse_rss(feed, r.text)
    if feed.kind == "json":
        if feed.json_extractor is None:
            raise ValueError(f"json_extractor missing for {feed.source}")
        return feed.json_extractor(r.json())
    if feed.kind == "html":
        if feed.html_extractor is None:
            raise ValueError(f"html_extractor missing for {feed.source}")
        soup = BeautifulSoup(r.text, "lxml")
        return feed.html_extractor(soup)
    raise ValueError(f"unknown feed kind: {feed.kind}")


def _parse_rss(feed: FeedSpec, text: str) -> list[RawDoc]:
    parsed = feedparser.parse(text)
    docs = []
    for entry in parsed.entries:
        url   = getattr(entry, "link", "") or ""
        title = getattr(entry, "title", "") or ""
        body  = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
        pub   = _parse_dt(entry.get("published") or entry.get("updated"))
        if not url or not title:
            continue
        docs.append(RawDoc(source=feed.source, theme=feed.theme,
                           url=url, title=title, body=body, published_at=pub))
    return docs


async def fetch_all_feeds(
    feeds: list[FeedSpec] | None = None,
    mock_responses: dict[str, list[RawDoc]] | None = None,
) -> list[RawDoc]:
    if feeds is None:
        feeds = FEED_REGISTRY

    if mock_responses is not None:
        docs: list[RawDoc] = []
        for feed in feeds:
            docs.extend(mock_responses.get(feed.source, []))
        return docs

    async with httpx.AsyncClient(follow_redirects=True) as client:
        tasks = [fetch_feed(client, f) for f in feeds]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    docs = []
    for feed, result in zip(feeds, results):
        if isinstance(result, Exception):
            _stale_flag(feed.source).touch()
            log.error("feed_exception", source=feed.source, err=str(result))
        else:
            docs.extend(result)
    return docs


# ── Feed extractors ───────────────────────────────────────────────────────────

def _edgar_extractor(theme: str, source: str):
    def _extract(data: dict) -> list[RawDoc]:
        hits = data.get("hits", {}).get("hits", []) if isinstance(data, dict) else []
        docs = []
        for hit in hits:
            src = hit.get("_source", {})
            docs.append(RawDoc(
                source=source, theme=theme,
                url=f"https://efts.sec.gov/LATEST/search-index?q={src.get('file_num','')}",
                title=(src.get("display_names", [{"name": "Filing"}])[0].get("name", "SEC Filing")
                       + " — " + src.get("form_type", "8-K")),
                body=src.get("period_of_report", "") + " " + src.get("entity_name", ""),
                published_at=_parse_dt(src.get("period_of_report")),
            ))
        return docs
    return _extract


def _fed_register_extractor(theme: str, source: str):
    def _extract(data: dict) -> list[RawDoc]:
        results = data.get("results", []) if isinstance(data, dict) else []
        docs = []
        for r in results:
            docs.append(RawDoc(
                source=source, theme=theme,
                url=r.get("html_url", r.get("pdf_url", "")),
                title=r.get("title", "Federal Register Notice"),
                body=r.get("abstract", r.get("excerpt", "")),
                published_at=_parse_dt(r.get("publication_date")),
            ))
        return docs
    return _extract


def _clintrials_extractor(theme: str, source: str):
    def _extract(data: dict) -> list[RawDoc]:
        studies = data.get("studies", []) if isinstance(data, dict) else []
        docs = []
        for s in studies:
            proto  = s.get("protocolSection", {})
            ident  = proto.get("identificationModule", {})
            status = proto.get("statusModule", {})
            docs.append(RawDoc(
                source=source, theme=theme,
                url=f"https://clinicaltrials.gov/study/{ident.get('nctId','')}",
                title=ident.get("briefTitle", "ClinicalTrials Update"),
                body=ident.get("officialTitle", "") + " " + status.get("overallStatus", ""),
                published_at=_parse_dt(status.get("lastUpdatePostDateStruct", {}).get("date")),
            ))
        return docs
    return _extract


def _eia_extractor(theme: str, source: str):
    def _extract(data: dict) -> list[RawDoc]:
        series = data.get("response", {}).get("data", []) if isinstance(data, dict) else []
        docs = []
        for s in series[:5]:
            docs.append(RawDoc(
                source=source, theme=theme,
                url="https://www.eia.gov/outlooks/steo/",
                title=f"EIA Data: {s.get('seriesDescription', 'Energy Indicator')} = {s.get('value', '')}",
                body=f"Period: {s.get('period', '')} | Value: {s.get('value', '')} {s.get('units', '')}",
                published_at=_parse_dt(s.get("period")),
            ))
        return docs
    return _extract


def _sda_extractor(theme: str, source: str):
    def _extract(soup: BeautifulSoup) -> list[RawDoc]:
        docs = []
        items = soup.select("article, .news-item, li.views-row, div.item-list li")
        for item in items[:20]:
            a = item.find("a")
            title = a.get_text(strip=True) if a else item.get_text(strip=True)[:120]
            href  = a.get("href", "") if a else ""
            if href and not href.startswith("http"):
                href = "https://www.sda.mil" + href
            docs.append(RawDoc(
                source=source, theme=theme,
                url=href or "https://www.sda.mil/news/",
                title=title or "SDA News",
                body=item.get_text(strip=True)[:500],
                published_at=datetime.now(timezone.utc),
            ))
        return docs[:10]
    return _extract


def _nrc_extractor(theme: str, source: str):
    def _extract(soup: BeautifulSoup) -> list[RawDoc]:
        docs = []
        rows = soup.select("table tr") or []
        for row in rows[1:20]:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            link  = cells[0].find("a")
            title = link.get_text(strip=True) if link else cells[0].get_text(strip=True)
            href  = link.get("href", "") if link else ""
            if href and not href.startswith("http"):
                href = "https://www.nrc.gov" + href
            docs.append(RawDoc(
                source=source, theme=theme,
                url=href or "https://www.nrc.gov/reactors/new-reactors/licensing-activities.html",
                title=title or "NRC Licensing Update",
                body=" ".join(c.get_text(strip=True) for c in cells),
                published_at=datetime.now(timezone.utc),
            ))
        return docs
    return _extract


# ── Build registry ────────────────────────────────────────────────────────────

def _build_registry() -> list[FeedSpec]:
    feeds: list[FeedSpec] = []

    # BIO
    feeds.append(FeedSpec(source="edgar_bio", theme="bio", kind="json",
        url="https://efts.sec.gov/LATEST/search-index?q=%22clinical+trial%22+%22topline%22&dateRange=custom&startdt=2024-01-01&forms=8-K",
        json_extractor=_edgar_extractor("bio", "edgar_bio"),
        description="EDGAR 8-K biotech"))
    feeds.append(FeedSpec(source="clintrials_bio", theme="bio", kind="json",
        url="https://clinicaltrials.gov/api/v2/studies?filter.advanced=AREA%5BLastUpdatePostDate%5DRANGE%5B2024-01-01%2CMAX%5D&query.cond=cancer+OR+rare+disease&pageSize=20&sort=LastUpdatePostDate%3Adesc",
        json_extractor=_clintrials_extractor("bio", "clintrials_bio"),
        description="ClinicalTrials.gov"))
    feeds.append(FeedSpec(source="fda_press", theme="bio", kind="rss",
        url="https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/fda-news-releases/rss.xml",
        description="FDA news releases"))
    feeds.append(FeedSpec(source="fed_register_bio", theme="bio", kind="json",
        url="https://www.federalregister.gov/api/v1/documents.json?conditions%5Bagencies%5D%5B%5D=food-and-drug-administration&conditions%5Bterm%5D=PDUFA&order=newest&per_page=20",
        json_extractor=_fed_register_extractor("bio", "fed_register_bio"),
        description="Federal Register FDA PDUFA"))

    # TECH_AI
    feeds.append(FeedSpec(source="edgar_semis", theme="tech_ai", kind="json",
        url="https://efts.sec.gov/LATEST/search-index?q=%22data+center%22+%22capital+expenditure%22&forms=8-K&dateRange=custom&startdt=2024-01-01",
        json_extractor=_edgar_extractor("tech_ai", "edgar_semis"),
        description="EDGAR 8-K hyperscaler capex"))
    feeds.append(FeedSpec(source="fed_register_chips", theme="tech_ai", kind="json",
        url="https://www.federalregister.gov/api/v1/documents.json?conditions%5Bagencies%5D%5B%5D=industry-and-security-bureau&order=newest&per_page=20",
        json_extractor=_fed_register_extractor("tech_ai", "fed_register_chips"),
        description="Federal Register BIS export controls"))
    feeds.append(FeedSpec(source="commerce_chips", theme="tech_ai", kind="rss",
        url="https://www.commerce.gov/feeds/news",
        description="Commerce Dept CHIPS news"))

    # ROBOTICS
    feeds.append(FeedSpec(source="fed_register_autonomous", theme="robotics", kind="json",
        url="https://www.federalregister.gov/api/v1/documents.json?conditions%5Bterm%5D=autonomous+systems&order=newest&per_page=20",
        json_extractor=_fed_register_extractor("robotics", "fed_register_autonomous"),
        description="Federal Register autonomous systems"))
    feeds.append(FeedSpec(source="edgar_robotics", theme="robotics", kind="json",
        url="https://efts.sec.gov/LATEST/search-index?q=%22surgical+robot%22+OR+%22autonomous+systems%22&forms=8-K&dateRange=custom&startdt=2024-01-01",
        json_extractor=_edgar_extractor("robotics", "edgar_robotics"),
        description="EDGAR 8-K robotics"))

    # ENERGY
    feeds.append(FeedSpec(source="nrc_licensing", theme="energy", kind="html",
        url="https://www.nrc.gov/reactors/new-reactors/licensing-activities.html",
        html_extractor=_nrc_extractor("energy", "nrc_licensing"),
        description="NRC SMR licensing"))
    feeds.append(FeedSpec(source="doe_nuclear", theme="energy", kind="rss",
        url="https://www.energy.gov/ne/articles/rss.xml",
        description="DOE Nuclear Energy press releases"))
    feeds.append(FeedSpec(source="eia_gas", theme="energy", kind="json",
        url="https://api.eia.gov/v2/natural-gas/pri/sum/data/?api_key=DEMO_KEY&frequency=monthly&data[0]=value&sort[0][column]=period&sort[0][direction]=desc&length=10",
        json_extractor=_eia_extractor("energy", "eia_gas"),
        description="EIA natural gas price"))
    feeds.append(FeedSpec(source="edgar_energy", theme="energy", kind="json",
        url="https://efts.sec.gov/LATEST/search-index?q=%22uranium%22+OR+%22LNG%22+%22nuclear%22&forms=8-K&dateRange=custom&startdt=2024-01-01",
        json_extractor=_edgar_extractor("energy", "edgar_energy"),
        description="EDGAR 8-K energy sector"))

    # SPACE
    feeds.append(FeedSpec(source="sda_news", theme="space", kind="html",
        url="https://www.sda.mil/news/",
        html_extractor=_sda_extractor("space", "sda_news"),
        description="Space Development Agency news"))
    feeds.append(FeedSpec(source="edgar_space", theme="space", kind="json",
        url="https://efts.sec.gov/LATEST/search-index?q=%22satellite%22+%22launch%22+%22SpaceMobile%22&forms=8-K&dateRange=custom&startdt=2024-01-01",
        json_extractor=_edgar_extractor("space", "edgar_space"),
        description="EDGAR 8-K space"))
    feeds.append(FeedSpec(source="spacenews_rss", theme="space", kind="rss",
        url="https://spacenews.com/feed/",
        description="SpaceNews RSS"))

    # RARE EARTH
    feeds.append(FeedSpec(source="fed_register_dpa", theme="rare_earth", kind="json",
        url="https://www.federalregister.gov/api/v1/documents.json?conditions%5Bterm%5D=critical+minerals&order=newest&per_page=20",
        json_extractor=_fed_register_extractor("rare_earth", "fed_register_dpa"),
        description="Federal Register critical minerals"))
    feeds.append(FeedSpec(source="edgar_remx", theme="rare_earth", kind="json",
        url="https://efts.sec.gov/LATEST/search-index?q=%22rare+earth%22+%22neodymium%22&forms=8-K&dateRange=custom&startdt=2024-01-01",
        json_extractor=_edgar_extractor("rare_earth", "edgar_remx"),
        description="EDGAR 8-K rare earth"))
    feeds.append(FeedSpec(source="doe_lpo", theme="rare_earth", kind="rss",
        url="https://www.energy.gov/lpo/articles/rss.xml",
        description="DOE Loan Programs Office"))

    # FOOD/AG
    feeds.append(FeedSpec(source="usda_crop_progress", theme="food_ag", kind="rss",
        url="https://www.nass.usda.gov/rss/feed.xml",
        description="USDA NASS crop progress"))
    feeds.append(FeedSpec(source="epa_rvo", theme="food_ag", kind="rss",
        url="https://www.epa.gov/rss/epa-newsroom.xml",
        description="EPA newsroom (RVO/RFS)"))
    feeds.append(FeedSpec(source="edgar_ag", theme="food_ag", kind="json",
        url="https://efts.sec.gov/LATEST/search-index?q=%22potash%22+OR+%22fertilizer%22&forms=8-K&dateRange=custom&startdt=2024-01-01",
        json_extractor=_edgar_extractor("food_ag", "edgar_ag"),
        description="EDGAR 8-K agriculture"))

    return feeds


FEED_REGISTRY.extend(_build_registry())
