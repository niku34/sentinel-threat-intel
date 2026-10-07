"""Image analysis for Veriff.

Wraps Google Cloud Vision when GOOGLE_APPLICATION_CREDENTIALS is configured,
giving us reverse-image (web) search, label/text detection and SafeSearch
signals. When no credentials are present we fall back to a lightweight
PIL-based analysis so the pipeline still returns a well-shaped result.

Public surface:
    analyze_image(image_bytes)        -> dict
    reverse_image_search(image_bytes) -> list[dict]
"""
from __future__ import annotations

import io
import logging
import os
from typing import List, Optional

logger = logging.getLogger("veriff.vision_analyzer")

_SAFE_LIKELIHOOD = {
    0: "UNKNOWN",
    1: "VERY_UNLIKELY",
    2: "UNLIKELY",
    3: "POSSIBLE",
    4: "LIKELY",
    5: "VERY_LIKELY",
}
# SafeSearch categories at POSSIBLE+ that we surface as a flag.
_FLAG_AT = {"POSSIBLE", "LIKELY", "VERY_LIKELY"}


class VisionAnalyzer:
    def __init__(self) -> None:
        self._client = None
        self._vision = None
        self.source = "pil_fallback"
        creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if creds and os.path.exists(creds):
            try:
                from google.cloud import vision

                self._vision = vision
                self._client = vision.ImageAnnotatorClient()
                self.source = "google_vision"
                logger.info("Google Vision client initialised")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Google Vision unavailable (%s) — PIL fallback", exc)
                self._client = None
                self.source = "pil_fallback"
        else:
            logger.info("No GOOGLE_APPLICATION_CREDENTIALS — using PIL fallback")

    @property
    def available(self) -> bool:
        return self._client is not None

    # ── public API ────────────────────────────────────────────
    def analyze_image(self, image_bytes: bytes) -> dict:
        if self._client is not None:
            try:
                return self._analyze_google(image_bytes)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Google Vision analyze failed (%s) — PIL fallback", exc)
        return self._analyze_pil(image_bytes)

    def reverse_image_search(self, image_bytes: bytes) -> List[dict]:
        """Reverse image (web) search — matching URLs from the web."""
        if self._client is None:
            return []
        try:
            image = self._vision.Image(content=image_bytes)
            resp = self._client.web_detection(image=image)
            return self._web_matches(resp.web_detection)
        except Exception as exc:  # noqa: BLE001
            logger.warning("reverse_image_search failed: %s", exc)
            return []

    # ── Google Vision ─────────────────────────────────────────
    def _analyze_google(self, image_bytes: bytes) -> dict:
        vision = self._vision
        image = vision.Image(content=image_bytes)

        features = [
            vision.Feature(type_=vision.Feature.Type.SAFE_SEARCH_DETECTION),
            vision.Feature(type_=vision.Feature.Type.LABEL_DETECTION, max_results=15),
            vision.Feature(type_=vision.Feature.Type.WEB_DETECTION),
            vision.Feature(type_=vision.Feature.Type.TEXT_DETECTION),
        ]
        request = vision.AnnotateImageRequest(image=image, features=features)
        resp = self._client.annotate_image(request=request)

        labels = [
            {"description": l.description, "score": round(float(l.score), 4)}
            for l in resp.label_annotations
        ]

        web_matches = self._web_matches(resp.web_detection)

        text_in_image = ""
        if resp.text_annotations:
            text_in_image = (resp.text_annotations[0].description or "").strip()

        safe = resp.safe_search_annotation
        safe_flags = self._safe_search_flags(safe)

        return {
            "source": "google_vision",
            "labels": labels,
            "web_matches": web_matches,
            "text_in_image": text_in_image,
            "safe_search_flags": safe_flags,
        }

    def _web_matches(self, web_detection) -> List[dict]:
        matches: List[dict] = []
        if web_detection is None:
            return matches
        # Full + partial matching images carry the strongest recycling signal.
        for m in list(web_detection.full_matching_images) + list(
            web_detection.partial_matching_images
        ):
            matches.append({"url": m.url, "title": "", "score": round(float(m.score or 0.0), 4)})
        for page in web_detection.pages_with_matching_images:
            matches.append(
                {
                    "url": page.url,
                    "title": (page.page_title or "").strip(),
                    "score": round(float(page.score or 0.0), 4),
                }
            )
        # Dedupe by url, keep highest score.
        best: dict = {}
        for m in matches:
            u = m["url"]
            if u not in best or m["score"] > best[u]["score"]:
                best[u] = m
        return sorted(best.values(), key=lambda x: -x["score"])[:15]

    def _safe_search_flags(self, safe) -> List[str]:
        flags: List[str] = []
        if safe is None:
            return flags
        checks = {
            "adult": safe.adult,
            "violence": safe.violence,
            "racy": safe.racy,
            "spoof": safe.spoof,  # spoof == likely doctored / meme
            "medical": safe.medical,
        }
        for name, likelihood in checks.items():
            label = _SAFE_LIKELIHOOD.get(int(likelihood), "UNKNOWN")
            if label in _FLAG_AT:
                flags.append(f"{name}:{label}")
        return flags

    # ── PIL fallback ──────────────────────────────────────────
    def _analyze_pil(self, image_bytes: bytes) -> dict:
        labels: List[dict] = []
        text_in_image = ""
        try:
            from PIL import Image

            img = Image.open(io.BytesIO(image_bytes))
            w, h = img.size
            labels = [
                {"description": f"{img.mode} image", "score": 1.0},
                {"description": f"{w}x{h}px", "score": 1.0},
                {"description": (img.format or "unknown").lower(), "score": 1.0},
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("PIL fallback failed to open image: %s", exc)
        return {
            "source": "pil_fallback",
            "labels": labels,
            "web_matches": [],
            "text_in_image": text_in_image,
            "safe_search_flags": [],
        }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    va = VisionAnalyzer()
    print("available:", va.available, "source:", va.source)
    # 1x1 PNG
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d494844520000000100000001080600000"
        "01f15c4890000000d4944415478da6360000002000155a2b4e00000000049454e44ae426082"
    )
    import json

    print(json.dumps(va.analyze_image(png), ensure_ascii=False, indent=2))
