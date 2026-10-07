"""Veriff fact-check agent — rebuilt pipeline.

Pipeline:
    1. classify_claim(text)   → NEWS / ENTERTAINMENT / SCIENTIFIC / RELIGIOUS
    2. extract_entities(text) → salient tokens for semantic matching
    3. run_all_scrapers(text) → 7 real fact-check sources in parallel
    4. derive_verdict(...)    → rule-based verdict + confidence + review flag
    5. persist FactCheck row  → DB + judge_llm validation (best effort)
"""
from __future__ import annotations

import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

from dotenv import load_dotenv
from sqlalchemy import Column, DateTime, Float, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api.models.channel import Base, engine  # noqa: E402
from agents.veriff.claim_classifier import classify_claim  # noqa: E402
from agents.veriff.scrapers import run_all_scrapers_detailed, run_web_searches  # noqa: E402
from agents.veriff.semantic_cache import SemanticCache  # noqa: E402
from agents.veriff.semantic_matcher import extract_entities  # noqa: E402
from agents.veriff.verdict_engine import (  # noqa: E402
    CACHE_HIT_THRESHOLD,
    DB_VERDICT_MAP,
    check_semantic_cache,
    derive_verdict,
)

try:
    from agents.judge.judge_llm import validate_veriff_verdict  # noqa: E402
except Exception:  # judge is best-effort; do not fail the pipeline if unavailable
    validate_veriff_verdict = None  # type: ignore[assignment]

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("fact_check_agent")

DEEPFAKE_FAKE_THRESHOLD = float(os.getenv("DEEPFAKE_FAKE_THRESHOLD", "0.75"))
DEEPFAKE_MANIPULATED_THRESHOLD = float(
    os.getenv("DEEPFAKE_MANIPULATED_THRESHOLD", "0.45")
)
RECYCLED_AGE_DAYS = int(os.getenv("VERIFF_RECYCLED_AGE_DAYS", "60"))

# Soft time budget for the real (Ollama-backed) deepfake analysis. The detector
# bounds its own Ollama call at ~70s (a cold model load alone can take ~40s),
# and video adds an ffmpeg frame extraction on top — this is a
# belt-and-suspenders ceiling so a stuck call can never hang the verdict. Same
# pattern as threat_intel_agent._run_phase_with_budget. Comfortably under the
# API's 180s subprocess ceiling.
DEEPFAKE_ANALYSIS_BUDGET_SECONDS = float(
    os.getenv("DEEPFAKE_ANALYSIS_BUDGET_SECONDS", "90")
)

VALID_MEDIA_TYPES = {"text", "image", "video", "audio"}
VALID_LANGUAGES = {"tamil", "english", "mixed"}


class FactCheck(Base):
    __tablename__ = "fact_checks"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    claim_text = Column(Text, nullable=False)
    media_type = Column(Text, nullable=False)
    language = Column(Text, nullable=False)
    verdict = Column(Text, nullable=False)
    confidence = Column(Float, nullable=False)
    verdict_time_seconds = Column(Integer, nullable=False)
    database_results = Column(JSONB, nullable=False)
    checked_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    checked_by = Column(Text, nullable=True)
    judge = Column(JSONB, nullable=True)


Base.metadata.create_all(engine, tables=[FactCheck.__table__])


def _detect_language(claim_text: str) -> str:
    """Best-effort language tag: tamil / english / mixed."""
    has_tamil = any("஀" <= ch <= "௿" for ch in claim_text)
    has_ascii = any("a" <= ch.lower() <= "z" for ch in claim_text)
    if has_tamil and has_ascii:
        return "mixed"
    if has_tamil:
        return "tamil"
    return "english"


def _reverse_media_search(claim_text: str, media_type: str) -> dict:
    """Reverse media search is not implemented — report that, never guess.

    This previously derived `seen_before`, `age_days` and an `earliest_source`
    like `archive://video/312d-ago` from a claim-text-seeded RNG. Because the
    seed was the claim text the output was stable across runs, so a fabricated
    "media previously seen 312 days ago" finding looked exactly like a real
    measurement and drove a RECYCLED_MEDIA flag in the verdict engine.
    No reverse-search backend is wired up (see 7c96ce0), so the honest answer
    is "not available".
    """
    if media_type not in {"image", "video"}:
        return {"applicable": False}
    return {"applicable": True, "available": False, "seen_before": None}


def _score_deepfake(
    claim_text: str, media_type: str, media_bytes: Optional[bytes] = None
) -> Optional[float]:
    """Return a real deepfake probability from the Ollama-backed detector, or
    None when nothing analysed the media.

    This previously returned `rng.uniform(0, 1)` for image/video claims. With
    MANIPULATED_DEEPFAKE_THRESHOLD at 0.70 that meant roughly 30% of media
    claims picked up a MANIPULATED flag from a random number, and the dashboard
    rendered it as "⚠ HIGH DEEPFAKE PROBABILITY".

    Now: when the caller actually attached media bytes, run the real detector
    (agents.deepfake.detector, Ollama Qwen2.5-14B-backed) under a soft time
    budget. A text-only claim (media_bytes is None) always returns None — no
    media was ever provided, so there is nothing to analyse and no score to
    report. A real analysis that fails, falls back (Ollama unreachable), or
    times out also returns None rather than a plausible-looking guess — an
    UNCERTAIN 0.5 fallback is not a measurement and must not be presented as
    one.
    """
    if media_bytes is None or media_type not in {"image", "video", "audio"}:
        return None

    try:
        from agents.deepfake.detector import DeepfakeDetector
    except Exception as exc:  # noqa: BLE001
        logger.warning("DeepfakeDetector import failed: %s", exc)
        return None

    detector = DeepfakeDetector()

    def _analyse():
        if media_type == "audio":
            return detector.analyze_audio(media_bytes)
        if media_type == "video":
            return detector.analyze_video(media_bytes)
        return detector.analyze_image(media_bytes)

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        analysis = pool.submit(_analyse).result(timeout=DEEPFAKE_ANALYSIS_BUDGET_SECONDS)
    except FutureTimeoutError:
        logger.warning(
            "Deepfake analysis exceeded its %.0fs budget — no score",
            DEEPFAKE_ANALYSIS_BUDGET_SECONDS,
        )
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Deepfake analysis raised %s — no score", exc)
        return None
    finally:
        pool.shutdown(wait=False)

    if analysis.get("analysis_method") in {"fallback", "unavailable"}:
        # Ollama unreachable / bad response / frame extraction failed — no real
        # signal was produced, so don't fabricate a mid-range score.
        logger.warning(
            "Deepfake analysis produced no real signal (method=%s) — no score",
            analysis.get("analysis_method"),
        )
        return None

    prob = analysis.get("deepfake_probability")
    return float(prob) if prob is not None else None


def _validate_inputs(claim_text: str, media_type: str, language: Optional[str]) -> str:
    if not claim_text or not claim_text.strip():
        raise ValueError("claim_text must be a non-empty string")
    if media_type not in VALID_MEDIA_TYPES:
        raise ValueError(
            f"media_type must be one of {sorted(VALID_MEDIA_TYPES)}, got {media_type!r}"
        )
    if language is None or not str(language).strip():
        return _detect_language(claim_text)
    normalized = str(language).lower()
    if normalized not in VALID_LANGUAGES:
        raise ValueError(
            f"language must be one of {sorted(VALID_LANGUAGES)}, got {language!r}"
        )
    return normalized


def _db_results_dict(evidence: list) -> dict:
    """Legacy per-source Match/No match dict for dashboard back-compat."""
    return {e["source"]: ("Match" if e["matched"] else "No match") for e in evidence}


def _db_results_list(evidence: list) -> list:
    """Shape the response's db_results per the rebuild spec."""
    return [
        {
            "source": e["source"],
            "matched": e["matched"],
            "url": e.get("url"),
            "headline": e.get("headline"),
            "score": e.get("score", 0.0),
            "tier": e.get("tier"),
            "verdict": e.get("verdict"),
        }
        for e in evidence
    ]


def _parse_claim_entities(claim_text: str) -> dict:
    """Regex + keyword entity extraction with best-effort Ollama fallback."""
    import re as _re

    TN_LOCATIONS = {
        "சென்னை", "Chennai", "கோயம்புத்தூர்", "Coimbatore", "மதுரை", "Madurai",
        "திருச்சி", "Trichy", "சேலம்", "Salem", "திருநெல்வேலி", "Tirunelveli",
        "வேலூர்", "Vellore", "ஈரோடு", "Erode", "தூத்துக்குடி", "Thoothukudi",
        "தஞ்சாவூர்", "Thanjavur", "புதுக்கோட்டை", "Pudukkottai",
    }
    EVENTS = {
        "தேர்தல்": "election", "election": "election",
        "போராட்டம்": "protest", "protest": "protest",
        "அறிவிப்பு": "announcement", "announced": "announcement",
        "விபத்து": "accident", "accident": "accident",
        "வெள்ளம்": "flood", "flood": "flood",
        "கைது": "arrest", "arrest": "arrest",
        "ஆர்ப்பாட்டம்": "demonstration", "demonstration": "demonstration",
    }
    years = _re.findall(r'\b(19|20)\d{2}\b', claim_text)
    tamil_dates = _re.findall(r'[஀-௿]+(?:ஆண்டு|மாதம்|நாள்|வருடம்)', claim_text)
    persons = _re.findall(r'\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})*\b', claim_text)
    persons = [p for p in persons if p not in TN_LOCATIONS][:3]
    locations = [loc for loc in TN_LOCATIONS if loc in claim_text]
    events = list(dict.fromkeys(
        eng for tam, eng in EVENTS.items()
        if tam in claim_text or eng.lower() in claim_text.lower()
    ))[:2]
    stopwords_set = {"the", "a", "an", "and", "or", "in", "on", "at", "is", "are", "of", "to", "by"}
    tokens = _re.findall(r'[A-Za-z0-9]+|[஀-௿]+', claim_text)
    keywords = list(dict.fromkeys(
        t for t in tokens if len(t) >= 3 and t.lower() not in stopwords_set
    ))[:8]

    result = {
        "who": ", ".join(persons) if persons else "",
        "what": ", ".join(events) if events else "",
        "when": ", ".join((years + tamil_dates)[:2]),
        "where": ", ".join(locations[:3]) if locations else "",
        "keywords": keywords,
    }

    # Ollama enhancement when regex yields nothing (best-effort, 5s timeout)
    if not any([persons, events, locations]) and len(claim_text) > 20:
        try:
            import json as _json
            import urllib.request as _ureq
            prompt = (
                'Extract entities from this news claim. Return ONLY valid JSON, no extra text: '
                '{"who": "person name or empty string", "what": "event or topic", '
                '"when": "year/date or empty string", "where": "location or empty string"}'
                f'\n\nClaim: {claim_text[:300]}'
            )
            body = _json.dumps({"model": "qwen2.5:14b", "prompt": prompt, "stream": False}).encode()
            req = _ureq.Request(
                "http://localhost:11434/api/generate", data=body,
                headers={"Content-Type": "application/json"},
            )
            with _ureq.urlopen(req, timeout=5) as resp:
                data = _json.loads(resp.read())
                raw_resp = data.get("response", "{}")
                # Extract first JSON object from response
                m = __import__("re").search(r'\{[^{}]+\}', raw_resp)
                parsed = _json.loads(m.group(0)) if m else {}
                for k in ("who", "what", "when", "where"):
                    if parsed.get(k) and not result[k]:
                        result[k] = str(parsed[k])
        except Exception:
            pass

    return result


def _find_related_claims(claim_text: str, threshold: float = 0.75, limit: int = 3) -> list:
    """Search pgvector for past claims similar to this one."""
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        return []
    try:
        cache = SemanticCache(db_url, ensure_schema=False)
        if not cache.available:
            return []
        return cache.get_similar(claim_text, threshold=threshold, limit=limit)
    except Exception as exc:
        logger.warning("related claims search failed: %s", exc)
        return []


def _compute_web_credibility(web_results_flat: list) -> tuple:
    """Weighted average credibility across web sources. Returns (score_0_100, label)."""
    if not web_results_flat:
        return 0.0, "NO WEB PRESENCE"
    total_weight = 0.0
    total_score = 0.0
    for item in web_results_flat:
        cred = item.get("credibility") or {}
        score = float(cred.get("score", 0.5))
        tier = cred.get("tier", "UNKNOWN")
        weight = 2.0 if tier == "HIGH" else 1.0
        total_score += score * weight
        total_weight += weight
    if total_weight == 0:
        return 50.0, "UNKNOWN"
    avg = total_score / total_weight
    score_100 = round(avg * 100, 1)
    if avg >= 0.80:
        label = "CREDIBLE"
    elif avg >= 0.60:
        label = "MIXED"
    elif avg >= 0.30:
        label = "LOW CREDIBILITY"
    else:
        label = "NO WEB PRESENCE"
    return score_100, label


def _cached_response(
    claim_text: str,
    media_type: str,
    language: str,
    classification,
    cached: dict,
    checked_by: Optional[str],
    started: float,
) -> dict:
    """Build + persist a response from a pgvector cache hit (label CACHED)."""
    full_verdict = cached.get("verdict") or "UNVERIFIED"
    db_verdict = DB_VERDICT_MAP.get(full_verdict, full_verdict)
    confidence = float(cached.get("confidence") or 0.0)
    similarity = float(cached.get("similarity") or 0.0)
    sources = cached.get("sources") or []
    verdict_time_seconds = max(1, int(time.monotonic() - started))
    reasoning = [
        f"Semantic cache hit — reused a prior verdict for a near-identical claim "
        f"(cosine similarity {similarity:.4f} ≥ {CACHE_HIT_THRESHOLD})."
    ]
    flags = ["CACHED"]

    db_results_dict = {
        (s.get("source") if isinstance(s, dict) else str(s)): "Match" for s in sources
    }
    result = {
        "id": None,
        "claim_text": claim_text,
        "verdict": full_verdict,
        "db_verdict": db_verdict,
        "confidence": confidence,
        "needs_human_review": bool(cached.get("needs_human_review", confidence < 0.85)),
        "claim_type": classification.claim_type,
        "claim_classification": classification.to_dict(),
        "flags": flags,
        "verdict_time_seconds": verdict_time_seconds,
        "matched_source_count": sum(
            1 for s in sources if isinstance(s, dict) and s.get("matched")
        ),
        "match_evidence": sources,
        "db_results": sources,
        "database_results": db_results_dict,
        # None, not 0.0 — a cache hit carries no media analysis of its own.
        "deepfake_score": None,
        "reverse_search": {"applicable": False},
        "media_type": media_type,
        "language": language,
        "reasoning": reasoning,
        "scrapers_timed_out": [],  # cache hit never runs the scrapers
        "from_cache": True,
        "cache_similarity": round(similarity, 4),
        "vision_analysis": None,
        "invid_matches": [],
        "web_search_results": [],
        "web_sources_checked": 0,
        "entities": _parse_claim_entities(claim_text),
        "related_claims": [],
        "web_credibility_score": 0.0,
        "web_credibility_label": "NO WEB PRESENCE",
        "checked_by": checked_by,
    }

    # Persist the cache-hit as a first-class fact_check row for the audit trail.
    try:
        with Session(engine) as session:
            record = FactCheck(
                claim_text=claim_text,
                media_type=media_type,
                language=language,
                verdict=db_verdict,
                confidence=confidence,
                verdict_time_seconds=verdict_time_seconds,
                database_results={
                    "legacy": db_results_dict,
                    "evidence": sources,
                    "claim_type": classification.claim_type,
                    "flags": flags,
                    "needs_human_review": result["needs_human_review"],
                    "verdict_full": full_verdict,
                    "reasoning": reasoning,
                    "from_cache": True,
                    "cache_similarity": round(similarity, 4),
                },
                checked_at=datetime.now(timezone.utc),
                checked_by=checked_by,
                judge=None,
            )
            session.add(record)
            session.commit()
            result["id"] = str(record.id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to persist cached fact_check: %s", exc)

    return result


def run_fact_check(
    claim_text: str,
    media_type: str = "text",
    language: Optional[str] = None,
    checked_by: Optional[str] = None,
    media_path: Optional[str] = None,
) -> dict:
    resolved_language = _validate_inputs(claim_text, media_type, language)
    logger.info(
        "Fact-check run: media_type=%s language=%s checked_by=%s media=%s",
        media_type,
        resolved_language,
        checked_by,
        bool(media_path),
    )
    started = time.monotonic()

    # Real media bytes for this claim, if the caller attached any (the API
    # writes the upload to a temp file and hands us the path since bytes
    # can't cross the api->agents-venv subprocess boundary as JSON). A
    # text-only claim (media_path is None) never gets a media_bytes value.
    media_bytes: Optional[bytes] = None
    if media_path and media_type != "text":
        try:
            with open(media_path, "rb") as fh:
                media_bytes = fh.read()
        except OSError as exc:
            logger.warning("Failed to read media_path %s: %s", media_path, exc)
            media_bytes = None

    # 1. Classify.
    classification = classify_claim(claim_text)
    logger.info(
        "Classified as %s (conf=%.2f, hits=%s)",
        classification.claim_type,
        classification.confidence,
        classification.scores,
    )

    # 2. Entities.
    entities = extract_entities(claim_text)

    # 2b. Semantic cache — reuse a near-identical claim's verdict (milliseconds
    #     instead of a multi-second seven-source scrape).
    cached = check_semantic_cache(claim_text, CACHE_HIT_THRESHOLD)
    if cached and cached.get("similarity", 0.0) > CACHE_HIT_THRESHOLD:
        logger.info(
            "Semantic cache hit (similarity=%.4f) — reusing verdict %s",
            cached.get("similarity"),
            cached.get("verdict"),
        )
        return _cached_response(
            claim_text=claim_text,
            media_type=media_type,
            language=resolved_language,
            classification=classification,
            cached=cached,
            checked_by=checked_by,
            started=started,
        )

    # 3. Scrapers (parallel). A source or two timing out is a legitimate
    # partial outcome — proceed with whatever completed rather than crashing
    # the whole request (run_all_scrapers_detailed raises only when truly
    # nothing came back).
    scraper_results, scrapers_timed_out = run_all_scrapers_detailed(claim_text)

    # 3b. Web searches — informational only, do not affect verdict score.
    web_results = run_web_searches(claim_text)
    web_results_flat = [item for items in web_results.values() for item in items]

    # 3c. Entity extraction (regex + optional Ollama).
    claim_entities = _parse_claim_entities(claim_text)

    # 3d. Related past claims via pgvector similarity (threshold 0.75, top-3).
    related_claims = _find_related_claims(claim_text)
    if related_claims:
        logger.info("Found %d related past claim(s)", len(related_claims))

    # 3e. Web credibility score.
    web_cred_score, web_cred_label = _compute_web_credibility(web_results_flat)

    # 4. Media checks.
    reverse = _reverse_media_search(claim_text, media_type)
    deepfake_score = _score_deepfake(claim_text, media_type, media_bytes)

    # 5. Verdict.
    verdict_pkt = derive_verdict(
        claim_type=classification.claim_type,
        entities=entities,
        scraper_results=scraper_results,
        deepfake_score=deepfake_score,
        reverse_search=reverse,
        claim_text=claim_text,
        media_type=media_type,
    )
    verdict = verdict_pkt["verdict"]
    confidence = verdict_pkt["confidence"]
    evidence = verdict_pkt["match_evidence"]
    flags = verdict_pkt["flags"]
    needs_review = verdict_pkt["needs_human_review"]
    matched_count = verdict_pkt["matched_source_count"]

    verdict_time_seconds = max(1, int(time.monotonic() - started))
    db_verdict = DB_VERDICT_MAP.get(verdict, verdict)

    db_results_dict = _db_results_dict(evidence)
    db_results_list = _db_results_list(evidence)

    reasoning_list = list(verdict_pkt["reasoning"])
    if scrapers_timed_out:
        responded = len(scraper_results) - len(scrapers_timed_out)
        reasoning_list.append(
            f"{responded}/{len(scraper_results)} fact-check sources responded in "
            f"time — {len(scrapers_timed_out)} timed out and were skipped: "
            + ", ".join(scrapers_timed_out)
        )
    if related_claims:
        reasoning_list.append(
            f"Found {len(related_claims)} similar past claim(s): "
            + "; ".join(
                f"{r['verdict']} (similarity {r['similarity']:.2f})" for r in related_claims
            )
        )

    pre_judge = {
        "verdict": verdict,
        "db_verdict": db_verdict,
        "confidence": confidence,
        "needs_human_review": needs_review,
        "claim_type": classification.claim_type,
        "claim_classification": classification.to_dict(),
        "flags": flags,
        "verdict_time_seconds": verdict_time_seconds,
        "matched_source_count": matched_count,
        "match_evidence": evidence,
        "db_results": db_results_list,
        "database_results": db_results_dict,  # legacy shape
        "deepfake_score": deepfake_score,
        "reverse_search": reverse,
        "media_type": media_type,
        "language": resolved_language,
        "reasoning": reasoning_list,
        "scrapers_timed_out": scrapers_timed_out,
        "from_cache": False,
        "cache_similarity": None,
        "vision_analysis": verdict_pkt.get("vision_analysis"),
        "invid_matches": verdict_pkt.get("invid_matches", []),
        "web_search_results": web_results_flat,
        "web_sources_checked": len(web_results_flat),
        "entities": claim_entities,
        "related_claims": related_claims,
        "web_credibility_score": web_cred_score,
        "web_credibility_label": web_cred_label,
    }

    # 6. Best-effort judge validation.
    judge_payload: dict = {}
    if validate_veriff_verdict is not None:
        try:
            validated = validate_veriff_verdict(pre_judge)
            judge_payload = {k: v for k, v in validated.items() if k.startswith("judge_")}
        except Exception as exc:
            logger.warning("judge_llm validation failed: %s", exc)
            judge_payload = {}

    # 7. Persist.
    with Session(engine) as session:
        record = FactCheck(
            claim_text=claim_text,
            media_type=media_type,
            language=resolved_language,
            verdict=db_verdict,
            confidence=confidence,
            verdict_time_seconds=verdict_time_seconds,
            database_results={
                "legacy": db_results_dict,
                "evidence": db_results_list,
                "claim_type": classification.claim_type,
                "flags": flags,
                "needs_human_review": needs_review,
                "verdict_full": verdict,
                "reasoning": pre_judge["reasoning"],
            },
            checked_at=datetime.now(timezone.utc),
            checked_by=checked_by,
            judge=judge_payload or None,
        )
        session.add(record)
        session.commit()
        record_id = str(record.id)

    logger.info(
        "Verdict: %s (db=%s conf=%.2f flags=%s review=%s) in %ds — %d/7 matched",
        verdict,
        db_verdict,
        confidence,
        flags,
        needs_review,
        verdict_time_seconds,
        matched_count,
    )

    return {
        "id": record_id,
        "claim_text": claim_text,
        **pre_judge,
        "checked_by": checked_by,
        **judge_payload,
    }


if __name__ == "__main__":
    import json
    samples = [
        ("விஜய் திரிஷா மாலத்தீவில் சந்தித்தனர்", "text"),
        ("Chennai protest footage today", "text"),
        ("PIB debunked election fraud claim", "text"),
    ]
    for text, mtype in samples:
        print(f"\n### {text}")
        result = run_fact_check(text, media_type=mtype, checked_by="cli-test")
        print(json.dumps({
            "claim_type": result["claim_type"],
            "verdict": result["verdict"],
            "confidence": result["confidence"],
            "needs_human_review": result["needs_human_review"],
            "flags": result["flags"],
            "matched": result["matched_source_count"],
        }, ensure_ascii=False, indent=2))
