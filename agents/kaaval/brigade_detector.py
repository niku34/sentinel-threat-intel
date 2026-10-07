"""Brigade detection module for Kaaval AI.

Analyzes YouTube comment patterns for coordinated inauthentic behavior
targeting Example Broadcaster content.
"""
from __future__ import annotations

import difflib
import re
import statistics
from collections import Counter
from datetime import datetime, timedelta

# Tamil Unicode range: U+0B80–U+0BFF
_TAMIL_RE = re.compile(r"[\w஀-௿]+")

STOPWORDS: frozenset[str] = frozenset({
    # English
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "it", "this", "that", "was", "are",
    "be", "as", "not", "have", "had", "do", "did", "will", "would", "could",
    "should", "may", "might", "can", "has", "been", "being", "they", "we",
    "you", "he", "she", "i", "my", "your", "his", "her", "our", "their",
    "its", "what", "which", "who", "how", "when", "where", "why", "all",
    "any", "both", "each", "few", "more", "most", "other", "some", "such",
    "no", "nor", "only", "own", "same", "so", "than", "too", "very", "just",
    "about", "after", "also", "always", "before", "during", "if", "into",
    "like", "never", "now", "out", "over", "still", "there", "through", "up",
    # Tamil common function words / stopwords
    "இது", "அது", "என்று", "ஆனால்", "மற்றும்", "அல்லது", "ஒரு", "இந்த",
    "என்", "நான்", "அவர்", "அவள்", "இவர்", "உங்கள்", "நம்", "தான்",
    "போல", "என", "கொண்டு", "இல்லை", "உள்ள", "மேல்", "கீழ்",
})

VELOCITY_WINDOW_SECONDS = 5 * 60   # 5-minute burst window
VELOCITY_THRESHOLD = 50             # unique comments that trigger max score
SIMILARITY_MAX_PAIRS = 300          # cap O(n²) comparison
KEYWORD_DENSITY_THRESHOLD = 0.40    # 40 % of comments share top keyword
TIMING_STD_THRESHOLD_SECS = 60.0   # std dev below this signals tight clustering


def _parse_ts(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


class BrigadeDetector:
    """Multi-signal brigade detector for YouTube comment streams."""

    # ── public API ────────────────────────────────────────────────────────────

    def analyze_youtube_comments(self, video_id: str, comments: list[dict]) -> dict:
        """Full analysis with report attached, keyed by video_id."""
        result = self.analyze_comment_batch(comments)
        result["video_id"] = video_id
        result["report"] = self.generate_report(result, video_id)
        return result

    def analyze_comment_batch(self, comments: list[dict]) -> dict:
        """Core signal computation — returns structured analysis dict."""
        if not comments:
            return self._empty_result()

        texts = [c.get("text", "") for c in comments]
        valid_ts = [t for t in (_parse_ts(c.get("timestamp", "")) for c in comments) if t]

        velocity_score      = self._velocity_score(valid_ts)
        similarity_score    = self._similarity_score(texts)
        top_keywords        = self.extract_keywords(comments)
        kw_density_score    = self._keyword_density_score(texts, top_keywords)
        timing_score        = self._timing_cluster_score(valid_ts)

        signals = {
            "velocity":         round(velocity_score,   3),
            "similarity":       round(similarity_score,  3),
            "keyword_density":  round(kw_density_score,  3),
            "timing_cluster":   round(timing_score,      3),
        }

        triggered = [k for k, v in signals.items() if v > 0.6]
        brigade_detected = len(triggered) >= 2
        confidence = round(sum(signals.values()) / len(signals), 3)

        if brigade_detected:
            if confidence >= 0.80:
                recommendation = (
                    "ESCALATE — High-confidence coordinated brigade. "
                    "Immediate content-team alert and YouTube abuse report recommended."
                )
            else:
                recommendation = (
                    "MONITOR — Possible brigade pattern. "
                    "Flag for editorial review within 1 hour."
                )
        else:
            recommendation = "CLEAR — No coordinated brigade pattern detected."

        return {
            "brigade_detected":      brigade_detected,
            "confidence":            confidence,
            "signals":               signals,
            "triggered_signals":     triggered,
            "top_keywords":          top_keywords[:10],
            "affected_comment_count": len(comments),
            "recommendation":        recommendation,
        }

    def extract_keywords(self, comments: list[dict]) -> list[str]:
        """Top 10 recurring Tamil + English content words, stopwords removed."""
        words: list[str] = []
        for c in comments:
            for token in _TAMIL_RE.findall(c.get("text", "")):
                tl = token.lower()
                if tl not in STOPWORDS and len(tl) > 2:
                    words.append(tl)
        return [w for w, _ in Counter(words).most_common(10)]

    def generate_report(self, analysis: dict, video_id: str) -> str:
        kw_str   = ", ".join(analysis.get("top_keywords", [])[:5]) or "N/A"
        affected = analysis.get("affected_comment_count", 0)
        conf     = analysis.get("confidence", 0.0)
        reco     = analysis.get("recommendation", "")
        if analysis.get("brigade_detected"):
            return (
                f"Brigade detected on video {video_id}: "
                f"{affected} coordinated comments analysed. "
                f"Confidence: {conf:.0%}. "
                f"Top keywords: {kw_str}. "
                f"Recommendation: {reco}"
            )
        return (
            f"No brigade detected on video {video_id}. "
            f"Confidence: {conf:.0%}. "
            f"Recommendation: {reco}"
        )

    # ── signal scorers ────────────────────────────────────────────────────────

    def _velocity_score(self, valid_ts: list[datetime]) -> float:
        """Score = fraction of VELOCITY_THRESHOLD hit inside any 5-min window."""
        if len(valid_ts) < 2:
            return 0.0
        ts_sorted = sorted(valid_ts)
        window = timedelta(seconds=VELOCITY_WINDOW_SECONDS)
        max_in_window = 0
        left = 0
        for right in range(len(ts_sorted)):
            while ts_sorted[right] - ts_sorted[left] > window:
                left += 1
            max_in_window = max(max_in_window, right - left + 1)
        return min(1.0, max_in_window / VELOCITY_THRESHOLD)

    def _similarity_score(self, texts: list[str]) -> float:
        """Average pairwise similarity of comment texts (capped at SIMILARITY_MAX_PAIRS)."""
        if len(texts) < 2:
            return 0.0
        pairs: list[tuple[str, str]] = []
        for i in range(len(texts)):
            for j in range(i + 1, len(texts)):
                pairs.append((texts[i], texts[j]))
                if len(pairs) >= SIMILARITY_MAX_PAIRS:
                    break
            if len(pairs) >= SIMILARITY_MAX_PAIRS:
                break
        ratios = [difflib.SequenceMatcher(None, a, b).ratio() for a, b in pairs]
        avg = sum(ratios) / len(ratios)
        # Map avg to score: 0.8 → 1.0, 0.4 → 0.0
        return min(1.0, max(0.0, (avg - 0.4) / 0.4))

    def _keyword_density_score(self, texts: list[str], top_keywords: list[str]) -> float:
        """Fraction of comments containing the single most-frequent keyword."""
        if not texts or not top_keywords:
            return 0.0
        top_kw = top_keywords[0].lower()
        hits = sum(1 for t in texts if top_kw in t.lower())
        density = hits / len(texts)
        # 40% density → score 1.0
        return min(1.0, density / KEYWORD_DENSITY_THRESHOLD)

    def _timing_cluster_score(self, valid_ts: list[datetime]) -> float:
        """Score = 1 when std-dev of timestamps < TIMING_STD_THRESHOLD_SECS."""
        if len(valid_ts) < 3:
            return 0.0
        epoch = [t.timestamp() for t in valid_ts]
        try:
            std = statistics.stdev(epoch)
        except statistics.StatisticsError:
            return 0.0
        return min(1.0, max(0.0, 1.0 - std / TIMING_STD_THRESHOLD_SECS))

    @staticmethod
    def _empty_result() -> dict:
        return {
            "brigade_detected":      False,
            "confidence":            0.0,
            "signals": {
                "velocity":        0.0,
                "similarity":      0.0,
                "keyword_density": 0.0,
                "timing_cluster":  0.0,
            },
            "triggered_signals":     [],
            "top_keywords":          [],
            "affected_comment_count": 0,
            "recommendation":        "CLEAR — No comments to analyse.",
        }
