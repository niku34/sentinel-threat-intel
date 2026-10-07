"""Fake-page / impersonation detector for Kaaval.

Live reconnaissance across the open web for pages, channels, and domains that
impersonate Example Broadcaster brands. Four independent probes:

  • search_google        — scrape Google for social profiles claiming the brand
  • scrape_facebook_public — Facebook public directory lookup
  • scrape_telegram_public — Telegram public channel + search scrape (t.me/s/…)
  • check_lookalike_domains — DNS resolution of typo-squat / lookalike domains

`run_full_scan` fans the four probes out over a ThreadPoolExecutor, then raises a
FAKE_PAGE threat in the DB for every high-suspicion hit (score >= 0.7).

Runs inside agents/.venv (Python 3.11): requests + beautifulsoup4 + sqlalchemy.
Every probe is wrapped so a network failure, block, or captcha degrades to an
empty list rather than taking the scan down.
"""
from __future__ import annotations

import logging
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote_plus, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("fake_page_detector")

# A desktop browser UA — bare requests UAs get blocked/captcha'd immediately.
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent": _UA,
    "Accept-Language": "en-US,en;q=0.9,ta;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

DEFAULT_BRANDS = ["Example Broadcaster", "Example News", "Example Entertainment", "Example Movies"]
DEFAULT_OFFICIAL_DOMAINS = ["example-broadcaster.in", "example-news.in", "example-media.in", "example-movies.in"]

# Hard ceiling on check_lookalike_domains() so a run of slow/unresponsive
# domains can't approach the caller's subprocess timeout (120s).
_LOOKALIKE_SCAN_BUDGET_SECONDS = 30.0

# Telegram channel-slug variants worth probing for each scan.
_TELEGRAM_VARIANTS = [
    "examplebroadcaster",
    "brand_main",
    "examplenews",
    "brand_news",
    "exampleentertainment",
    "examplemovies",
    "examplebroadcaster",
    "examplenews",
]

# Lookalike / typo-squat domains grouped by the trick they use.
_LOOKALIKE_DOMAINS = [
    ("example-broadcaster.in", "tld_swap"),
    ("example-broadcaster.in", "hyphenation"),
    ("example-broadcaster.in", "keyword_append"),
    ("example-media.in", "keyword_append"),
    ("example-news.in", "tld_swap"),
    ("example-news.in", "hyphenation"),
    ("example-news.in", "keyword_append"),
    ("j-movies.in", "hyphenation"),
    ("example-movies.in", "tld_swap"),
    ("example-movies.in", "keyword_append"),
    ("example-broadcaster.in", "brand_claim"),
    ("example-broadcaster.in", "brand_claim"),
    ("example-broadcaster.in", "brand_claim"),
]


# Markers of a domain-parking / for-sale / not-yet-launched page. A domain
# serving one of these is registered but is not impersonating anything.
_PARKING_MARKERS = (
    "is for sale", "for sale |", "buy this domain", "domain is for sale",
    "this domain", "hugedomains", "sedo", "afternic", "dan.com", "spaceship",
    "godaddy", "namecheap parking", "parked domain", "domain parking",
    "coming soon", "under construction", "website coming soon",
    "future home of", "default web page", "it works!",
    "buy now for", "make an offer", "inquire about this domain",
    # Doorway / holding pages that serve nothing but an entry link.
    "click here to enter", "enter site", "this domain is",
)


def _clean_text(text: str) -> str:
    """Collapse whitespace in scraped text."""
    return re.sub(r"\s+", " ", (text or "")).strip()


def _slug(text: str) -> str:
    """Compact alphanumeric slug: 'Example Broadcaster' -> 'examplebroadcaster'."""
    return "".join(c.lower() for c in text if c.isalnum())


def _brand_tokens(brand: str) -> list[str]:
    """Lowercase word tokens of the brand name, for keyword matching.

    Single-character tokens (e.g. the "J" in "Example Movies") are dropped: as a
    substring match against arbitrary page text they match almost anything,
    which turned unrelated pages into false-positive impersonation hits.
    """
    return [t for t in re.split(r"\s+", brand.lower()) if len(t) > 1]


def _platform_for(host: str) -> str:
    host = host.lower()
    if "facebook.com" in host or "fb.com" in host:
        return "facebook"
    if "instagram.com" in host:
        return "instagram"
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if "t.me" in host or "telegram.me" in host:
        return "telegram"
    if "twitter.com" in host or "x.com" in host:
        return "x"
    return host or "web"


def _is_official(host: str, official_domains: list[str]) -> bool:
    host = host.lower()
    return any(host == d or host.endswith("." + d) for d in official_domains)


def _dedup_by_url(items: list[dict]) -> list[dict]:
    """Collapse to one entry per URL, keeping the highest suspicion_score."""
    best: dict[str, dict] = {}
    for item in items:
        url = item.get("url", "")
        if url not in best or item["suspicion_score"] > best[url]["suspicion_score"]:
            best[url] = item
    return sorted(best.values(), key=lambda i: i["suspicion_score"], reverse=True)


class FakePageDetector:
    """Live impersonation reconnaissance across Google, Facebook, Telegram, DNS."""

    def __init__(self, official_domains: list[str] | None = None):
        self.official_domains = official_domains or list(DEFAULT_OFFICIAL_DOMAINS)

    # ── Google ────────────────────────────────────────────────────────────
    def search_google(self, brand: str) -> list[dict]:
        """Scrape Google for social profiles claiming the brand.

        Query pins the search to the major social/messaging platforms and flags
        any result whose URL carries the brand slug but is not on an official
        Broadcaster domain (i.e. a third-party page riding the brand name).
        """
        query = (
            f'"{brand}" official site:facebook.com OR site:instagram.com '
            f"OR site:youtube.com OR site:t.me OR site:telegram.me"
        )
        url = "https://www.google.com/search?" + "q=" + quote_plus(query) + "&num=20"
        results: list[dict] = []
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=10)
            if resp.status_code != 200:
                logger.warning("Google search HTTP %s for %r", resp.status_code, brand)
                return results
            soup = BeautifulSoup(resp.text, "html.parser")
            for anchor in soup.select("a"):
                href = anchor.get("href", "")
                # Google wraps result links as /url?q=<real>&sa=...
                real = href
                if href.startswith("/url?"):
                    m = re.search(r"[?&]q=([^&]+)", href)
                    if not m:
                        continue
                    real = requests.utils.unquote(m.group(1))
                if not real.startswith("http"):
                    continue
                host = urlparse(real).netloc
                platform = _platform_for(host)
                if platform not in {"facebook", "instagram", "youtube", "telegram"}:
                    continue
                h3 = anchor.find("h3")
                title = h3.get_text(strip=True) if h3 else anchor.get_text(strip=True)
                if not title:
                    continue
                # Snippet: nearest enclosing text block, best-effort.
                snippet = ""
                container = anchor.find_parent("div")
                if container:
                    snippet = container.get_text(" ", strip=True)[:280]

                score = self._score_web_result(brand, real, title, host)
                results.append(
                    {
                        "url": real,
                        "title": title,
                        "snippet": snippet,
                        "platform": platform,
                        "suspicion_score": round(score, 2),
                    }
                )
                if len(results) >= 10:
                    break
        except requests.RequestException as exc:
            logger.warning("Google search failed for %r: %s", brand, exc)
        return results

    def _score_web_result(self, brand: str, url: str, title: str, host: str) -> float:
        slug = _slug(brand)
        title_l = title.lower()
        url_l = url.lower()
        official = _is_official(host, self.official_domains)
        if official:
            return 0.1
        score = 0.0
        # URL carries the brand slug but is not on an official domain.
        if slug in _slug(url):
            score = max(score, 0.8)
        # Any brand token present in the URL path.
        if any(tok in url_l for tok in _brand_tokens(brand)):
            score = max(score, 0.65)
        # Claims to be "official" while off-domain — strong impersonation tell.
        if "official" in title_l and slug not in host:
            score = max(score, 0.9)
        # Title uses the full brand name.
        if all(tok in title_l for tok in _brand_tokens(brand)):
            score = max(score, 0.6)
        return score

    # ── Facebook ──────────────────────────────────────────────────────────
    def scrape_facebook_public(self, brand: str) -> list[dict]:
        """Facebook public directory lookup for pages named after the brand."""
        url = "https://www.facebook.com/public/" + quote_plus(brand)
        results: list[dict] = []
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=8)
            if resp.status_code != 200:
                logger.warning("Facebook public HTTP %s for %r", resp.status_code, brand)
                return results
            soup = BeautifulSoup(resp.text, "html.parser")
            seen: set[str] = set()
            for anchor in soup.select("a[href]"):
                href = anchor.get("href", "")
                if "facebook.com/" not in href or "/public/" in href:
                    continue
                page_name = anchor.get_text(strip=True)
                if not page_name or href in seen:
                    continue
                # Only care about pages whose name references the brand.
                if not all(tok in page_name.lower() for tok in _brand_tokens(brand)):
                    continue
                seen.add(href)
                host = urlparse(href).netloc
                score = self._score_web_result(brand, href, page_name, host)
                # A brand-named FB page is unofficial by construction here.
                score = max(score, 0.7)
                results.append(
                    {
                        "page_name": page_name,
                        "url": href,
                        "followers": None,
                        "suspicion_score": round(score, 2),
                    }
                )
                if len(results) >= 10:
                    break
        except requests.RequestException as exc:
            logger.warning("Facebook public scrape failed for %r: %s", brand, exc)
        return results

    # ── Telegram ──────────────────────────────────────────────────────────
    def scrape_telegram_public(self, brand: str) -> list[dict]:
        """Probe Telegram public channels + the t.me/s search preview.

        Telegram exposes channel previews at https://t.me/s/<slug> without login,
        so this is the probe most likely to return live impersonator data.

        Up to 9 slug variants are probed per brand. Each is an independent
        8s-timeout HTTP call, so fetching them one at a time could take up to
        ~72s for a single brand — fan them out on their own small pool instead
        of blocking the caller's outer probe pool for that long.
        """
        variants = list(_TELEGRAM_VARIANTS)
        brand_slug = _slug(brand)
        if brand_slug not in {v.replace("_", "") for v in _TELEGRAM_VARIANTS}:
            variants.append(brand_slug)

        results: list[dict] = []
        seen: set[str] = set()
        with ThreadPoolExecutor(max_workers=len(variants)) as pool:
            futures = [pool.submit(self._fetch_telegram_channel, brand, v) for v in variants]
            for future in as_completed(futures):
                try:
                    channel = future.result()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Telegram variant fetch failed for %r: %s", brand, exc)
                    continue
                if channel and channel["url"] not in seen:
                    seen.add(channel["url"])
                    results.append(channel)

        return results

    def _fetch_telegram_channel(self, brand: str, slug: str) -> dict | None:
        url = f"https://t.me/s/{slug}"
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=8)
        except requests.RequestException as exc:
            logger.warning("Telegram fetch failed for %r: %s", slug, exc)
            return None
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")

        # .tgme_channel_info_header_title is the only dependable signal that a
        # *real* channel with actual post content exists. A non-existent or
        # unclaimed handle still returns HTTP 200 and still carries an og:title
        # meta tag — but that meta tag is unreliable as a text-matching target:
        # it's sometimes literally "Telegram: Contact @slug", and sometimes just
        # the bare display name (with the "you can contact @slug" tell buried in
        # og:description instead). Both are the same contact-card fallback page,
        # not a channel, so we don't try to text-match either meta field — we
        # require the real header element instead.
        header = soup.select_one(".tgme_channel_info_header_title")
        if header is None:
            return None
        channel_name = header.get_text(strip=True)
        if not channel_name or channel_name.lower() == "telegram":
            return None

        desc_el = soup.select_one(".tgme_channel_info_description") or soup.select_one(
            "meta[property='og:description']"
        )
        description = ""
        if desc_el is not None:
            description = (
                desc_el.get_text(" ", strip=True)
                if desc_el.name != "meta"
                else desc_el.get("content", "").strip()
            )

        members = self._telegram_members(soup)
        score = self._score_telegram(brand, channel_name, description)
        return {
            "channel_name": channel_name,
            "url": f"https://t.me/{slug}",
            "members": members,
            "description": description[:280],
            "suspicion_score": round(score, 2),
        }

    @staticmethod
    def _telegram_members(soup: BeautifulSoup) -> int | None:
        for counter in soup.select(".tgme_channel_info_counter"):
            kind = counter.select_one(".counter_type")
            value = counter.select_one(".counter_value")
            if kind and value and "subscriber" in kind.get_text(strip=True).lower():
                digits = re.sub(r"[^\d]", "", value.get_text(strip=True))
                if digits:
                    return int(digits)
        return None

    def _score_telegram(self, brand: str, name: str, description: str) -> float:
        tokens = _brand_tokens(brand)
        blob = f"{name} {description}".lower()
        matched = sum(1 for tok in tokens if tok in blob)
        if matched == 0:
            return 0.3
        score = 0.55 + 0.15 * matched  # more brand tokens => more suspicious
        # Explicit "official" claim on an unofficial Telegram channel.
        if "official" in blob:
            score = max(score, 0.9)
        # Broadcast/news framing riding the brand.
        if any(kw in blob for kw in ("news", "live", "breaking", "channel")):
            score = max(score, 0.8)
        return min(score, 0.95)

    # ── Lookalike domains ─────────────────────────────────────────────────
    def check_lookalike_domains(self, brand: str, official_domain: str) -> list[dict]:
        """Classify lookalike / typo-squat domains by what they actually serve.

        A domain resolving in DNS proves only that somebody registered it —
        most typo-squat candidates are parking pages, for-sale listings, or
        dead hosts owned by unrelated parties. Scoring on resolution alone
        turned every registered domain into a HIGH-severity impersonation
        threat, which is what previously filled the threats table.

        So we resolve, then fetch, then score on the content:

          brand_impersonation  — serves Broadcaster brand tokens        → 0.75–0.95
          unrelated_content    — serves something else entirely  → 0.30
          parked_for_sale      — parking / for-sale / coming-soon → 0.10
          registered_no_content— resolves but will not serve      → 0.15
          unregistered         — does not resolve                → 0.0

        Only `brand_impersonation` clears the 0.7 persistence threshold.
        """
        results: list[dict] = []
        started = time.monotonic()
        for i, (domain, variant_type) in enumerate(_LOOKALIKE_DOMAINS):
            if time.monotonic() - started > _LOOKALIKE_SCAN_BUDGET_SECONDS:
                skipped = [d for d, _ in _LOOKALIKE_DOMAINS[i:]]
                logger.warning(
                    "check_lookalike_domains: %.1fs budget exceeded — skipping %d "
                    "remaining domain(s): %s",
                    _LOOKALIKE_SCAN_BUDGET_SECONDS,
                    len(skipped),
                    skipped,
                )
                break
            resolves = self._resolves(domain, timeout=2.0)
            if not resolves:
                results.append({
                    "domain": domain,
                    "resolves": False,
                    "suspicion_score": 0.0,
                    "variant_type": variant_type,
                    "classification": "unregistered",
                    "evidence": "Does not resolve in DNS.",
                })
                continue

            classification, score, evidence = self._classify_domain_content(domain, brand)
            results.append({
                "domain": domain,
                "resolves": True,
                "suspicion_score": score,
                "variant_type": variant_type,
                "classification": classification,
                "evidence": evidence,
            })
        return results

    def _classify_domain_content(self, domain: str, brand: str) -> tuple[str, float, str]:
        """Fetch a resolving lookalike domain and judge what it is actually serving."""
        html = ""
        for scheme in ("https", "http"):
            try:
                resp = requests.get(
                    f"{scheme}://{domain}", headers=_HEADERS, timeout=8, allow_redirects=True
                )
                if resp.status_code < 400 and resp.text:
                    html = resp.text
                    break
            except requests.RequestException:
                continue

        if not html:
            return (
                "registered_no_content",
                0.15,
                "Resolves in DNS but serves no reachable page (timeout, TLS failure, or error).",
            )

        soup = BeautifulSoup(html, "html.parser")
        title = _clean_text(soup.title.get_text() if soup.title else "")
        body = _clean_text(soup.get_text(" "))[:4000]
        blob = f"{title} {body}".lower()

        # Domain parking / resale — registered by a squatter or broker, serving
        # no impersonating content. Not a brand threat.
        if any(marker in blob for marker in _PARKING_MARKERS):
            return (
                "parked_for_sale",
                0.10,
                f"Parking / for-sale page — title: {title[:120] or '(none)'}",
            )

        # Brand tokens must come from the CONTENT, not from the domain name
        # echoed back at us. Every lookalike contains the brand by construction
        # (that is what makes it a lookalike), so a parking page whose only text
        # is its own hostname would otherwise score as impersonation. Strip the
        # domain and its labels before matching.
        content_blob = blob
        for fragment in {domain.lower(), *domain.lower().split(".")}:
            if fragment:
                content_blob = content_blob.replace(fragment, " ")

        tokens = _brand_tokens(brand)
        matched = [tok for tok in tokens if tok in content_blob]
        if matched:
            score = min(0.75 + 0.10 * len(matched), 0.95)
            if any(kw in content_blob for kw in ("live", "news", "official", "watch")):
                score = min(score + 0.05, 0.95)
            return (
                "brand_impersonation",
                round(score, 2),
                f"Serves brand tokens {matched} — title: {title[:120] or '(none)'}",
            )

        return (
            "unrelated_content",
            0.30,
            f"Live site with no Broadcaster branding — title: {title[:120] or '(none)'}",
        )

    @staticmethod
    def _resolves(domain: str, timeout: float = 2.0) -> bool:
        # gethostbyname ignores socket.setdefaulttimeout on some resolvers,
        # so bound it with a worker thread we can abandon. A `with` block would
        # call pool.shutdown(wait=True) on exit and block until the orphaned
        # gethostbyname() call finishes anyway — defeating the timeout — so the
        # pool is shut down without waiting instead, leaving the stray thread to
        # finish (and get garbage collected) on its own.
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(socket.gethostbyname, domain)
        try:
            future.result(timeout=timeout)
            return True
        except Exception:  # noqa: BLE001 — NXDOMAIN, timeout, etc.
            return False
        finally:
            pool.shutdown(wait=False)

    # ── Full scan ─────────────────────────────────────────────────────────
    def run_full_scan(self, brands: list[str] | None = None) -> dict:
        started = time.monotonic()
        brands = brands or list(DEFAULT_BRANDS)
        official = self.official_domains

        google_results: list[dict] = []
        facebook_results: list[dict] = []
        telegram_results: list[dict] = []
        domain_results: list[dict] = []

        # Fan the four probe families out across brands in parallel. One worker
        # per top-level task (4 brands x google/facebook/telegram + 1 domain
        # batch) so no probe sits queued behind another while the ceiling above
        # (scan_threats' per-step budget) is ticking.
        with ThreadPoolExecutor(max_workers=len(brands) * 3 + 1) as pool:
            futures = {}
            for brand in brands:
                futures[pool.submit(self.search_google, brand)] = ("google", brand)
                futures[pool.submit(self.scrape_facebook_public, brand)] = (
                    "facebook",
                    brand,
                )
                futures[pool.submit(self.scrape_telegram_public, brand)] = (
                    "telegram",
                    brand,
                )
            # Lookalike domains are brand-independent — run the set once.
            primary_domain = official[0] if official else "example-broadcaster.in"
            futures[
                pool.submit(self.check_lookalike_domains, brands[0], primary_domain)
            ] = ("domain", brands[0])

            for future in as_completed(futures):
                kind, brand = futures[future]
                try:
                    out = future.result()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("%s probe failed for %r: %s", kind, brand, exc)
                    continue
                if kind == "google":
                    google_results.extend(out)
                elif kind == "facebook":
                    facebook_results.extend(out)
                elif kind == "telegram":
                    telegram_results.extend(out)
                elif kind == "domain":
                    domain_results.extend(out)

        # The same channel/page surfaces once per brand (variants are shared);
        # collapse to the single highest-suspicion hit per URL.
        google_results = _dedup_by_url(google_results)
        facebook_results = _dedup_by_url(facebook_results)
        telegram_results = _dedup_by_url(telegram_results)

        threats_created = self._raise_threats(
            google_results, facebook_results, telegram_results, domain_results
        )

        scan_time_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "Fake-page scan: google=%d facebook=%d telegram=%d domains=%d "
            "threats=%d in %dms",
            len(google_results),
            len(facebook_results),
            len(telegram_results),
            len(domain_results),
            threats_created,
            scan_time_ms,
        )
        return {
            "google_results": google_results,
            "facebook_results": facebook_results,
            "telegram_results": telegram_results,
            "domain_results": domain_results,
            "threats_created": threats_created,
            "scan_time_ms": scan_time_ms,
        }

    # ── Threat persistence ────────────────────────────────────────────────
    def _raise_threats(
        self,
        google_results: list[dict],
        facebook_results: list[dict],
        telegram_results: list[dict],
        domain_results: list[dict],
    ) -> int:
        """Persist a FAKE_PAGE threat for every hit scoring >= 0.7."""
        payloads = []
        for r in google_results:
            if r["suspicion_score"] >= 0.7:
                payloads.append(
                    self._payload(
                        title=f"Impersonator {r['platform']} page: {r['title'][:80]}",
                        description=(
                            f"Google surfaced a {r['platform']} profile using Broadcaster "
                            f"branding at {r['url']} (suspicion "
                            f"{r['suspicion_score']:.2f}). Snippet: {r['snippet'][:160]}"
                        ),
                        platform=r["platform"],
                        url=r["url"],
                        score=r["suspicion_score"],
                    )
                )
        for r in facebook_results:
            if r["suspicion_score"] >= 0.7:
                payloads.append(
                    self._payload(
                        title=f"Fake Facebook page: {r['page_name'][:80]}",
                        description=(
                            f"Facebook public directory page '{r['page_name']}' "
                            f"({r['url']}) references Broadcaster branding without "
                            f"authorization (suspicion {r['suspicion_score']:.2f})."
                        ),
                        platform="facebook",
                        url=r["url"],
                        score=r["suspicion_score"],
                    )
                )
        for r in telegram_results:
            if r["suspicion_score"] >= 0.7:
                members = f"{r['members']:,}" if r["members"] else "unknown"
                payloads.append(
                    self._payload(
                        title=f"Impersonator Telegram channel: {r['channel_name'][:70]}",
                        description=(
                            f"Telegram channel '{r['channel_name']}' ({r['url']}, "
                            f"{members} members) is using Broadcaster branding "
                            f"(suspicion {r['suspicion_score']:.2f}). "
                            f"{r['description'][:160]}"
                        ),
                        platform="telegram",
                        url=r["url"],
                        score=r["suspicion_score"],
                    )
                )
        for r in domain_results:
            if r["suspicion_score"] >= 0.7:
                payloads.append(
                    self._payload(
                        title=f"Lookalike domain serving Broadcaster branding: {r['domain']}",
                        description=(
                            f"Lookalike domain {r['domain']} ({r['variant_type']}) is live "
                            f"and serving Example Broadcaster brand content — candidate phishing / "
                            f"brand-impersonation site (suspicion "
                            f"{r['suspicion_score']:.2f}). "
                            f"Evidence: {r.get('evidence', 'n/a')}"
                        ),
                        platform="web",
                        url=f"http://{r['domain']}",
                        score=r["suspicion_score"],
                    )
                )

        if not payloads:
            return 0

        # DB layer lives in threat_intel_agent (imported lazily to keep the
        # detector usable standalone / without a DB).
        try:
            from sqlalchemy.orm import Session

            from agents.kaaval.threat_intel_agent import _persist_threat, engine
        except Exception as exc:  # noqa: BLE001
            logger.warning("Threat persistence unavailable: %s", exc)
            return 0

        created = 0
        with Session(engine) as session:
            for payload in payloads:
                _persist_threat(session, None, payload)
                created += 1
            session.commit()
        return created

    @staticmethod
    def _payload(title: str, description: str, platform: str, url: str, score: float) -> dict:
        severity = "CRITICAL" if score >= 0.9 else "HIGH" if score >= 0.8 else "MEDIUM"
        return {
            "threat_type": "FAKE_PAGE",
            "severity": severity,
            "title": title,
            "description": description,
            "source_platform": platform,
            "source_url": url,
        }

    # ── Meta (Facebook + Instagram) Graph API scan ────────────────────────
    def scan_meta(self, brands: list[str] | None = None) -> dict:
        """Run the authenticated Meta Graph API scan (delegates to module fn)."""
        return scan_meta(brands)


# Brand → platform_config credential slug (Example Broadcaster → brand_main, Example Movies → brand_movies).
_BRAND_SLUGS = {
    "Example Broadcaster": "brand_main",
    "Example News": "brand_news",
    "Example Entertainment": "brand_entertainment",
    "Example Movies": "brand_movies",
}


def _load_meta_config() -> dict:
    """Read all module='meta' rows from platform_config."""
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from agents.kaaval.threat_intel_agent import engine

    with Session(engine) as db:
        rows = db.execute(
            text("SELECT key, value FROM platform_config WHERE module = 'meta'")
        ).fetchall()
    return {k: v for k, v in rows}


def _set_meta_threat_score(score: int) -> None:
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from agents.kaaval.threat_intel_agent import engine

    with Session(engine) as db:
        db.execute(
            text(
                "INSERT INTO platform_config (module, key, value, updated_at) "
                "VALUES ('meta', 'meta_threat_score', :v, NOW()) "
                "ON CONFLICT (module, key) DO UPDATE "
                "SET value = EXCLUDED.value, updated_at = NOW()"
            ),
            {"v": str(int(score))},
        )
        db.commit()


def scan_meta(brands: list[str] | None = None) -> dict:
    """Authenticated Meta scan — HUMAN-IN-THE-LOOP, never auto-strikes.

    Loads credentials from platform_config. Every finding is persisted as a
    threat with status='detected' (never 'confirmed') and a judge blob carrying
    reach + platform + requires_human_review=True. Nothing is filed or reported
    to Meta — that only happens after an editor confirms, downstream.
    """
    brands = brands or list(DEFAULT_BRANDS)
    config = _load_meta_config()

    if str(config.get("meta_integration_enabled", "false")).lower() != "true":
        return {"status": "not_configured", "threats_created": 0}

    from agents.kaaval.meta_client import MetaClient

    page_tokens = {
        slug: config.get(f"meta_page_token_{slug}", "")
        for slug in _BRAND_SLUGS.values()
    }
    ig_accounts = {
        slug: config.get(f"meta_instagram_account_{slug}", "")
        for slug in _BRAND_SLUGS.values()
    }
    client = MetaClient(
        app_id=config.get("meta_app_id", ""),
        app_secret=config.get("meta_app_secret", ""),
        page_tokens=page_tokens,
        ig_accounts=ig_accounts,
    )
    if not client.is_configured():
        return {"status": "not_configured", "threats_created": 0}

    fake_pages: list[dict] = []
    brigades: list[dict] = []
    ig_misuse: list[dict] = []

    for brand in brands:
        slug = _BRAND_SLUGS.get(brand)
        page_token = page_tokens.get(slug, "") if slug else ""
        ig_account = ig_accounts.get(slug, "") if slug else ""

        for fp in client.search_fake_pages(brand):
            fp["brand"] = brand
            fake_pages.append(fp)

        # Brigade signal on the official page (page_id == the brand's own page).
        if page_token:
            neg = client.get_page_negative_feedback(page_token, f"{slug}_official")
            neg["brand"] = brand
            if neg.get("brigade_suspected"):
                brigades.append(neg)

        if ig_account and page_token:
            for post in client.get_instagram_tagged_posts(ig_account, page_token):
                post["brand"] = brand
                ig_misuse.append(post)

    meta_score = client.calculate_meta_threat_score(fake_pages, brigades, ig_misuse)

    threats_created = _persist_meta_threats(client, fake_pages, brigades, ig_misuse)
    _set_meta_threat_score(meta_score)

    logger.info(
        "Meta scan: fake_pages=%d brigades=%d ig_misuse=%d threats=%d score=%d",
        len(fake_pages), len(brigades), len(ig_misuse), threats_created, meta_score,
    )
    return {
        "status": "ok",
        "fake_pages_found": len(fake_pages),
        "brigades_detected": len(brigades),
        "ig_misuse_found": len(ig_misuse),
        "threats_created": threats_created,
        "meta_threat_score": meta_score,
    }


def _persist_meta_threats(client, fake_pages, brigades, ig_misuse) -> int:
    """Persist every Meta finding as a status='detected' threat (human-in-loop)."""
    from datetime import datetime, timezone

    from sqlalchemy.orm import Session

    from agents.kaaval.threat_intel_agent import Threat, engine

    created = 0
    with Session(engine) as session:
        for fp in fake_pages:
            reach = client.calculate_reach(fp.get("followers", 0), "post")
            session.add(Threat(
                threat_type="FAKE_PAGE",
                severity="HIGH" if fp.get("suspicion_score", 0) >= 0.8 else "MEDIUM",
                title=f"Meta fake page: {(fp.get('name') or '')[:80]}",
                description=(
                    f"Facebook page '{fp.get('name')}' ({fp.get('url')}) uses "
                    f"{fp.get('brand')} branding without blue verification. "
                    f"{fp.get('followers', 0):,} followers · estimated reach "
                    f"{reach['estimated_reach']:,}. Suspicion {fp.get('suspicion_score')}."
                ),
                source_platform="facebook",
                source_url=fp.get("url"),
                detected_at=datetime.now(timezone.utc),
                status="detected",
                judge={
                    "reach": reach,
                    "platform": "facebook",
                    "requires_human_review": True,
                    "meta_finding": "FAKE_PAGE",
                },
            ))
            created += 1

        for b in brigades:
            reach = client.calculate_reach(0, "post")
            session.add(Threat(
                threat_type="BRIGADE",
                severity="HIGH",
                title=f"Comment brigade on {b.get('brand')} page",
                description=(
                    f"Negative-feedback spike on the official {b.get('brand')} page "
                    f"(current {b.get('current_rate')} vs baseline {b.get('baseline_rate')}, "
                    f">3x). Coordinated brigade suspected · confidence {b.get('confidence')}."
                ),
                source_platform="facebook",
                source_url=None,
                detected_at=datetime.now(timezone.utc),
                status="detected",
                judge={
                    "reach": reach,
                    "platform": "facebook",
                    "requires_human_review": True,
                    "meta_finding": "BRIGADE",
                    "brigade": b,
                },
            ))
            created += 1

        for post in ig_misuse:
            reach = client.calculate_reach(0, post.get("media_type", "post"))
            session.add(Threat(
                threat_type="INSTAGRAM_MISUSE",
                severity="MEDIUM",
                title=f"Instagram misuse by @{post.get('username')}",
                description=(
                    f"Instagram {post.get('media_type')} by @{post.get('username')} "
                    f"({post.get('permalink')}) tags an official Broadcaster account with a "
                    f"suspicious caption: {post.get('caption', '')[:160]}"
                ),
                source_platform="instagram",
                source_url=post.get("permalink"),
                detected_at=datetime.now(timezone.utc),
                status="detected",
                judge={
                    "reach": reach,
                    "platform": "instagram",
                    "requires_human_review": True,
                    "meta_finding": "INSTAGRAM_MISUSE",
                },
            ))
            created += 1

        session.commit()
    return created


def run_full_scan(brands: list[str] | None = None) -> dict:
    """Module-level entry point used by the API subprocess bridge."""
    return FakePageDetector().run_full_scan(brands)


if __name__ == "__main__":
    logging.basicConfig(level="INFO", format="%(asctime)s [%(levelname)s] %(message)s")
    import json

    print(json.dumps(run_full_scan(), indent=2))
