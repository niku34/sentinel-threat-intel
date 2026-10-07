import json
import logging
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("judge_llm")

JUDGE_LLM_MODEL = os.getenv("JUDGE_LLM_MODEL", "qwen2.5:14b")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
JUDGE_TIMEOUT = float(os.getenv("JUDGE_LLM_TIMEOUT", "60"))
SENTINEL_API_BASE_URL = os.getenv("SENTINEL_API_BASE_URL", "http://localhost:8000")

BROADCASTER_CONTEXT = """
BROADCASTER TV NETWORK CONTEXT:
- Organisation: Example Broadcaster Ltd
- Channels monitored: Example Broadcaster, Example News News, Example Entertainment, J Movie Official
- Primary language: Tamil
- Threat CATEGORIES this platform watches for (these are the classes of attack
  the system is designed to detect — they are NOT a record of confirmed
  incidents at Example Broadcaster, and must not be treated as evidence that any of them
  has occurred here):
  * AI voice cloning / deepfake audio of on-air anchors
  * YouTube channel takeover or mass video deletion (an industry risk; the
    September 2025 India TV breach is a reference case at another broadcaster,
    not a Example Broadcaster incident)
  * Coordinated comment brigading
  * Archive footage recirculated with fabricated captions
  * Fake Facebook/Telegram/Instagram pages using Example Broadcaster branding
- Critical threshold: Any threat with reach >5000 users OR anchor impersonation OR YouTube admin changes
- False positive indicators: Known third-party clips, licensed content, official Example Broadcaster social posts
- CERT-In reporting required within 6 hours for: account breaches, data exposure, service disruption
- Common Tamil false positives: அறிவிப்பு (announcement), விளம்பரம் (advertisement), சிறப்பு நிகழ்ச்சி (special program)

COMMON TAMIL FALSE POSITIVES (lower confidence score if these patterns match):
- அறிவிப்பு (arivipu) — routine announcements, NOT threats
- விளம்பரம் (vilambaaram) — advertisements, NOT misinformation
- சிறப்பு நிகழ்ச்சி (sirappu nigazhchi) — special programs, NOT suspicious content
- திரைப்பட விளம்பரம் (thiraipada vilambaaram) — film promotions, NOT fake news
- பிறந்தநாள் வாழ்த்துகள் (pirantha naal vaazhthugal) — birthday wishes, NOT coordinated brigades
- அஞ்சலி (anjali) — condolence posts, NOT propaganda
- நேரலை (neralai) — live broadcast announcements, often flagged incorrectly
If a threat matches these patterns, set confidence below 0.6 and consider FALSE_POSITIVE.
"""


def get_feedback_context() -> str:
    try:
        with httpx.Client(timeout=5) as client:
            resp = client.get(f"{SENTINEL_API_BASE_URL}/api/judge/feedback-summary")
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return ""

    confirmed = data.get("total_confirmed", 0)
    dismissed = data.get("total_dismissed", 0)
    recent = data.get("recent_dismissals", [])
    recent_str = ", ".join(f'"{r}"' for r in recent) if recent else "None yet"

    # The endpoint returns null for the rate until the sample is large enough.
    # Pass that through honestly rather than telling the model "0.0%", which it
    # would read as "this system has never produced a false positive".
    raw_rate = data.get("false_positive_rate")
    if raw_rate is None:
        rate_line = (
            f"False positive rate: not yet measurable "
            f"({data.get('sample_size', confirmed + dismissed)} editorial "
            f"decision(s) recorded — too few to compute a rate)\n"
        )
    else:
        rate_line = f"False positive rate: {round(raw_rate * 100, 1)}%\n"

    return (
        "EDITORIAL FEEDBACK FROM PREVIOUS SCANS:\n"
        f"Confirmed true positives: {confirmed} | Dismissed as false positives: {dismissed}\n"
        + rate_line
        + f"Recently dismissed patterns: {recent_str}\n"
    )


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        first_nl = stripped.find("\n")
        if first_nl != -1:
            stripped = stripped[first_nl + 1:]
    if stripped.endswith("```"):
        stripped = stripped[:-3]
    stripped = stripped.strip()
    # strip any prose before the first { and after the last }
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end >= start:
        stripped = stripped[start:end + 1]
    return stripped


def _call_ollama(prompt: str) -> dict | None:
    payload = {
        "model": JUDGE_LLM_MODEL,
        "prompt": prompt,
        "stream": False,
    }
    try:
        with httpx.Client(timeout=JUDGE_TIMEOUT) as client:
            response = client.post(
                f"{OLLAMA_BASE_URL}/api/generate", json=payload
            )
            response.raise_for_status()
            raw = response.json().get("response", "")
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Ollama call failed: %s", exc)
        return None

    cleaned = _strip_fences(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.warning("Judge LLM returned non-JSON: %s — %s", exc, cleaned[:200])
        return None


def _fail_open(extra: dict) -> dict:
    """Judge unavailable: keep the finding, but do not claim it was validated.

    This previously returned judge_confidence=1.0, which asserted maximum
    confidence in a review that never happened and fed that 1.0 into the
    confidence trend on the accuracy dashboard. Confidence is now null so
    unvalidated findings are excluded from confidence metrics rather than
    inflating them.
    """
    base = {
        "judge_confirmed": True,
        "judge_confidence": None,
        "judge_reasoning": "NOT VALIDATED — Judge LLM unavailable; original result kept unreviewed",
    }
    base.update(extra)
    return base


def validate_kaaval_threat(threat: dict) -> dict:
    feedback = get_feedback_context()
    prompt = (
        BROADCASTER_CONTEXT
        + (f"\n{feedback}" if feedback else "")
        + "\nYou are the Judge LLM for Sentinel — Example Broadcaster's AI security platform.\n"
        + "Review this Kaaval AI threat against the threat categories above.\n"
        + f"Threat: {json.dumps(threat, ensure_ascii=False, indent=2)}\n"
        + "Return ONLY this JSON with values filled in, no other text:\n"
        + '{"confirmed": true, "confidence": 0.95, "reasoning": "your reasoning here max 80 words", '
        + '"adjusted_severity": "CRITICAL", "brand_main_relevance": "relevance here"}'
    )
    result = {**threat}
    parsed = _call_ollama(prompt)
    if parsed is None:
        result.update(
            _fail_open(
                {
                    "adjusted_severity": threat.get("severity"),
                    "judge_brand_main_relevance": "n/a",
                }
            )
        )
    else:
        result["judge_confirmed"] = bool(parsed.get("confirmed", True))
        result["judge_confidence"] = float(parsed.get("confidence", 0.0))
        result["judge_reasoning"] = parsed.get("reasoning", "")
        result["adjusted_severity"] = parsed.get(
            "adjusted_severity", threat.get("severity")
        )
        result["judge_brand_main_relevance"] = parsed.get("brand_main_relevance", "")
    logger.info(
        "Judge LLM validate_kaaval_threat: confirmed=%s confidence=%s",
        result["judge_confirmed"],
        result["judge_confidence"],
    )
    return result


def validate_kavasam_anomaly(anomaly: dict) -> dict:
    feedback = get_feedback_context()
    prompt = (
        BROADCASTER_CONTEXT
        + (f"\n{feedback}" if feedback else "")
        + "\nYou are the Judge LLM reviewing a YouTube channel anomaly on Example Broadcaster.\n"
        + "Judge the anomaly on its own evidence. Do not assume a breach because\n"
        + "channel takeovers have happened at other broadcasters.\n"
        + f"Anomaly: {json.dumps(anomaly, ensure_ascii=False, indent=2)}\n"
        + "Return ONLY this JSON with values filled in, no other text:\n"
        + '{"confirmed": true, "confidence": 0.95, "reasoning": "your reasoning here max 80 words", '
        + '"escalate_to_certin": false, "certin_reason": "reason here", "urgency": "HIGH"}'
    )
    result = {**anomaly}
    parsed = _call_ollama(prompt)
    if parsed is None:
        result.update(
            _fail_open(
                {
                    "judge_escalate_to_certin": False,
                    "judge_certin_reason": "n/a",
                    "judge_urgency": "MONITOR",
                }
            )
        )
    else:
        result["judge_confirmed"] = bool(parsed.get("confirmed", True))
        result["judge_confidence"] = float(parsed.get("confidence", 0.0))
        result["judge_reasoning"] = parsed.get("reasoning", "")
        result["judge_escalate_to_certin"] = bool(
            parsed.get("escalate_to_certin", False)
        )
        result["judge_certin_reason"] = parsed.get("certin_reason", "")
        result["judge_urgency"] = parsed.get("urgency", "MONITOR")
    logger.info(
        "Judge LLM validate_kavasam_anomaly: confirmed=%s confidence=%s",
        result["judge_confirmed"],
        result["judge_confidence"],
    )
    return result


def validate_veriff_verdict(fact_check: dict) -> dict:
    feedback = get_feedback_context()
    prompt = (
        BROADCASTER_CONTEXT
        + (f"\n{feedback}" if feedback else "")
        + "\nYou are the Judge LLM validating a fact-check for Example News journalists.\n"
        + "Wrong verdict = defamation lawsuit or MIB notice for Example Broadcaster.\n"
        + f"Result: {json.dumps(fact_check, ensure_ascii=False, indent=2)}\n"
        + "Return ONLY this JSON with values filled in, no other text:\n"
        + '{"confirmed": true, "confidence": 0.95, "reasoning": "your reasoning here max 80 words", '
        + '"final_verdict": "REAL", "broadcast_recommendation": "CLEAR", "legal_risk": "LOW"}'
    )
    result = {**fact_check}
    parsed = _call_ollama(prompt)
    if parsed is None:
        result.update(
            _fail_open(
                {
                    "judge_final_verdict": fact_check.get("verdict"),
                    "judge_broadcast_recommendation": "INVESTIGATE",
                    "judge_legal_risk": "MEDIUM",
                }
            )
        )
    else:
        result["judge_confirmed"] = bool(parsed.get("confirmed", True))
        result["judge_confidence"] = float(parsed.get("confidence", 0.0))
        result["judge_reasoning"] = parsed.get("reasoning", "")
        result["judge_final_verdict"] = parsed.get(
            "final_verdict", fact_check.get("verdict")
        )
        result["judge_broadcast_recommendation"] = parsed.get(
            "broadcast_recommendation", "INVESTIGATE"
        )
        result["judge_legal_risk"] = parsed.get("legal_risk", "MEDIUM")
    logger.info(
        "Judge LLM validate_veriff_verdict: confirmed=%s confidence=%s",
        result["judge_confirmed"],
        result["judge_confidence"],
    )
    return result
