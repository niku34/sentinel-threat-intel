import ipaddress
import json
import socket
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import requests
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import Column, DateTime, ForeignKey, Text, func, select, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Session

from core.limiter import limiter
from models.channel import Base, Channel, engine

# scan_threats lives in agents/.venv (Python 3.11 + crewai).
# We cross the venv boundary via subprocess rather than a direct import,
# since crewai depends on tiktoken which cannot build on Python 3.14.
_AGENTS_PYTHON = Path(__file__).resolve().parents[2] / "agents" / ".venv" / "bin" / "python"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

router = APIRouter(prefix="/api/kaaval", tags=["kaaval"])


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
    status = Column(Text, nullable=False, default="OPEN")
    judge = Column(JSONB, nullable=True)


def _run_agents_script(script: str, timeout: int = 120, input_data: str | None = None) -> dict:
    """Execute a snippet inside agents/.venv (Python 3.11) and parse its JSON.

    crewai / bs4 / sqlalchemy live in that venv, so agent code is invoked across
    the venv boundary via subprocess rather than imported into the API venv.
    Pass input_data to send JSON via stdin (used for brigade analysis payloads).
    """
    result = subprocess.run(
        [str(_AGENTS_PYTHON), "-c", script],
        cwd=str(_PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        input=input_data,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    # The agent modules log to stdout; the JSON payload is the last line.
    stdout = result.stdout.strip()
    last_line = stdout.splitlines()[-1] if stdout else ""
    return json.loads(last_line)


# ── Pydantic models ───────────────────────────────────────

class CommentIn(BaseModel):
    author: str
    text: str
    timestamp: str
    likes: int = 0


class BrigadeAnalyzeRequest(BaseModel):
    video_id: str
    comments: list[CommentIn]


def _run_brigade_analysis(video_id: str, comments: list[dict]) -> dict:
    script = (
        "import sys, json; sys.path.insert(0, '.'); "
        "payload = json.loads(sys.stdin.read()); "
        "from agents.kaaval.brigade_detector import BrigadeDetector; "
        "d = BrigadeDetector(); "
        "result = d.analyze_youtube_comments(payload['video_id'], payload['comments']); "
        "print(json.dumps(result))"
    )
    return _run_agents_script(
        script,
        input_data=json.dumps({"video_id": video_id, "comments": comments}),
    )


def _run_scan() -> dict:
    return _run_agents_script(
        "import sys; sys.path.insert(0, '.'); "
        "from agents.kaaval.threat_intel_agent import scan_threats; "
        "import json; print(json.dumps(scan_threats()))"
    )


def _run_fake_page_scan() -> dict:
    return _run_agents_script(
        "import sys; sys.path.insert(0, '.'); "
        "from agents.kaaval.fake_page_detector import run_full_scan; "
        "import json; print(json.dumps(run_full_scan()))"
    )


# ── GET /api/kaaval/threats ───────────────────────────────
@router.get("/threats")
def list_threats():
    with Session(engine) as session:
        rows = session.execute(
            select(Threat, Channel)
            .join(Channel, Threat.channel_id == Channel.id, isouter=True)
            .order_by(Threat.detected_at.desc())
            .limit(50)
        ).all()

        result = [
            {
                "id": str(t.id),
                "channel_id": str(t.channel_id) if t.channel_id else None,
                "channel_name": ch.name if ch else None,
                "threat_type": t.threat_type,
                "severity": t.severity,
                "title": t.title,
                "description": t.description,
                "source_platform": t.source_platform,
                "source_url": t.source_url,
                "detected_at": t.detected_at.isoformat() if t.detected_at else None,
                "status": t.status,
            }
            for t, ch in rows
        ]

        return {"threats": result, "total": len(result)}


# ── GET /api/kaaval/threats-feed ─────────────────────────
@router.get("/threats-feed")
def threats_feed(days: int = 7):
    """Return threats detected within the last `days` days, newest-first."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    with Session(engine) as session:
        rows = session.execute(
            select(Threat)
            .where(Threat.detected_at >= since)
            .order_by(Threat.detected_at.desc())
            .limit(50)
        ).scalars().all()
        result = [
            {
                "id": str(t.id),
                "threat_type": t.threat_type,
                "severity": t.severity,
                "title": t.title,
                "description": t.description,
                "source_platform": t.source_platform,
                "detected_at": t.detected_at.isoformat() if t.detected_at else None,
                "status": t.status,
            }
            for t in rows
        ]
        return {"threats": result, "total": len(result), "period_days": days}


# ── POST /api/kaaval/scan ─────────────────────────────────
@router.post("/scan", status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("5/minute")
def trigger_scan(request: Request):
    try:
        summary = _run_scan()
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Scan timed out after 120 seconds.",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )
    return summary


# ── POST /api/kaaval/fake-pages/scan ──────────────────────
@router.post("/fake-pages/scan", status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("5/minute")
def trigger_fake_page_scan(request: Request):
    """Live fake-page recon: Google, Facebook, Telegram, lookalike-domain sweep.

    High-suspicion hits (score >= 0.7) are persisted as FAKE_PAGE threats.
    """
    try:
        results = _run_fake_page_scan()
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Fake-page scan timed out after 120 seconds.",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )
    return results


# ── GET /api/kaaval/verify-domain ─────────────────────────

def _extract_domain(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    return (parsed.netloc or parsed.path).split("@")[-1].split(":")[0].strip().lower()


def _is_public_host(hostname: str) -> bool:
    """Refuse to probe loopback/private/link-local targets — the domain in the
    request is attacker-influenced (it comes from a scraped finding), so this
    guards against using the server as an SSRF proxy into internal infra.
    """
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


def _probe_domain(domain: str, preferred_scheme: str) -> tuple[int | None, str | None]:
    """Best-effort liveness probe: HEAD first, GET fallback (some servers 403/406
    HEAD but happily serve GET), trying the finding's own scheme before the other.
    Any HTTP response — even a 403 or 404 — proves the server is up; only a
    connection-level failure (timeout, refused, DNS) means "not responding".
    """
    headers = {"User-Agent": "Mozilla/5.0 (compatible; SentinelBot/1.0)"}
    schemes = [preferred_scheme] + [s for s in ("https", "http") if s != preferred_scheme]
    for scheme in schemes:
        target = f"{scheme}://{domain}"
        try:
            resp = requests.head(target, timeout=8, allow_redirects=True, headers=headers)
            return resp.status_code, resp.headers.get("Server")
        except requests.RequestException:
            pass
        try:
            with requests.get(
                target, timeout=8, allow_redirects=True, headers=headers, stream=True
            ) as resp:
                return resp.status_code, resp.headers.get("Server")
        except requests.RequestException:
            pass
    return None, None


def _rdap_registrar(entities: list[dict]) -> str | None:
    for entity in entities:
        if "registrar" not in (entity.get("roles") or []):
            continue
        vcard = entity.get("vcardArray")
        if vcard and len(vcard) > 1:
            for field in vcard[1]:
                if field[0] == "fn" and len(field) > 3:
                    return field[3]
        return entity.get("handle")
    return None


@router.get("/verify-domain")
@limiter.limit("20/minute")
def verify_domain(url: str, request: Request):
    """Live-check + RDAP lookup for a fake-page finding's source domain.

    Best-effort: any failure (unreachable host, RDAP miss, bad URL) degrades
    to partial data rather than a 500 — this is an investigative aid, not a
    hard dependency for the finding to be actionable.
    """
    domain = _extract_domain(url)
    preferred_scheme = urlparse(url if "://" in url else f"https://{url}").scheme or "https"
    result: dict = {
        "domain": domain,
        "is_live": False,
        "http_status": None,
        "server": None,
        "registered": False,
        "creation_date": None,
        "registrar": None,
        "verification": "UNKNOWN",
    }
    if not domain or not _is_public_host(domain):
        return result

    http_status, server = _probe_domain(domain, preferred_scheme)
    result["http_status"] = http_status
    result["server"] = server
    result["is_live"] = http_status is not None

    try:
        rdap_resp = requests.get(f"https://rdap.org/domain/{domain}", timeout=8)
        if rdap_resp.ok:
            rdap = rdap_resp.json()
            result["registered"] = True
            for event in rdap.get("events", []):
                if event.get("eventAction") == "registration":
                    result["creation_date"] = event.get("eventDate")
                    break
            result["registrar"] = _rdap_registrar(rdap.get("entities", []))
    except (requests.RequestException, ValueError):
        pass

    if result["is_live"]:
        result["verification"] = "CONFIRMED_LIVE"
    elif result["registered"]:
        result["verification"] = "NOT_RESPONDING"

    return result


# ── GET /api/kaaval/metrics ───────────────────────────────
@router.get("/metrics")
def get_metrics():
    now = datetime.now(timezone.utc)
    since_24h = now - timedelta(hours=24)
    since_7d  = now - timedelta(days=7)

    with Session(engine) as session:
        total_threats_24h = session.execute(
            select(func.count()).select_from(Threat).where(Threat.detected_at >= since_24h)
        ).scalar_one()

        threats_7d = session.execute(
            select(func.count()).select_from(Threat).where(Threat.detected_at >= since_7d)
        ).scalar_one()

        type_rows = session.execute(
            select(Threat.threat_type, func.count())
            .where(Threat.detected_at >= since_24h)
            .group_by(Threat.threat_type)
        ).all()
        by_type: dict[str, int] = {tt: cnt for tt, cnt in type_rows}

        brigade_detected_24h = by_type.get("BRIGADE", 0)
        fake_pages_24h       = by_type.get("FAKE_PAGE", 0)
        deepfakes_24h        = by_type.get("DEEPFAKE_AUDIO", 0) + by_type.get("DEEPFAKE_VIDEO", 0)

        # sources_monitored: 7 fact-check scrapers + enabled plugins in DB
        enabled_plugins: int = session.execute(
            text("SELECT COUNT(*) FROM plugins WHERE enabled = true")
        ).scalar_one() or 0
        sources_monitored = 7 + enabled_plugins

        # active_agents: count .py agent files (non-dunder) across agent dirs
        agent_dirs = [
            _PROJECT_ROOT / "agents" / "kaaval",
            _PROJECT_ROOT / "agents" / "veriff",
            _PROJECT_ROOT / "agents" / "voice",
        ]
        active_agents = sum(
            1
            for d in agent_dirs
            if d.exists()
            for f in d.glob("*.py")
            if not f.name.startswith("_")
        )

        # last_scan: most recent completed scan timestamp
        last_scan_ts = session.execute(
            text("SELECT MAX(completed_at) FROM scans WHERE status = 'completed'")
        ).scalar_one()
        last_scan = last_scan_ts.isoformat() if last_scan_ts else None

        return {
            "total_threats_24h":    total_threats_24h,
            "threats_7d":           threats_7d,
            "by_type":              by_type,
            "brigade_detected_24h": brigade_detected_24h,
            "fake_pages_24h":       fake_pages_24h,
            "deepfakes_24h":        deepfakes_24h,
            "sources_monitored":    sources_monitored,
            "active_agents":        active_agents,
            "last_scan":            last_scan,
        }


# ── POST /api/kaaval/brigade/analyze ─────────────────────
@router.post("/brigade/analyze")
@limiter.limit("20/minute")
def analyze_brigade(body: BrigadeAnalyzeRequest, request: Request):
    """Run multi-signal brigade detection on a submitted comment batch.

    If a brigade is confirmed, a BRIGADE threat is persisted automatically.
    """
    try:
        result = _run_brigade_analysis(
            body.video_id,
            [c.model_dump() for c in body.comments],
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Brigade analysis timed out.",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )

    if result.get("brigade_detected"):
        confidence = result.get("confidence", 0.0)
        severity = "HIGH" if confidence >= 0.80 else "MEDIUM"
        top_kw = ", ".join(result.get("top_keywords", [])[:5]) or "unknown"
        with Session(engine) as session:
            threat = Threat(
                threat_type="BRIGADE",
                severity=severity,
                title=f"Coordinated comment brigade on video {body.video_id}",
                description=(
                    f"{result.get('affected_comment_count', 0)} coordinated comments detected. "
                    f"Confidence: {confidence:.0%}. "
                    f"Triggered signals: {', '.join(result.get('triggered_signals', []))}. "
                    f"Top keywords: {top_kw}."
                ),
                source_platform="youtube",
                source_url=f"https://youtube.com/watch?v={body.video_id}",
                detected_at=datetime.now(timezone.utc),
                status="OPEN",
                judge={"brigade_signals": result.get("signals", {})},
            )
            session.add(threat)
            session.commit()
            result["threat_id"] = str(threat.id)

    return result
