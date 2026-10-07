"""Deepfake analysis API — spectral feature extraction + Ollama Qwen 2.5 14B.

POST /api/deepfake/analyze accepts either:
  • a multipart `file` upload (image / audio / video), or
  • a JSON body {text_claim, source_url} for text-only deepfake claims.

The heavy lifting lives in agents/deepfake/detector.py (deps: PIL + numpy, both
in this API venv), imported directly with the project root on sys.path.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.limiter import limiter

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agents.deepfake.detector import DeepfakeDetector  # noqa: E402

router = APIRouter(prefix="/api/deepfake", tags=["deepfake"])

_DETECTOR = DeepfakeDetector()

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".aac"}
_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}


class TextClaimRequest(BaseModel):
    text_claim: str
    source_url: str = ""


def _kind(content_type: str, filename: str) -> str:
    ct = (content_type or "").lower()
    ext = Path(filename or "").suffix.lower()
    if ct.startswith("image/") or ext in _IMAGE_EXTS:
        return "image"
    if ct.startswith("audio/") or ext in _AUDIO_EXTS:
        return "audio"
    if ct.startswith("video/") or ext in _VIDEO_EXTS:
        return "video"
    return "image"  # best-effort default


@router.post("/analyze")
@limiter.limit("20/minute")
async def analyze(request: Request):
    started = time.monotonic()
    content_type = request.headers.get("content-type", "")

    if "multipart/form-data" in content_type:
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            raise HTTPException(status_code=400, detail="No 'file' in upload.")
        data = await upload.read()
        kind = _kind(getattr(upload, "content_type", ""), getattr(upload, "filename", ""))
        try:
            if kind == "audio":
                result = _DETECTOR.analyze_audio(data)
            else:
                # image and video (first frame best-effort) both go through
                # the image analyzer; PIL handles stills, video falls back.
                result = _DETECTOR.analyze_image(data)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=422, detail=f"Could not analyze media: {exc}")
        result["media_kind"] = kind
    else:
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(
                status_code=400,
                detail="Send a multipart 'file' upload or JSON {text_claim, source_url}.",
            )
        body = TextClaimRequest(**(payload or {}))
        if not body.text_claim.strip():
            raise HTTPException(status_code=400, detail="text_claim is required.")
        result = _DETECTOR.analyze_text_claim(body.text_claim, body.source_url)
        result["media_kind"] = "text"

    result["processing_time_ms"] = int((time.monotonic() - started) * 1000)
    # Ensure the documented response contract keys are present.
    result.setdefault("analysis_method", "ollama_spectral")
    result.setdefault("indicators", [])
    return result
