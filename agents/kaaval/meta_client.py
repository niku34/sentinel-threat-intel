"""Meta (Facebook + Instagram) Graph API client for Kaaval.

Read-only reconnaissance against the Meta Graph API to surface brand
impersonation, comment brigades, and Instagram misuse targeting Example Broadcaster brands.

HUMAN-IN-THE-LOOP: this client only *detects*. It never strikes, reports, or
files anything on Meta platforms. Every finding it produces is a candidate for
editorial review — the DETECTED → confirm → draft → file-manually workflow lives
downstream, exactly like the Content Protection DMCA drafter.

Pure `requests` + stdlib, so it imports cleanly in both venvs. Every network call
is wrapped: a failure, block, or missing credential degrades to an empty result
rather than raising, so a Meta outage never takes a Kaaval scan down.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger("meta_client")

# Keywords that make an Instagram tag / public post worth flagging for review.
_SUSPICIOUS_KEYWORDS = ("fake", "deepfake", "unauthorized", "unauthorised", "stolen", "clone")

# Engagement rates used to estimate reach per media type.
_ENGAGEMENT_RATES = {"post": 0.03, "reel": 0.06, "story": 0.04}


class MetaClient:
    BASE_URL = "https://graph.facebook.com/v18.0"

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        page_tokens: dict | None = None,
        ig_accounts: dict | None = None,
        timeout: int = 10,
    ) -> None:
        self.app_id = app_id or ""
        self.app_secret = app_secret or ""
        self.page_tokens = {k: v for k, v in (page_tokens or {}).items() if v}
        self.ig_accounts = {k: v for k, v in (ig_accounts or {}).items() if v}
        self.timeout = timeout
        # App-level token: "{app_id}|{app_secret}".
        self.app_token = f"{self.app_id}|{self.app_secret}" if self.app_id and self.app_secret else ""

    # ── configuration ──────────────────────────────────────
    def is_configured(self) -> bool:
        """True only when app_id + app_secret + at least one page token are set."""
        return bool(self.app_id and self.app_secret and self.page_tokens)

    def _any_page_token(self) -> str | None:
        for token in self.page_tokens.values():
            if token:
                return token
        return None

    # ── low-level GET ──────────────────────────────────────
    def _get(self, path: str, params: dict) -> dict | None:
        url = f"{self.BASE_URL}/{path.lstrip('/')}"
        try:
            resp = requests.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            logger.warning("Meta GET %s failed: %s", path, exc)
            return None
        if resp.status_code != 200:
            logger.warning("Meta GET %s HTTP %s: %s", path, resp.status_code, resp.text[:200])
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    # ── fake page search ───────────────────────────────────
    def search_fake_pages(self, brand: str) -> list[dict]:
        """Search Meta pages for the brand; flag unverified pages using the name."""
        if not self.is_configured():
            return []
        data = self._get(
            "search",
            {
                "type": "page",
                "q": brand,
                "fields": "id,name,fan_count,verification_status,link",
                "access_token": self.app_token,
            },
        )
        if not data:
            return []

        brand_l = brand.lower()
        results: list[dict] = []
        for page in data.get("data", []):
            name = (page.get("name") or "")
            verification = (page.get("verification_status") or "").lower()
            # Skip officially blue-verified pages; only flag pages whose name
            # references the brand.
            if verification == "blue_verified":
                continue
            if brand_l not in name.lower():
                continue
            followers = int(page.get("fan_count") or 0)
            results.append(
                {
                    "page_id": page.get("id"),
                    "name": name,
                    "url": page.get("link") or f"https://facebook.com/{page.get('id')}",
                    "followers": followers,
                    "suspicion_score": self._page_suspicion(name, brand, verification, followers),
                    "platform": "facebook",
                    "status": "DETECTED",
                }
            )
        return results

    @staticmethod
    def _page_suspicion(name: str, brand: str, verification: str, followers: int) -> float:
        score = 0.6
        low = name.lower()
        if "official" in low:
            score = max(score, 0.9)
        if verification in ("", "not_verified"):
            score = max(score, 0.75)
        if followers > 5000:
            score = min(score + 0.1, 0.95)
        return round(score, 2)

    # ── page negative feedback (brigade signal) ────────────
    def get_page_negative_feedback(self, page_token: str, page_id: str) -> dict:
        """Compare last-24h negative feedback against the 7-day baseline."""
        result = {
            "page_id": page_id,
            "current_rate": 0.0,
            "baseline_rate": 0.0,
            "brigade_suspected": False,
            "confidence": 0.0,
        }
        if not page_token:
            return result
        data = self._get(
            f"{page_id}/insights",
            {
                "metric": "page_negative_feedback",
                "period": "day",
                "access_token": page_token,
            },
        )
        if not data:
            return result

        values: list[float] = []
        for metric in data.get("data", []):
            for v in metric.get("values", []):
                try:
                    values.append(float(v.get("value") or 0))
                except (TypeError, ValueError):
                    continue
        if not values:
            return result

        current = values[-1]
        baseline_window = values[-8:-1] if len(values) > 1 else values[:-1]
        baseline = (sum(baseline_window) / len(baseline_window)) if baseline_window else 0.0

        brigade = baseline > 0 and current > (3 * baseline)
        confidence = 0.0
        if brigade:
            confidence = round(min(0.5 + (current / (baseline * 6)), 0.95), 2)

        result.update(
            {
                "current_rate": round(current, 2),
                "baseline_rate": round(baseline, 2),
                "brigade_suspected": bool(brigade),
                "confidence": confidence,
            }
        )
        return result

    # ── instagram tagged posts ─────────────────────────────
    def get_instagram_tagged_posts(self, ig_account_id: str, page_token: str) -> list[dict]:
        """Posts tagging the official IG account whose caption looks suspicious."""
        if not ig_account_id or not page_token:
            return []
        data = self._get(
            f"{ig_account_id}/tags",
            {
                "fields": "id,caption,media_type,timestamp,permalink,username",
                "access_token": page_token,
            },
        )
        if not data:
            return []

        results: list[dict] = []
        for post in data.get("data", []):
            caption = (post.get("caption") or "")
            low = caption.lower()
            hits = [kw for kw in _SUSPICIOUS_KEYWORDS if kw in low]
            if not hits:
                continue
            results.append(
                {
                    "post_id": post.get("id"),
                    "username": post.get("username"),
                    "caption": caption[:280],
                    "permalink": post.get("permalink"),
                    "media_type": (post.get("media_type") or "post").lower(),
                    "suspicion_score": round(min(0.6 + 0.1 * len(hits), 0.95), 2),
                    "platform": "instagram",
                    "status": "DETECTED",
                }
            )
        return results

    # ── public post search ─────────────────────────────────
    def search_public_posts(self, brand: str, page_token: str) -> list[dict]:
        """Recent public posts mentioning the brand in a suspicious context."""
        if not page_token:
            return []
        data = self._get(
            "search",
            {
                "type": "post",
                "q": brand,
                "fields": "id,message,created_time,from",
                "access_token": page_token,
            },
        )
        if not data:
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        results: list[dict] = []
        for post in data.get("data", []):
            message = (post.get("message") or "")
            low = message.lower()
            if brand.lower() not in low:
                continue
            if not any(kw in low for kw in _SUSPICIOUS_KEYWORDS):
                continue
            created = self._parse_time(post.get("created_time"))
            if created and created < cutoff:
                continue
            results.append(
                {
                    "post_id": post.get("id"),
                    "message": message[:280],
                    "url": f"https://facebook.com/{post.get('id')}",
                    "created_time": post.get("created_time"),
                    "platform": "facebook",
                    "status": "DETECTED",
                }
            )
        return results

    @staticmethod
    def _parse_time(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    # ── reach + threat score ───────────────────────────────
    def calculate_reach(self, followers: int, media_type: str = "post") -> dict:
        followers = int(followers or 0)
        rate = _ENGAGEMENT_RATES.get((media_type or "post").lower(), 0.03)
        return {
            "estimated_reach": int(followers * rate),
            "followers": followers,
            "engagement_rate": rate,
            "media_type": (media_type or "post").lower(),
        }

    def calculate_meta_threat_score(
        self, fake_pages: list, brigades: list, ig_misuse: list
    ) -> int:
        """0–100 composite risk score from the three finding families."""
        score = 0
        # Each fake page +15, capped at 45.
        score += min(len(fake_pages) * 15, 45)
        # Each detected brigade +20 (per channel).
        brigade_hits = [b for b in brigades if b and b.get("brigade_suspected")]
        score += len(brigade_hits) * 20
        # Instagram misuse +10 each, capped at 30.
        score += min(len(ig_misuse) * 10, 30)
        return int(min(score, 100))
