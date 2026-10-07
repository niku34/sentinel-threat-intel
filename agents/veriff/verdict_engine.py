"""Verdict engine for Veriff.

Rules (per rebuild spec):
- claim_type == ENTERTAINMENT → verdict = NEEDS_HUMAN_REVIEW, confidence capped 0.60.
- 3+ DBs matched → HIGH_CONFIDENCE verdict.
- 1-2 DBs matched → MEDIUM_CONFIDENCE, flag for review.
- 0 DBs matched → UNVERIFIED (never REAL).
- Deepfake score > 0.7 → add MANIPULATED flag regardless.
- Only NEWS can return AUTO_REAL / AUTO_FAKE at high confidence; other
  hard-news-adjacent categories (SCIENTIFIC, RELIGIOUS) top out at
  MEDIUM_CONFIDENCE and needs_human_review.

Outputs a dict shaped:
    {
      verdict, confidence, needs_human_review,
      flags: [...], matched_source_count,
      match_evidence: [{source, matched, url, headline, score, tier, verdict, ratio}],
      reasoning: [str, ...],
    }
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
import uuid
from typing import Dict, List, Optional

from .semantic_matcher import ClaimEntities, best_match

# Semantic upgrade (Day 13): pgvector cache, Google Vision, InVID.
# All optional — guarded so the verdict pipeline resolves without them.
try:
    from .semantic_cache import SemanticCache
except Exception:  # noqa: BLE001
    SemanticCache = None  # type: ignore
try:
    from .vision_analyzer import VisionAnalyzer
except Exception:  # noqa: BLE001
    VisionAnalyzer = None  # type: ignore
try:
    from .invid_client import InVIDClient
except Exception:  # noqa: BLE001
    InVIDClient = None  # type: ignore

logger = logging.getLogger("veriff.verdict_engine")

ENTERTAINMENT_CONFIDENCE_CAP = 0.60
MANIPULATED_DEEPFAKE_THRESHOLD = 0.70

# Cache reuse threshold — a claim this close to a cached one reuses its verdict.
CACHE_HIT_THRESHOLD = 0.92
CACHE_STORE_MIN_CONFIDENCE = 0.60

_SEMANTIC_CACHE = None
_VISION_ANALYZER = None


def get_semantic_cache():
    """Lazily build a single process-wide SemanticCache (or None)."""
    global _SEMANTIC_CACHE
    if _SEMANTIC_CACHE is not None:
        return _SEMANTIC_CACHE if getattr(_SEMANTIC_CACHE, "available", False) else None
    if SemanticCache is None:
        return None
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        return None
    try:
        _SEMANTIC_CACHE = SemanticCache(db_url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("SemanticCache init failed: %s", exc)
        return None
    return _SEMANTIC_CACHE if _SEMANTIC_CACHE.available else None


def check_semantic_cache(claim_text: str, threshold: float = CACHE_HIT_THRESHOLD) -> Optional[dict]:
    """Return a cached verdict for a near-identical claim, or None."""
    cache = get_semantic_cache()
    if cache is None:
        return None
    try:
        return cache.get_cached(claim_text, threshold)
    except Exception as exc:  # noqa: BLE001
        logger.warning("check_semantic_cache failed: %s", exc)
        return None


def store_semantic_cache(claim_text: str, verdict: str, confidence: float, sources: list) -> None:
    """Persist a verdict to the pgvector cache when confident enough."""
    if confidence is None or float(confidence) <= CACHE_STORE_MIN_CONFIDENCE:
        return
    cache = get_semantic_cache()
    if cache is None:
        return
    try:
        cache.store(claim_text, verdict, float(confidence), sources or [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("store_semantic_cache failed: %s", exc)


def get_vision_analyzer():
    """Lazily build a single VisionAnalyzer (Google Vision or PIL fallback)."""
    global _VISION_ANALYZER
    if _VISION_ANALYZER is None and VisionAnalyzer is not None:
        try:
            _VISION_ANALYZER = VisionAnalyzer()
        except Exception as exc:  # noqa: BLE001
            logger.warning("VisionAnalyzer init failed: %s", exc)
            _VISION_ANALYZER = None
    return _VISION_ANALYZER


def analyze_media_image(image_bytes: bytes) -> Optional[dict]:
    va = get_vision_analyzer()
    if va is None or not image_bytes:
        return None
    try:
        return va.analyze_image(image_bytes)
    except Exception as exc:  # noqa: BLE001
        logger.warning("analyze_media_image failed: %s", exc)
        return None


def search_video(video_url: str) -> List[dict]:
    if InVIDClient is None or not video_url:
        return []
    try:
        return InVIDClient().reverse_video_search(video_url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("search_video failed: %s", exc)
        return []

ARCHIVE_API_URL = "http://localhost:8000/api/archive/fingerprint"
ARCHIVE_MATCH_SIMILARITY = 0.85

# Deepfake spectral analysis thresholds (Ollama Qwen 2.5 14B).
DEEPFAKE_FLAG_PROBABILITY = 0.70
DEEPFAKE_MANIPULATED_PROBABILITY = 0.85


def run_deepfake_analysis(
    claim_text: str,
    media_bytes: Optional[bytes] = None,
    media_type: str = "text",
    source: str = "",
) -> Optional[dict]:
    """Run the Ollama-backed DeepfakeDetector for a DEEPFAKE-type claim.

    Uses real media bytes when available (image/audio), otherwise falls back to
    the text-claim reasoning path. Returns None on any import/runtime failure so
    the verdict still resolves.
    """
    try:
        from agents.deepfake.detector import DeepfakeDetector
    except Exception as exc:  # noqa: BLE001
        logger.warning("DeepfakeDetector import failed: %s", exc)
        return None

    detector = DeepfakeDetector()
    try:
        if media_bytes and media_type == "audio":
            return detector.analyze_audio(media_bytes)
        if media_bytes and media_type in ("image", "video"):
            return detector.analyze_image(media_bytes)
        return detector.analyze_text_claim(claim_text, source=source)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Deepfake analysis failed: %s", exc)
        return None


def check_archive(
    media_bytes: bytes,
    media_type: str = "image",
    api_url: str = ARCHIVE_API_URL,
) -> Optional[dict]:
    """POST media bytes to /api/archive/fingerprint and return its JSON.

    Best-effort: any network/parse failure returns None so the verdict still
    resolves. Uses a hand-built multipart body to avoid extra deps.
    """
    boundary = f"----sentinel{uuid.uuid4().hex}"
    filename = "clip.mp4" if str(media_type).lower().startswith("video") else "clip.png"
    ctype = "video/mp4" if filename.endswith(".mp4") else "image/png"
    pre = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode()
    post = f"\r\n--{boundary}--\r\n".encode()
    body = pre + media_bytes + post
    req = urllib.request.Request(
        api_url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
            return json.loads(resp.read().decode())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Archive check failed: %s", exc)
        return None


def _archive_flags(archive_check: Optional[dict]) -> List[str]:
    """Derive verdict flags from an archive fingerprint result."""
    flags: List[str] = []
    if not archive_check or not archive_check.get("match_found"):
        return flags
    flags.append("ARCHIVE_MATCH")
    matches = archive_check.get("matches") or []
    if any(m.get("fingerprint_type") == "FOOTAGE_THEFT" for m in matches):
        flags.append("FOOTAGE_THEFT")
    return flags

VERDICTS = (
    "AUTO_REAL",
    "AUTO_FAKE",
    "HIGH_CONFIDENCE_FAKE",
    "MEDIUM_CONFIDENCE_FAKE",
    "MANIPULATED",
    "NEEDS_HUMAN_REVIEW",
    "UNVERIFIED",
)

# Alias for the FactCheck DB column (constrained by dashboard code paths).
DB_VERDICT_MAP = {
    "AUTO_REAL": "REAL",
    "AUTO_FAKE": "FAKE",
    "HIGH_CONFIDENCE_FAKE": "FAKE",
    "MEDIUM_CONFIDENCE_FAKE": "FAKE",
    "MANIPULATED": "MANIPULATED",
    "NEEDS_HUMAN_REVIEW": "NEEDS_HUMAN_REVIEW",
    "UNVERIFIED": "UNVERIFIED",
}


def _score_sources(
    entities: ClaimEntities,
    scraper_results: Dict[str, List[dict]],
) -> List[dict]:
    """For each source, run semantic_matcher against its top candidates.

    Returns per-source records with matched flag + best-scoring headline.
    """
    evidence: List[dict] = []
    for source, hits in scraper_results.items():
        if not hits:
            evidence.append({
                "source": source,
                "matched": False,
                "score": 0.0,
                "tier": "no_results",
                "url": None,
                "headline": None,
                "verdict": None,
                "ratio": 0.0,
            })
            continue
        top = best_match(entities, hits)
        if not top or top.get("score", 0.0) <= 0.0:
            evidence.append({
                "source": source,
                "matched": False,
                "score": 0.0,
                "tier": "none",
                "url": hits[0].get("url"),
                "headline": hits[0].get("headline"),
                "verdict": hits[0].get("verdict"),
                "ratio": 0.0,
            })
            continue
        evidence.append({
            "source": source,
            "matched": True,
            "score": top["score"],
            "tier": top["tier"],
            "url": top.get("url"),
            "headline": top.get("headline"),
            "verdict": top.get("verdict"),
            "ratio": top.get("ratio", 0.0),
        })
    return evidence


def _cap(value: float, ceiling: float) -> float:
    return round(min(value, ceiling), 3)


def _matched_count(evidence: List[dict]) -> int:
    return sum(1 for e in evidence if e["matched"])


def _predominant_source_verdict(evidence: List[dict]) -> Optional[str]:
    """Return "FAKE" only when matched sources actually say so.

    "REVIEWED" means "we found and read this article but could not extract its
    conclusion" — it is not evidence of falsity and must never be counted as
    such. A set of purely REVIEWED hits is inconclusive (None), which routes the
    claim to human review.
    """
    verdicts = [
        (e.get("verdict") or "").upper()
        for e in evidence if e["matched"] and e.get("verdict")
    ]
    if not verdicts:
        return None
    fake = [v for v in verdicts if v == "FAKE"]
    contradicting = [v for v in verdicts if v not in {"FAKE", "REVIEWED"}]
    if fake and not contradicting:
        return "FAKE"
    return None


def derive_verdict(
    claim_type: str,
    entities: ClaimEntities,
    scraper_results: Dict[str, List[dict]],
    # None = nothing analysed the media. Distinct from 0.0, which would mean
    # "analysed and found clean" — never infer the latter from the former.
    deepfake_score: Optional[float] = None,
    reverse_search: Optional[dict] = None,
    media_bytes: Optional[bytes] = None,
    media_type: str = "image",
    archive_check: Optional[dict] = None,
    threat_type: str = "",
    claim_text: str = "",
    deepfake_source: str = "",
    deepfake_analysis: Optional[dict] = None,
    video_url: str = "",
    vision_analysis: Optional[dict] = None,
    invid_matches: Optional[list] = None,
    store_cache: bool = True,
) -> dict:
    reasoning: List[str] = []
    flags: List[str] = []

    evidence = _score_sources(entities, scraper_results)

    # ── Google Vision reverse-image analysis (image media) ────
    if vision_analysis is None and media_bytes and media_type == "image":
        vision_analysis = analyze_media_image(media_bytes)
    if vision_analysis:
        web_matches = vision_analysis.get("web_matches") or []
        for wm in web_matches:
            evidence.append({
                "source": f"VisionWeb:{vision_analysis.get('source', 'vision')}",
                "matched": True,
                "score": float(wm.get("score", 0.0) or 0.0),
                "tier": "web_match",
                "url": wm.get("url"),
                "headline": wm.get("title") or "",
                "verdict": "RECYCLED",
                "ratio": float(wm.get("score", 0.0) or 0.0),
            })
        if web_matches:
            # Same image surfacing elsewhere on the web = recycled-media signal.
            flags.append("RECYCLED")
            reasoning.append(
                f"Google Vision reverse-image search found {len(web_matches)} web "
                "match(es) — RECYCLED flag applied"
            )
        for sf in vision_analysis.get("safe_search_flags") or []:
            reasoning.append(f"Vision SafeSearch: {sf}")

    # ── InVID reverse-video search (video media) ──────────────
    if invid_matches is None and media_type == "video" and video_url:
        invid_matches = search_video(video_url)
    if invid_matches:
        for vm in invid_matches:
            evidence.append({
                "source": "InVID",
                "matched": True,
                "score": 0.7,
                "tier": vm.get("match_type", "keyframe"),
                "url": vm.get("source_url"),
                "headline": vm.get("title") or "",
                "verdict": "RECYCLED",
                "ratio": 0.0,
            })
        flags.append("RECYCLED")
        reasoning.append(
            f"InVID reverse-video search found {len(invid_matches)} earlier "
            "appearance(s) — RECYCLED flag applied"
        )
    matched_count = _matched_count(evidence)
    total_sources = len(scraper_results) or 1
    reasoning.append(
        f"{matched_count}/{total_sources} fact-check sources returned a semantic match"
    )

    predominant = _predominant_source_verdict(evidence)

    # Archive fingerprint check — run against the archive when we have media
    # bytes (unless a precomputed result was passed in, e.g. for tests).
    if archive_check is None and media_bytes:
        archive_check = check_archive(media_bytes, media_type)
    for f in _archive_flags(archive_check):
        flags.append(f)
    if archive_check and archive_check.get("match_found"):
        reasoning.append(
            "Archive fingerprint match "
            f"(similarity {archive_check.get('highest_similarity')}) — "
            f"{'FOOTAGE_THEFT' if 'FOOTAGE_THEFT' in flags else 'ARCHIVE_MATCH'} flag applied"
        )

    # Deepfake spectral analysis (Ollama Qwen 2.5 14B) for DEEPFAKE-type claims.
    if deepfake_analysis is None and "DEEPFAKE" in (threat_type or "").upper():
        deepfake_analysis = run_deepfake_analysis(
            claim_text, media_bytes, media_type, source=deepfake_source
        )
    force_manipulated = False
    if deepfake_analysis:
        df_prob = float(deepfake_analysis.get("deepfake_probability", 0.0) or 0.0)
        if df_prob > DEEPFAKE_FLAG_PROBABILITY:
            if "DEEPFAKE" not in flags:
                flags.append("DEEPFAKE")
            reasoning.append(
                f"Deepfake analysis probability {df_prob:.2f} > {DEEPFAKE_FLAG_PROBABILITY} "
                f"({deepfake_analysis.get('analysis_method', 'ollama')}) — DEEPFAKE flag applied"
            )
        if df_prob > DEEPFAKE_MANIPULATED_PROBABILITY:
            force_manipulated = True
            reasoning.append(
                f"Deepfake probability {df_prob:.2f} > {DEEPFAKE_MANIPULATED_PROBABILITY} — "
                "verdict forced to MANIPULATED regardless of DB matches"
            )

    # Deepfake flag applies regardless of DB matches — but only when a real
    # score exists. An unanalysed claim must not be flagged either way.
    if deepfake_score is not None and deepfake_score >= MANIPULATED_DEEPFAKE_THRESHOLD:
        flags.append("MANIPULATED")
        reasoning.append(
            f"Deepfake score {deepfake_score:.2f} >= {MANIPULATED_DEEPFAKE_THRESHOLD} — MANIPULATED flag applied"
        )

    # Entertainment short-circuit.
    if claim_type == "ENTERTAINMENT":
        reasoning.append(
            "Claim classified as ENTERTAINMENT — verdict routed to human review "
            f"(confidence capped at {ENTERTAINMENT_CONFIDENCE_CAP})"
        )
        confidence = _cap(0.35 + 0.05 * matched_count, ENTERTAINMENT_CONFIDENCE_CAP)
        ent_verdict = "NEEDS_HUMAN_REVIEW"
        if force_manipulated:
            ent_verdict = "MANIPULATED"
            confidence = max(confidence, float(deepfake_analysis.get("confidence", 0.85)))
        if store_cache and claim_text:
            store_semantic_cache(claim_text, ent_verdict, confidence, evidence)
        return {
            "verdict": ent_verdict,
            "confidence": confidence,
            "needs_human_review": True,
            "flags": flags + ["ENTERTAINMENT"],
            "matched_source_count": matched_count,
            "match_evidence": evidence,
            "reasoning": reasoning,
            "archive_check": archive_check,
            "deepfake_analysis": deepfake_analysis,
            "vision_analysis": vision_analysis,
            "invid_matches": invid_matches or [],
        }

    # Non-news categories can only reach MEDIUM_CONFIDENCE.
    only_news_can_auto = claim_type == "NEWS"

    if matched_count >= 3:
        # High confidence outcome — direction determined by source verdicts.
        if predominant == "FAKE":
            verdict = "AUTO_FAKE" if only_news_can_auto else "HIGH_CONFIDENCE_FAKE"
            confidence = 0.85 + 0.03 * min(matched_count - 3, 3)
            reasoning.append(
                f"3+ sources match and at least one fact-checker headline states the claim "
                f"is false → {verdict}"
            )
        else:
            # Matched but sources disagree on direction — still needs review.
            verdict = "NEEDS_HUMAN_REVIEW"
            confidence = 0.65
            reasoning.append(
                "3+ sources matched but verdicts split — routed to human review"
            )
        needs_review = verdict == "NEEDS_HUMAN_REVIEW" or not only_news_can_auto
    elif 1 <= matched_count <= 2:
        if predominant == "FAKE":
            verdict = "MEDIUM_CONFIDENCE_FAKE"
            confidence = 0.55 + 0.05 * matched_count
            reasoning.append(
                f"{matched_count} source(s) matched with FAKE verdict — MEDIUM_CONFIDENCE_FAKE, flagged for review"
            )
        else:
            verdict = "NEEDS_HUMAN_REVIEW"
            confidence = 0.50
            reasoning.append("Only partial evidence — needs human review")
        needs_review = True
    else:
        # No matches — never REAL.
        verdict = "UNVERIFIED"
        confidence = 0.30
        needs_review = True
        reasoning.append(
            "No matching fact-check DB entries — UNVERIFIED "
            "(never assume REAL if unverified)"
        )

    # Bonus: if reverse media search says the media is old (RECYCLED-adjacent),
    # push toward review but keep verdict.
    if reverse_search and reverse_search.get("seen_before"):
        flags.append("RECYCLED_MEDIA")
        needs_review = True
        reasoning.append(
            "Reverse media search: media previously seen "
            f"{reverse_search.get('age_days', '?')} days ago"
        )

    if "MANIPULATED" in flags and verdict == "UNVERIFIED":
        confidence = max(confidence, 0.55)
        needs_review = True

    # Deepfake probability > 0.85 forces MANIPULATED regardless of DB matches.
    if force_manipulated:
        verdict = "MANIPULATED"
        needs_review = True
        confidence = max(confidence, float(deepfake_analysis.get("confidence", 0.85)))

    final_confidence = _cap(confidence, 0.98)
    if store_cache and claim_text:
        store_semantic_cache(claim_text, verdict, final_confidence, evidence)

    return {
        "verdict": verdict,
        "confidence": final_confidence,
        "needs_human_review": bool(needs_review),
        "flags": flags,
        "matched_source_count": matched_count,
        "match_evidence": evidence,
        "reasoning": reasoning,
        "archive_check": archive_check,
        "deepfake_analysis": deepfake_analysis,
        "vision_analysis": vision_analysis,
        "invid_matches": invid_matches or [],
    }


if __name__ == "__main__":
    import json
    from .semantic_matcher import extract_entities

    ents = extract_entities("PIB debunked election fraud claim")
    scraper_stub = {
        "PIB": [{"headline": "PIB Fact Check: Election fraud video is old", "verdict": "FAKE", "url": "x"}],
        "AltNews": [{"headline": "Election fraud claim in Bihar debunked", "verdict": "FAKE", "url": "y"}],
        "IndiaToday": [{"headline": "Fact check: 2019 election video peddled as fraud", "verdict": "FAKE", "url": "z"}],
        "Vishvas": [],
        "FactCrescendo": [],
        "QuintWebQoof": [],
        "NewscheckerTamil": [],
    }
    result = derive_verdict("NEWS", ents, scraper_stub, deepfake_score=0.0)
    print(json.dumps(result, ensure_ascii=False, indent=2))
