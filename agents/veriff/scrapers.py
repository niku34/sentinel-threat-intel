"""Seven fact-check scrapers for Veriff.

Each `search_<source>(claim)` returns list[dict]:
    {source, url, headline, verdict, date}

All scrapers:
- 8s timeout, fail closed ([] on error)
- 1-hour in-process TTL cache per (source, query)

Public entrypoint: `run_all_scrapers(claim)` — fans out to all seven in parallel.

Selector notes are inline: many fact-check sites are WordPress installs; a
handful (PIB, Newschecker Tamil) render client-side, so we use their listing
pages / WP REST endpoints instead of the search pages that don't render
server-side.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup

from agents.core.plugin_check import is_plugin_enabled

logger = logging.getLogger("veriff.scrapers")

TIMEOUT = 8.0
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36 Sentinel/1.0 Veriff-Tamil"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9,ta;q=0.8,hi;q=0.7",
}
MAX_RESULTS_PER_SOURCE = 6
CACHE_TTL_SECONDS = 3600

_CACHE: Dict[str, tuple] = {}
_CACHE_LOCK = threading.Lock()


def _cache_get(key: str) -> Optional[List[dict]]:
    now = time.time()
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if not entry:
            return None
        expires_at, payload = entry
        if expires_at < now:
            _CACHE.pop(key, None)
            return None
        return payload


def _cache_set(key: str, payload: List[dict]) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = (time.time() + CACHE_TTL_SECONDS, payload)


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def _fetch(url: str, accept_json: bool = False):
    hdrs = dict(HEADERS)
    if accept_json:
        hdrs["Accept"] = "application/json"
    try:
        resp = requests.get(url, headers=hdrs, timeout=TIMEOUT, allow_redirects=True)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("fetch failed: %s (%s)", url, exc)
        return None
    return resp


def _fetch_soup(url: str) -> Optional[BeautifulSoup]:
    resp = _fetch(url)
    if not resp:
        return None
    return BeautifulSoup(resp.text, "html.parser")


def _fetch_json(url: str):
    resp = _fetch(url, accept_json=True)
    if not resp:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


_QUERY_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "for", "is",
    "are", "was", "were", "with", "by", "from", "news", "claim", "video",
    "photo", "image", "footage", "today", "yesterday",
}


def _query_terms(claim: str, limit: int = 3) -> str:
    """Pick the N most informative tokens for a search query.

    Fact-check WordPress search endpoints AND their terms, so 5-word English
    queries rarely match. We take the longest non-stopword tokens (proper
    nouns / topical keywords tend to be longer than filler words).
    """
    tokens = re.findall(r"[A-Za-z0-9]+|[஀-௿]+", claim)
    if not tokens:
        return claim.strip()
    filtered = [t for t in tokens if t.lower() not in _QUERY_STOPWORDS and len(t) > 2]
    if not filtered:
        filtered = tokens
    # Preserve first-seen order but prefer longer / rarer tokens.
    ordered = sorted(set(filtered), key=lambda t: (-len(t), filtered.index(t)))
    picked = ordered[:limit]
    # Restore original order for reproducibility.
    picked.sort(key=lambda t: filtered.index(t))
    return " ".join(picked)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


# ── Verdict labelling ──────────────────────────────────────────
# A search hit only tells us an article exists. It does NOT tell us what that
# article concluded, so the default label is "REVIEWED" — found and reviewed,
# verdict not definitively extracted. "FAKE" is used only when the fact-checker's
# own headline says, in so many words, that the claim is false.
_FALSE_MARKERS_EN = (
    "false", "fake news", "fake claim", "hoax", "debunk", "misleading",
    "no truth", "untrue", "misinformation", "disinformation", "morphed",
    "doctored", "digitally altered", "edited video", "old video", "old image",
    "baseless", "fabricated", "viral lie",
)

_FALSE_MARKERS_TA = (
    "பொய்",          # lie / false
    "போலி",          # fake
    "வதந்தி",        # rumour
    "தவறான",         # incorrect / wrong
    "புரளி",         # hoax
    "திரிக்கப்பட்ட",  # distorted
    "எடிட்",         # edited
    "தவறு",          # mistake / wrong
)


def _verdict_for(headline: str, url: str = "") -> str:
    """Label a search hit from what the source itself says.

    Returns "FAKE" only when the headline carries explicit fact-check negative
    language (English or Tamil). Otherwise "REVIEWED" — never assume the worst.
    """
    text = f"{headline or ''} {url or ''}".lower()
    if any(m in text for m in _FALSE_MARKERS_EN):
        return "FAKE"
    if any(m in (headline or "") for m in _FALSE_MARKERS_TA):
        return "FAKE"
    return "REVIEWED"


def _cached_search(source: str, claim: str, impl: Callable[[str], List[dict]]) -> List[dict]:
    key = f"{source}::{claim.lower().strip()}"
    hit = _cache_get(key)
    if hit is not None:
        logger.info("%s cache hit (%d results)", source, len(hit))
        return hit
    try:
        results = impl(claim) or []
    except Exception as exc:
        logger.warning("%s scraper error: %s", source, exc)
        results = []
    results = results[:MAX_RESULTS_PER_SOURCE]
    _cache_set(key, results)
    logger.info("%s -> %d results", source, len(results))
    return results


# ── PIB Fact Check ─────────────────────────────────────────────
# factcheck.pib.gov.in and its search endpoints are JS-rendered (SPA);
# the working fallback is AllRelease.aspx?reg=3&lang=1 which lists all
# recent English press releases with real PRIDs. We filter that list by
# claim keywords.
_PIB_ALL_URL = "https://www.pib.gov.in/AllRelease.aspx?reg=3&lang=1"


def _pib_impl(claim: str) -> List[dict]:
    soup = _fetch_soup(_PIB_ALL_URL)
    if not soup:
        return []

    # Collect all PRID-based press-release anchors.
    all_releases: List[dict] = []
    seen: set = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "PRID=" not in href:
            continue
        title = _clean(a.get_text())
        if len(title) < 15:
            continue
        url = urljoin("https://www.pib.gov.in/", href)
        if url in seen:
            continue
        seen.add(url)
        all_releases.append({"url": url, "headline": title})

    keywords = [
        k.lower()
        for k in re.findall(r"[A-Za-z]{4,}", claim)
        if k.lower() not in _QUERY_STOPWORDS
    ]

    matches: List[dict] = []
    if keywords:
        for r in all_releases:
            tl = r["headline"].lower()
            if any(kw in tl for kw in keywords) or "fact check" in tl:
                matches.append(
                    {"source": "PIB", "url": r["url"], "headline": r["headline"],
                     "verdict": "REVIEWED", "date": ""}
                )
                if len(matches) >= MAX_RESULTS_PER_SOURCE:
                    break

    # If keyword search yielded no matches, surface the most recent releases
    # so the semantic matcher can score them (it will reject on low overlap).
    if not matches:
        for r in all_releases[:MAX_RESULTS_PER_SOURCE]:
            matches.append(
                {"source": "PIB", "url": r["url"], "headline": r["headline"],
                 "verdict": "REVIEWED", "date": ""}
            )
    return matches


def search_pib(claim: str) -> List[dict]:
    if not is_plugin_enabled("PIB Fact Check"):
        logger.info("Plugin PIB Fact Check disabled — skipping")
        return []
    return _cached_search("PIB", claim, _pib_impl)


# ── AltNews ────────────────────────────────────────────────────
# Selector confirmed: `article h3 a` on /?s=<q>
def _altnews_impl(claim: str) -> List[dict]:
    q = quote_plus(_query_terms(claim))
    soup = _fetch_soup(f"https://www.altnews.in/?s={q}")
    if not soup:
        return []
    results: List[dict] = []
    for a in soup.select("article h3 a"):
        href = a.get("href")
        title = _clean(a.get_text())
        if not href or not title:
            continue
        results.append(
            {
                "source": "AltNews",
                "url": href,
                "headline": title,
                "verdict": _verdict_for(title, href),
                "date": "",
            }
        )
    return results


def search_altnews(claim: str) -> List[dict]:
    if not is_plugin_enabled("AltNews Scraper"):
        logger.info("Plugin AltNews Scraper disabled — skipping")
        return []
    return _cached_search("AltNews", claim, _altnews_impl)


# ── Vishvas News ───────────────────────────────────────────────
# Selector confirmed: `h3 a` on /english/?s=<q>
def _vishvas_impl(claim: str) -> List[dict]:
    q = quote_plus(_query_terms(claim))
    soup = _fetch_soup(f"https://www.vishvasnews.com/english/?s={q}")
    if not soup:
        return []
    results: List[dict] = []
    for a in soup.select("h3 a"):
        href = a.get("href")
        title = _clean(a.get_text())
        if not href or not title:
            continue
        if "vishvasnews.com" not in href:
            continue
        results.append(
            {
                "source": "Vishvas",
                "url": href,
                "headline": title,
                "verdict": _verdict_for(title, href),
                "date": "",
            }
        )
    return results


def search_vishvas(claim: str) -> List[dict]:
    if not is_plugin_enabled("Vishvas News"):
        logger.info("Plugin Vishvas News disabled — skipping")
        return []
    return _cached_search("Vishvas", claim, _vishvas_impl)


# ── Fact Crescendo Tamil ───────────────────────────────────────
# Selector confirmed: `article h2 a` or `.entry-title a` on tamil.factcrescendo.com/?s=<q>
def _fact_crescendo_impl(claim: str) -> List[dict]:
    q = quote_plus(_query_terms(claim))
    soup = _fetch_soup(f"https://tamil.factcrescendo.com/?s={q}")
    if not soup:
        return []
    results: List[dict] = []
    for a in soup.select("article h2 a, .entry-title a"):
        href = a.get("href")
        title = _clean(a.get_text())
        if not href or not title:
            continue
        if any(r.get("url") == href for r in results):
            continue
        results.append(
            {
                "source": "FactCrescendo",
                "url": href,
                "headline": title,
                "verdict": _verdict_for(title, href),
                "date": "",
            }
        )
    return results


def search_fact_crescendo(claim: str) -> List[dict]:
    if not is_plugin_enabled("Fact Crescendo Tamil"):
        logger.info("Plugin Fact Crescendo Tamil disabled — skipping")
        return []
    return _cached_search("FactCrescendo", claim, _fact_crescendo_impl)


# ── India Today Fact Check ─────────────────────────────────────
def _india_today_impl(claim: str) -> List[dict]:
    q = quote_plus(_query_terms(claim))
    soup = _fetch_soup(f"https://www.indiatoday.in/search/{q}")
    results: List[dict] = []

    if soup:
        for card in soup.select("div.detail, article, li.stories, div.story__grid"):
            link = card.find("a", href=True)
            title_el = card.find(["h1", "h2", "h3", "h4"])
            if not link or not title_el:
                continue
            href = link["href"]
            if "fact-check" not in href.lower():
                continue
            results.append(
                {
                    "source": "IndiaToday",
                    "url": urljoin("https://www.indiatoday.in/", href),
                    "headline": _clean(title_el.get_text()),
                    "verdict": "REVIEWED",
                    "date": "",
                }
            )
            if len(results) >= MAX_RESULTS_PER_SOURCE:
                break

    if not results:
        soup2 = _fetch_soup("https://www.indiatoday.in/fact-check")
        if soup2:
            for h in soup2.select("h2 a, h3 a, h4 a"):
                headline = _clean(h.get_text())
                href = h.get("href") or ""
                if not headline or "fact-check" not in href.lower():
                    continue
                results.append(
                    {
                        "source": "IndiaToday",
                        "url": urljoin("https://www.indiatoday.in/", href),
                        "headline": headline,
                        "verdict": "REVIEWED",
                        "date": "",
                    }
                )
                if len(results) >= MAX_RESULTS_PER_SOURCE:
                    break
    return results


def search_india_today(claim: str) -> List[dict]:
    if not is_plugin_enabled("IndiaToday Fact Check"):
        logger.info("Plugin IndiaToday Fact Check disabled — skipping")
        return []
    return _cached_search("IndiaToday", claim, _india_today_impl)


# ── Quint WebQoof ──────────────────────────────────────────────
# Selector confirmed: `a[href*='webqoof']` on /search?q=<q>
def _quint_webqoof_impl(claim: str) -> List[dict]:
    q = quote_plus(_query_terms(claim))
    soup = _fetch_soup(f"https://www.thequint.com/search?q={q}")
    results: List[dict] = []
    seen: set = set()
    if soup:
        for a in soup.select("a[href*='webqoof'], a[href*='fact-check']"):
            href = a.get("href") or ""
            title = _clean(a.get_text())
            if not href or not title or len(title) < 20:
                continue
            full = urljoin("https://www.thequint.com/", href)
            if full in seen:
                continue
            seen.add(full)
            results.append(
                {
                    "source": "QuintWebQoof",
                    "url": full,
                    "headline": title,
                    "verdict": _verdict_for(title, full),
                    "date": "",
                }
            )
            if len(results) >= MAX_RESULTS_PER_SOURCE:
                break

    if not results:
        soup2 = _fetch_soup("https://www.thequint.com/news/webqoof")
        if soup2:
            for a in soup2.select("a[href*='webqoof']"):
                href = a.get("href") or ""
                title = _clean(a.get_text())
                if not href or not title or len(title) < 20:
                    continue
                full = urljoin("https://www.thequint.com/", href)
                if full in seen:
                    continue
                seen.add(full)
                results.append(
                    {
                        "source": "QuintWebQoof",
                        "url": full,
                        "headline": title,
                        "verdict": _verdict_for(title, full),
                        "date": "",
                    }
                )
                if len(results) >= MAX_RESULTS_PER_SOURCE:
                    break
    return results


def search_quint_webqoof(claim: str) -> List[dict]:
    if not is_plugin_enabled("Quint WebQoof"):
        logger.info("Plugin Quint WebQoof disabled — skipping")
        return []
    return _cached_search("QuintWebQoof", claim, _quint_webqoof_impl)


# ── Newschecker Tamil ──────────────────────────────────────────
# newschecker.in/tamil is a JS SPA. Use their WordPress REST API:
#   /wp-json/wp/v2/posts?search=<q>&per_page=5
# Filter for Tamil-language fact checks by URL path or category when possible.
def _newschecker_impl(claim: str) -> List[dict]:
    q = quote_plus(_query_terms(claim))
    # Primary: WP JSON API (English/Hindi/Tamil unified index)
    api_url = f"https://newschecker.in/wp-json/wp/v2/posts?search={q}&per_page={MAX_RESULTS_PER_SOURCE}"
    data = _fetch_json(api_url)
    results: List[dict] = []
    if isinstance(data, list):
        for post in data:
            link = post.get("link") or ""
            title = _clean((post.get("title") or {}).get("rendered", ""))
            date = post.get("date") or ""
            if not link or not title:
                continue
            # Any fact-check URL is relevant; prefer tamil paths when available.
            results.append(
                {
                    "source": "NewscheckerTamil",
                    "url": link,
                    "headline": title,
                    "verdict": _verdict_for(title, link),
                    "date": date,
                }
            )
    # Fallback: search the English root's SSR search page.
    if not results:
        soup = _fetch_soup(f"https://newschecker.in/?s={q}")
        if soup:
            for a in soup.find_all("a", href=True):
                href = a.get("href")
                title = _clean(a.get_text())
                if not href or not title or len(title) < 25:
                    continue
                if "/fact-check/" not in href and "/tamil/" not in href:
                    continue
                results.append(
                    {
                        "source": "NewscheckerTamil",
                        "url": urljoin("https://newschecker.in/", href),
                        "headline": title,
                        "verdict": _verdict_for(title, href),
                        "date": "",
                    }
                )
                if len(results) >= MAX_RESULTS_PER_SOURCE:
                    break
    return results


def search_newschecker(claim: str) -> List[dict]:
    if not is_plugin_enabled("Newschecker Tamil"):
        logger.info("Plugin Newschecker Tamil disabled — skipping")
        return []
    return _cached_search("NewscheckerTamil", claim, _newschecker_impl)


ALL_SCRAPERS: Dict[str, Callable[[str], List[dict]]] = {
    "PIB": search_pib,
    "AltNews": search_altnews,
    "Vishvas": search_vishvas,
    "FactCrescendo": search_fact_crescendo,
    "IndiaToday": search_india_today,
    "QuintWebQoof": search_quint_webqoof,
    "NewscheckerTamil": search_newschecker,
}


# ── Domain credibility scoring ─────────────────────────────────

CREDIBILITY_SCORES: Dict[str, float] = {
    # HIGH tier (0.85-1.0) — authoritative fact-checkers and national press
    "pib.gov.in": 0.98, "altnews.in": 0.95, "thehindu.com": 0.95,
    "ndtv.com": 0.90, "timesofindia.com": 0.90, "indianexpress.com": 0.90,
    "vishvasnews.com": 0.92, "newschecker.in": 0.90, "factcrescendo.com": 0.90,
    "tamil.factcrescendo.com": 0.90, "indiatoday.in": 0.88, "thequint.com": 0.88,
    "theprint.in": 0.88, "scroll.in": 0.85, "thewire.in": 0.85,
    # MEDIUM tier (0.60-0.84) — Tamil regional press
    "vikatan.com": 0.82, "dinamalar.com": 0.80, "dinamani.com": 0.80,
    "example-media.in": 0.60,
    # SUSPICIOUS (<0.30) — known impersonators / fake-page domains
    "example-broadcaster.in": 0.10, "example-movies.in": 0.10, "example-news.in": 0.10,
}

_DOMAIN_RE = re.compile(r"https?://(?:www\.)?([^/?#]+)")


def score_domain_credibility(url: str) -> dict:
    m = _DOMAIN_RE.match(url or "")
    domain = m.group(1).lower() if m else ""
    score = CREDIBILITY_SCORES.get(domain)
    if score is None:
        return {"domain": domain, "score": 0.5, "tier": "UNKNOWN"}
    if score >= 0.85:
        tier = "HIGH"
    elif score >= 0.60:
        tier = "MEDIUM"
    elif score >= 0.30:
        tier = "LOW"
    else:
        tier = "SUSPICIOUS"
    return {"domain": domain, "score": score, "tier": tier}


def _enrich_credibility(results: List[dict]) -> List[dict]:
    for r in results:
        r["credibility"] = score_domain_credibility(r.get("url", ""))
    return results


# ── Google News (English) ──────────────────────────────────────
def _google_news_impl(claim: str) -> List[dict]:
    import xml.etree.ElementTree as ET  # stdlib — no extra dep
    q = quote_plus(_query_terms(claim, limit=4))
    url = f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
    resp = _fetch(url)
    if not resp:
        return []
    try:
        root = ET.fromstring(resp.text)
        channel = root.find("channel")
        if channel is None:
            return []
        results: List[dict] = []
        for item in channel.findall("item")[:MAX_RESULTS_PER_SOURCE]:
            title = _clean(item.findtext("title") or "")
            link = _clean(item.findtext("link") or "")
            pub = _clean(item.findtext("pubDate") or "")
            if title and link:
                results.append({"source": "GoogleNews", "url": link, "headline": title, "verdict": "NEWS", "date": pub})
        return results
    except Exception as exc:
        logger.warning("google_news parse error: %s", exc)
        return []


def search_google_news(claim: str) -> List[dict]:
    return _cached_search("GoogleNews", claim, _google_news_impl)


# ── Google News Tamil ──────────────────────────────────────────
def _google_news_tamil_impl(claim: str) -> List[dict]:
    import xml.etree.ElementTree as ET
    # Send full Tamil text as-is for best recall; keyword reduction manges Tamil
    q = quote_plus(claim[:200])
    url = f"https://news.google.com/rss/search?q={q}&hl=ta&gl=IN&ceid=IN:ta"
    resp = _fetch(url)
    if not resp:
        return []
    try:
        root = ET.fromstring(resp.text)
        channel = root.find("channel")
        if channel is None:
            return []
        results: List[dict] = []
        for item in channel.findall("item")[:MAX_RESULTS_PER_SOURCE]:
            title = _clean(item.findtext("title") or "")
            link = _clean(item.findtext("link") or "")
            pub = _clean(item.findtext("pubDate") or "")
            if title and link:
                results.append({"source": "GoogleNewsTamil", "url": link, "headline": title, "verdict": "NEWS", "date": pub})
        return results
    except Exception as exc:
        logger.warning("google_news_tamil parse error: %s", exc)
        return []


def search_google_news_tamil(claim: str) -> List[dict]:
    return _cached_search("GoogleNewsTamil", claim, _google_news_tamil_impl)


# ── Vikatan ────────────────────────────────────────────────────
def _vikatan_impl(claim: str) -> List[dict]:
    q = quote_plus(claim[:150])
    soup = _fetch_soup(f"https://www.vikatan.com/search?query={q}")
    if not soup:
        return []
    results: List[dict] = []
    seen: set = set()
    for a in soup.select("a[href*='/news/'], a[href*='/article/']"):
        href = a.get("href") or ""
        title = _clean(a.get_text())
        if not href or not title or len(title) < 10:
            continue
        full = urljoin("https://www.vikatan.com/", href)
        if full in seen:
            continue
        seen.add(full)
        results.append({"source": "Vikatan", "url": full, "headline": title, "verdict": "NEWS", "date": ""})
        if len(results) >= MAX_RESULTS_PER_SOURCE:
            break
    return results


def search_vikatan(claim: str) -> List[dict]:
    return _cached_search("Vikatan", claim, _vikatan_impl)


# ── Dinamalar ──────────────────────────────────────────────────
def _dinamalar_impl(claim: str) -> List[dict]:
    q = quote_plus(claim[:150])
    soup = _fetch_soup(f"https://www.dinamalar.com/search_news.asp?q={q}")
    if not soup:
        return []
    results: List[dict] = []
    seen: set = set()
    for a in soup.select("a[href]"):
        href = a.get("href") or ""
        title = _clean(a.get_text())
        if not href or not title or len(title) < 10:
            continue
        if "dinamalar.com" not in href and not href.startswith("/"):
            continue
        full = urljoin("https://www.dinamalar.com/", href)
        if full in seen:
            continue
        seen.add(full)
        results.append({"source": "Dinamalar", "url": full, "headline": title, "verdict": "NEWS", "date": ""})
        if len(results) >= MAX_RESULTS_PER_SOURCE:
            break
    return results


def search_dinamalar(claim: str) -> List[dict]:
    return _cached_search("Dinamalar", claim, _dinamalar_impl)


# ── YouTube Search ─────────────────────────────────────────────
# YouTube results are JS-rendered; use DuckDuckGo site:youtube.com as proxy.
def _youtube_impl(claim: str) -> List[dict]:
    q = quote_plus(f"{_query_terms(claim, limit=4)} site:youtube.com")
    soup = _fetch_soup(f"https://html.duckduckgo.com/html/?q={q}")
    if not soup:
        return []
    results: List[dict] = []
    seen: set = set()
    for a in soup.select("a.result__a"):
        href = a.get("href") or ""
        title = _clean(a.get_text())
        if not href or not title or "youtube.com/watch" not in href:
            continue
        if href in seen:
            continue
        seen.add(href)
        results.append({"source": "YouTube", "url": href, "headline": title, "verdict": "VIDEO", "date": ""})
        if len(results) >= MAX_RESULTS_PER_SOURCE:
            break
    return results


def search_youtube(claim: str) -> List[dict]:
    return _cached_search("YouTube", claim, _youtube_impl)


# ── Web Claim Search ───────────────────────────────────────────
def _web_claim_impl(claim: str) -> List[dict]:
    q = quote_plus(f"{_query_terms(claim, limit=4)} fact check")
    soup = _fetch_soup(f"https://html.duckduckgo.com/html/?q={q}")
    if not soup:
        return []
    results: List[dict] = []
    seen: set = set()
    for a in soup.select("a.result__a"):
        href = a.get("href") or ""
        title = _clean(a.get_text())
        if not href or not title:
            continue
        if href in seen:
            continue
        seen.add(href)
        results.append({"source": "Web", "url": href, "headline": title, "verdict": "REVIEWED", "date": ""})
        if len(results) >= MAX_RESULTS_PER_SOURCE:
            break
    return results


def search_web_claim(claim: str) -> List[dict]:
    return _cached_search("Web", claim, _web_claim_impl)


WEB_SEARCH_SCRAPERS: Dict[str, Callable[[str], List[dict]]] = {
    "GoogleNews": search_google_news,
    "GoogleNewsTamil": search_google_news_tamil,
    "YouTube": search_youtube,
    "Web": search_web_claim,
    "Vikatan": search_vikatan,
    "Dinamalar": search_dinamalar,
}


_FAN_OUT_BUDGET_SECONDS = TIMEOUT * 2 + 4


def _fan_out(
    scraper_map: Dict[str, Callable[[str], List[dict]]],
    claim: str,
    max_workers: int,
    label: str,
) -> Tuple[Dict[str, List[dict]], List[str]]:
    """Run every scraper in `scraper_map` in parallel with a shared time budget.

    Partial completion is a legitimate outcome, not a crash: if some sources
    don't finish within budget, this returns results for the ones that did
    rather than letting `as_completed`'s TimeoutError escape (it previously
    propagated all the way to the API layer as an unhandled exception, which
    FastAPI surfaced to the client as a raw Python traceback).

    A slow future is abandoned, not killed (Python threads can't be force-
    killed) — same tradeoff as threat_intel_agent._run_phase_with_budget.
    `pool.shutdown(wait=False)` means this function returns promptly instead
    of blocking on the straggler.

    Returns (results, timed_out_source_names).
    """
    out: Dict[str, List[dict]] = {name: [] for name in scraper_map}
    pool = ThreadPoolExecutor(max_workers=max_workers)
    futures = {pool.submit(fn, claim): name for name, fn in scraper_map.items()}
    timed_out: List[str] = []
    try:
        for fut in as_completed(futures, timeout=_FAN_OUT_BUDGET_SECONDS):
            name = futures[fut]
            try:
                out[name] = fut.result() or []
            except Exception as exc:  # noqa: BLE001
                logger.warning("%s %s failed: %s", label, name, exc)
                out[name] = []
    except FutureTimeoutError:
        timed_out = sorted(name for fut, name in futures.items() if not fut.done())
        logger.warning(
            "%s: %d/%d source(s) exceeded the %.0fs budget and were abandoned — "
            "proceeding with %d completed result(s). Timed out: %s",
            label, len(timed_out), len(futures), _FAN_OUT_BUDGET_SECONDS,
            len(futures) - len(timed_out), timed_out,
        )
    finally:
        pool.shutdown(wait=False)
    return out, timed_out


def run_web_searches(claim: str, max_workers: int = 6) -> Dict[str, List[dict]]:
    """Fan out to web sources (incl. Tamil). Informational only — no verdict impact."""
    out, _timed_out = _fan_out(WEB_SEARCH_SCRAPERS, claim, max_workers, "web search")
    return {name: _enrich_credibility(hits) for name, hits in out.items()}


def run_all_scrapers(claim: str, max_workers: int = 7) -> Dict[str, List[dict]]:
    results, _timed_out = run_all_scrapers_detailed(claim, max_workers)
    return results


def run_all_scrapers_detailed(
    claim: str, max_workers: int = 7
) -> Tuple[Dict[str, List[dict]], List[str]]:
    """Like `run_all_scrapers`, but also reports which sources timed out.

    Raises RuntimeError — a clean, honest message, never a raw traceback —
    only when truly nothing useful came back (every source timed out or
    failed). A partial result (even 1 of 7 sources missing) is legitimate and
    returned normally; the caller can report "6/7 sources checked, 1 timed
    out" instead of failing the whole fact-check.
    """
    out, timed_out = _fan_out(ALL_SCRAPERS, claim, max_workers, "fact-check scraper")
    if timed_out and len(timed_out) == len(ALL_SCRAPERS):
        raise RuntimeError(
            "All fact-check sources timed out or failed to respond — unable to "
            "complete verification. Please retry."
        )
    return out, timed_out


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    for sample in [
        "PIB debunked election fraud claim",
        "Chennai protest footage today",
    ]:
        print(f"\n############# {sample} #############")
        out = run_all_scrapers(sample)
        counts = {k: len(v) for k, v in out.items()}
        print(json.dumps({"counts": counts}, indent=2))
        for src, hits in out.items():
            if hits:
                print(f"\n{src}:")
                for h in hits[:2]:
                    print(" -", (h.get("headline") or "")[:110])
                    print("   ", h.get("url"))
