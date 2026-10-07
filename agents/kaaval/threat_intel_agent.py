import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from dotenv import load_dotenv
from sqlalchemy import Column, DateTime, ForeignKey, Text, create_engine, select
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api.models.channel import Base, Channel, engine  # noqa: E402
from agents.judge.judge_llm import validate_kaaval_threat  # noqa: E402
from agents.core.plugin_check import is_plugin_enabled  # noqa: E402
from agents.core.config import get_youtube_api_key  # noqa: E402

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("threat_intel_agent")

YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_COMMENT_THREADS_URL = "https://www.googleapis.com/youtube/v3/commentThreads"
YOUTUBE_COMMENT_FETCH_MAX = int(os.getenv("YOUTUBE_COMMENT_FETCH_MAX", "100"))
BRIGADE_MIN_UNIQUE_COMMENTERS = int(os.getenv("BRIGADE_MIN_UNIQUE_COMMENTERS", "20"))
BRIGADE_WINDOW_MINUTES = int(os.getenv("BRIGADE_WINDOW_MINUTES", "10"))
YOUTUBE_API_TIMEOUT = float(os.getenv("YOUTUBE_API_TIMEOUT", "10"))

THREAT_TYPES = ("FAKE_PAGE", "BRIGADE", "ARCHIVE_MISUSE", "DEEPFAKE", "IMPERSONATION")
SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
STATUS_OPEN = "OPEN"


class Threat(Base):
    __tablename__ = "threats"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    channel_id = Column(
        PG_UUID(as_uuid=True), ForeignKey("channels.id"), nullable=True
    )
    threat_type = Column(Text, nullable=False)
    severity = Column(Text, nullable=False)
    title = Column(Text, nullable=False)
    description = Column(Text, nullable=False)
    source_platform = Column(Text, nullable=False)
    source_url = Column(Text)
    detected_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    status = Column(Text, nullable=False, default=STATUS_OPEN)
    judge = Column(JSONB, nullable=True)


Base.metadata.create_all(engine, tables=[Threat.__table__])


def _persist_threat(
    session: Session,
    channel_pk: UUID | None,
    payload: dict,
    judge: dict | None = None,
) -> Threat:
    threat = Threat(
        channel_id=channel_pk,
        threat_type=payload["threat_type"],
        severity=payload["severity"],
        title=payload["title"],
        description=payload["description"],
        source_platform=payload["source_platform"],
        source_url=payload["source_url"],
        detected_at=datetime.now(timezone.utc),
        status=STATUS_OPEN,
        judge=judge,
    )
    session.add(threat)
    return threat


# Real-media deepfake threat types (distinct from the synthetic DEEPFAKE bucket).
DEEPFAKE_THREAT_TYPES = ("DEEPFAKE_AUDIO", "DEEPFAKE_VIDEO")


def analyze_media_for_deepfake_threat(
    channel: Channel | None,
    media_bytes: bytes,
    media_type: str,
    source_url: str = "",
    source_platform: str = "",
    persist: bool = True,
) -> dict | None:
    """Run the Ollama-backed DeepfakeDetector over real media during a scan.

    If the detector returns LIKELY_FAKE, build (and optionally persist) a threat
    with threat_type DEEPFAKE_AUDIO / DEEPFAKE_VIDEO and the detected indicators
    embedded in the description. Returns the analysis dict (with `threat` key when
    a threat was created), or None if the detector could not run.
    """
    try:
        from agents.deepfake.detector import DeepfakeDetector
    except Exception as exc:  # noqa: BLE001
        logger.warning("DeepfakeDetector unavailable for Kaaval scan: %s", exc)
        return None

    detector = DeepfakeDetector()
    kind = (media_type or "").lower()
    try:
        analysis = (
            detector.analyze_audio(media_bytes)
            if kind == "audio"
            else detector.analyze_image(media_bytes)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Deepfake media analysis failed: %s", exc)
        return None

    if analysis.get("verdict") != "LIKELY_FAKE":
        logger.info(
            "Kaaval deepfake scan: %s (prob=%.2f) — no threat raised",
            analysis.get("verdict"),
            analysis.get("deepfake_probability", 0.0),
        )
        return analysis

    threat_type = "DEEPFAKE_AUDIO" if kind == "audio" else "DEEPFAKE_VIDEO"
    channel_name = channel.name if channel else "Broadcaster Network"
    indicators = analysis.get("indicators") or []
    indicator_text = "; ".join(indicators) if indicators else "spectral anomalies detected"
    prob = analysis.get("deepfake_probability", 0.0)
    modality = "audio clip" if kind == "audio" else "video"

    payload = {
        "threat_type": threat_type,
        "severity": "CRITICAL" if prob >= 0.85 else "HIGH",
        "title": f"Deepfake {modality} impersonating {channel_name} anchor",
        "description": (
            f"Ollama Qwen 2.5 14B spectral analysis flagged this {modality} as "
            f"LIKELY_FAKE (deepfake probability {prob:.2f}, confidence "
            f"{analysis.get('confidence', 0.0):.2f}). Indicators: {indicator_text}."
        ),
        "source_platform": source_platform or "unknown",
        "source_url": source_url or "",
    }
    analysis["threat"] = payload

    if persist:
        channel_pk = channel.id if channel else None
        with Session(engine) as session:
            _persist_threat(session, channel_pk, payload)
            session.commit()
        logger.info("Kaaval raised %s threat (prob=%.2f)", threat_type, prob)

    return analysis


def _judge_payload(validated: dict) -> dict:
    return {k: v for k, v in validated.items() if k.startswith("judge_") or k == "adjusted_severity"}


def _fetch_latest_video(client: httpx.Client, channel_id: str, api_key: str) -> dict | None:
    params = {
        "part": "snippet",
        "channelId": channel_id,
        "maxResults": 1,
        "order": "date",
        "type": "video",
        "key": api_key,
    }
    try:
        response = client.get(YOUTUBE_SEARCH_URL, params=params)
        response.raise_for_status()
        items = response.json().get("items", [])
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "YouTube search failed for channel %s: %s", channel_id, exc
        )
        return None

    if not items:
        return None

    item = items[0]
    video_id = item.get("id", {}).get("videoId")
    snippet = item.get("snippet", {})
    if not video_id:
        return None
    return {
        "video_id": video_id,
        "title": snippet.get("title"),
        "url": f"https://www.youtube.com/watch?v={video_id}",
    }


def _fetch_comment_threads(client: httpx.Client, video_id: str, api_key: str) -> list[dict] | None:
    params = {
        "part": "snippet",
        "videoId": video_id,
        "maxResults": YOUTUBE_COMMENT_FETCH_MAX,
        "order": "time",
        "key": api_key,
    }
    try:
        response = client.get(YOUTUBE_COMMENT_THREADS_URL, params=params)
        response.raise_for_status()
        return response.json().get("items", [])
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "YouTube commentThreads failed for video %s: %s", video_id, exc
        )
        return None


def _analyze_brigade(comment_items: list[dict]) -> dict | None:
    window = timedelta(minutes=BRIGADE_WINDOW_MINUTES)
    parsed: list[tuple[datetime, str]] = []
    for item in comment_items:
        top = (
            item.get("snippet", {})
            .get("topLevelComment", {})
            .get("snippet", {})
        )
        published = top.get("publishedAt")
        author = top.get("authorChannelId", {}).get("value") or top.get(
            "authorDisplayName"
        )
        if not published or not author:
            continue
        try:
            ts = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except ValueError:
            continue
        parsed.append((ts, author))

    if len(parsed) < BRIGADE_MIN_UNIQUE_COMMENTERS:
        return None

    parsed.sort(key=lambda row: row[0])

    best: dict | None = None
    left = 0
    for right in range(len(parsed)):
        while parsed[right][0] - parsed[left][0] > window:
            left += 1
        window_slice = parsed[left : right + 1]
        unique_authors = {author for _, author in window_slice}
        if len(unique_authors) >= BRIGADE_MIN_UNIQUE_COMMENTERS:
            span_minutes = (
                (window_slice[-1][0] - window_slice[0][0]).total_seconds() / 60
            )
            candidate = {
                "comment_count": len(window_slice),
                "unique_accounts": len(unique_authors),
                "window_minutes": max(span_minutes, 0.1),
                "first_comment_at": window_slice[0][0],
                "last_comment_at": window_slice[-1][0],
            }
            if best is None or candidate["unique_accounts"] > best["unique_accounts"]:
                best = candidate

    return best


def detect_youtube_brigades(api_key: str | None = None) -> list[dict]:
    api_key = api_key or get_youtube_api_key()
    if not api_key:
        logger.warning(
            "No YouTube API key in platform_config or YOUTUBE_API_KEY env — "
            "skipping real brigade detection"
        )
        return []

    detected: list[dict] = []
    with Session(engine) as session:
        channels = (
            session.execute(
                select(Channel).where(Channel.platform == "youtube")
            )
            .scalars()
            .all()
        )
        logger.info(
            "Running real brigade detection across %d YouTube channels",
            len(channels),
        )

        with httpx.Client(timeout=YOUTUBE_API_TIMEOUT) as client:
            for channel in channels:
                channel_started = time.monotonic()
                try:
                    latest = _fetch_latest_video(client, channel.channel_id, api_key)
                    if not latest:
                        logger.info(
                            "No latest video found for %s (%s)",
                            channel.name,
                            channel.channel_id,
                        )
                        continue

                    comments = _fetch_comment_threads(client, latest["video_id"], api_key)
                    if comments is None:
                        continue

                    brigade = _analyze_brigade(comments)
                    if not brigade:
                        logger.info(
                            "No brigade pattern on %s — latest video '%s'",
                            channel.name,
                            latest["title"],
                        )
                        continue
                finally:
                    logger.info(
                        "TIMING: brigade channel %s took %.1fs",
                        channel.name,
                        time.monotonic() - channel_started,
                    )

                payload = {
                    "threat_type": "BRIGADE",
                    "severity": "HIGH",
                    "title": f"Coordinated comment brigade on {channel.name}",
                    "description": (
                        f"{brigade['comment_count']} comments from "
                        f"{brigade['unique_accounts']} accounts in "
                        f"{brigade['window_minutes']:.1f} minutes on latest "
                        f"video '{latest['title']}'."
                    ),
                    "source_platform": "youtube",
                    "source_url": latest["url"],
                }
                validated = validate_kaaval_threat(payload)
                judge_payload = _judge_payload(validated)
                threat = _persist_threat(
                    session, channel.id, payload, judge=judge_payload
                )
                session.flush()
                logger.warning(
                    "Brigade confirmed on %s: %d accounts / %.1f min",
                    channel.name,
                    brigade["unique_accounts"],
                    brigade["window_minutes"],
                )
                detected.append(
                    {
                        "id": str(threat.id),
                        "channel_id": str(channel.id),
                        "channel_name": channel.name,
                        "threat_type": payload["threat_type"],
                        "severity": payload["severity"],
                        "title": payload["title"],
                        "description": payload["description"],
                        "source_platform": payload["source_platform"],
                        "source_url": payload["source_url"],
                        "detected_at": threat.detected_at.isoformat()
                        if threat.detected_at
                        else datetime.now(timezone.utc).isoformat(),
                        "status": threat.status,
                        **judge_payload,
                    }
                )

        session.commit()

    logger.info("Real brigade detection complete: %d brigades", len(detected))
    return detected


def run_fake_page_scan(brands: list[str] | None = None) -> dict | None:
    """Run the live FakePageDetector as part of a Kaaval scan.

    Degrades to None if the detector cannot be imported or the scan errors, so a
    network hiccup never brings down the wider threat sweep.
    """
    try:
        from agents.kaaval.fake_page_detector import FakePageDetector
    except Exception as exc:  # noqa: BLE001
        logger.warning("FakePageDetector unavailable for Kaaval scan: %s", exc)
        return None
    try:
        return FakePageDetector().run_full_scan(brands)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Fake-page scan failed: %s", exc)
        return None


def run_meta_scan() -> dict | None:
    """Run the authenticated Meta (Facebook + Instagram) scan in the pipeline.

    Only runs when Meta credentials are configured in the Admin Console. When not
    configured it logs guidance and returns without touching anything. Findings
    are persisted as status='detected' threats — HUMAN-IN-THE-LOOP, never
    auto-filed or auto-struck on Meta.
    """
    try:
        from agents.kaaval.fake_page_detector import scan_meta
    except Exception as exc:  # noqa: BLE001
        logger.warning("Meta scan unavailable: %s", exc)
        return None
    try:
        result = scan_meta()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Meta scan failed: %s", exc)
        return None
    if result.get("status") == "not_configured":
        logger.info(
            "Meta integration not configured — enter credentials in Admin Console"
        )
        return result
    logger.info(
        "Meta scan: %d fake pages, %d brigades, %d IG misuse, %d threats (score=%d)",
        result.get("fake_pages_found", 0),
        result.get("brigades_detected", 0),
        result.get("ig_misuse_found", 0),
        result.get("threats_created", 0),
        result.get("meta_threat_score", 0),
    )
    return result


def run_voice_monitoring_sweep() -> dict | None:
    """Run the anchor-voice monitoring sweep as part of the Kaaval pipeline.

    Delegates to agents.voice.monitor.VoiceMonitor, which downloads audio from
    watched Telegram channels, fingerprints it, compares against enrolled anchor
    profiles, and persists DEEPFAKE_AUDIO threats (with anchor_name + similarity
    in the description) for any impersonation match or clone-risk hit.
    """
    try:
        from agents.voice.monitor import VoiceMonitor
    except Exception as exc:  # noqa: BLE001
        logger.warning("VoiceMonitor unavailable: %s", exc)
        return None
    try:
        result = VoiceMonitor().run_monitoring_sweep()
        logger.info(
            "Voice sweep: %d channels, %d clips, %d threats",
            result.get("channels_scanned", 0),
            result.get("clips_checked", 0),
            result.get("threats_found", 0),
        )
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("Voice monitoring sweep failed: %s", exc)
        return None


# Soft per-phase time budget inside scan_threats(). scan_threats() itself runs
# under the API's 120s subprocess ceiling with no internal step budget, so a
# single slow phase (a stuck YouTube API call, a slow Telegram host, a hung
# Meta Graph API request) could previously consume the whole 120s alone and
# starve every phase after it. Four phases x this budget must stay comfortably
# under the outer ceiling.
_PHASE_BUDGET_SECONDS = 25.0


def _run_phase_with_budget(fn, label: str, budget_seconds: float = _PHASE_BUDGET_SECONDS):
    """Run `fn` with a soft timeout; on overrun, log and move on with no result.

    The phase's worker thread is not forcibly killed (Python threads can't be),
    so it keeps running to completion in the background and is abandoned —
    the same tradeoff already used for DNS lookups in fake_page_detector's
    `_resolves()`. This bounds scan_threats()'s own wall time even though the
    orphaned call may still be doing I/O after we've moved on.
    """
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(fn)
    try:
        return future.result(timeout=budget_seconds)
    except FutureTimeoutError:
        logger.warning(
            "TIMING: %s exceeded its %.0fs budget — skipping remaining work in "
            "this phase and moving on",
            label, budget_seconds,
        )
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s raised %s — treating as no result", label, exc)
        return None
    finally:
        pool.shutdown(wait=False)


def scan_threats() -> dict:
    scan_started = time.monotonic()
    logger.info("Starting Kaaval AI threat intel scan")
    threats_out: list[dict] = []
    by_severity: dict[str, int] = {s: 0 for s in SEVERITIES}
    by_type: dict[str, int] = {t: 0 for t in THREAT_TYPES}

    youtube_api_key = get_youtube_api_key()

    # Brigade detection needs live YouTube comment data. Without an API key it is
    # skipped outright — there is deliberately no demo/sample fallback, because a
    # fabricated finding is indistinguishable from a real one once persisted to
    # the threats table.
    step_started = time.monotonic()
    if not is_plugin_enabled("Social Listening"):
        logger.info("Plugin disabled — skipping Social Listening (brigade detection)")
    elif not youtube_api_key:
        logger.warning(
            "Brigade detection skipped — YouTube API key not configured. "
            "No brigade findings will be produced for this scan cycle."
        )
    else:
        real_brigades = _run_phase_with_budget(
            lambda: detect_youtube_brigades(youtube_api_key), "brigade detection"
        ) or []
        for threat in real_brigades:
            by_severity[threat["severity"]] += 1
            by_type[threat["threat_type"]] += 1
            threats_out.append(threat)
    logger.info(
        "TIMING: brigade detection step took %.1fs", time.monotonic() - step_started
    )

    # Live fake-page / impersonation reconnaissance (Google, Facebook, Telegram,
    # lookalike DNS). Persists its own FAKE_PAGE threats; count them here.
    step_started = time.monotonic()
    if not is_plugin_enabled("Video Forensics"):
        logger.info("Plugin disabled — skipping Video Forensics (fake page scan)")
        fake_pages = None
    else:
        fake_pages = _run_phase_with_budget(run_fake_page_scan, "fake-page scan")
    logger.info(
        "TIMING: fake-page scan step took %.1fs", time.monotonic() - step_started
    )
    if fake_pages:
        by_type["FAKE_PAGE"] += fake_pages.get("threats_created", 0)
        logger.info(
            "Fake-page recon raised %d threats (google=%d fb=%d telegram=%d "
            "domains=%d)",
            fake_pages.get("threats_created", 0),
            len(fake_pages.get("google_results", [])),
            len(fake_pages.get("facebook_results", [])),
            len(fake_pages.get("telegram_results", [])),
            len(fake_pages.get("domain_results", [])),
        )

    # Authenticated Meta (Facebook + Instagram) scan — persists status='detected'
    # threats (human-in-the-loop). No-op unless credentials are configured.
    step_started = time.monotonic()
    meta_scan = _run_phase_with_budget(run_meta_scan, "meta scan")
    logger.info(
        "TIMING: meta scan step took %.1fs", time.monotonic() - step_started
    )
    if meta_scan and meta_scan.get("threats_created"):
        by_type["FAKE_PAGE"] = by_type.get("FAKE_PAGE", 0) + meta_scan.get("fake_pages_found", 0)
        by_type["BRIGADE"] = by_type.get("BRIGADE", 0) + meta_scan.get("brigades_detected", 0)
        by_type["INSTAGRAM_MISUSE"] = (
            by_type.get("INSTAGRAM_MISUSE", 0) + meta_scan.get("ig_misuse_found", 0)
        )

    # Anchor voice monitoring sweep — persists its own DEEPFAKE_AUDIO threats.
    step_started = time.monotonic()
    if not is_plugin_enabled("Tamil NLP"):
        logger.info("Plugin disabled — skipping Tamil NLP (voice monitoring sweep)")
        voice_sweep = None
    else:
        voice_sweep = _run_phase_with_budget(run_voice_monitoring_sweep, "voice monitoring sweep")
    logger.info(
        "TIMING: voice monitoring sweep step took %.1fs", time.monotonic() - step_started
    )
    if voice_sweep and voice_sweep.get("threats_found"):
        by_type["DEEPFAKE_AUDIO"] = (
            by_type.get("DEEPFAKE_AUDIO", 0) + voice_sweep["threats_found"]
        )

    summary = {
        "total_threats": len(threats_out),
        "by_severity": by_severity,
        "by_type": by_type,
        "threats": threats_out,
    }
    logger.info(
        "TIMING: scan_threats() total wall time %.1fs", time.monotonic() - scan_started
    )
    logger.info(
        "Scan complete: %d threats (%s)",
        summary["total_threats"],
        ", ".join(f"{k}:{v}" for k, v in by_severity.items() if v),
    )
    return summary


if __name__ == "__main__":
    summary = scan_threats()
    logger.info(
        "Final summary: %d threats — by_severity=%s by_type=%s",
        summary["total_threats"],
        summary["by_severity"],
        summary["by_type"],
    )
