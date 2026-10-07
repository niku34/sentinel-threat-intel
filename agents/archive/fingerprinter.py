"""Archive perceptual-fingerprinting engine.

Standalone module — no DB, no FastAPI. It knows how to turn media (images or
video frames, raw files or in-memory bytes) into a perceptual hash (pHash) and
how to compare / search those hashes.

pHash gives us near-duplicate detection: visually identical or lightly re-encoded
clips hash close together (small Hamming distance) even when the bytes differ.

Video frame extraction prefers OpenCV (cv2) when installed and falls back to
PIL for animated/multi-frame images. If a real video file is supplied without
cv2 available, a clear RuntimeError is raised rather than silently guessing.
"""
from __future__ import annotations

import io
import os
import tempfile

import imagehash
from PIL import Image

# pHash is 64-bit → 16 hex chars → max Hamming distance of 64 bits.
_HASH_BITS = 64
# Hamming distance <= 10 counts as a match (similarity >= 0.85 with 64-bit hash).
MATCH_HAMMING_THRESHOLD = 10
MATCH_SIMILARITY_THRESHOLD = 0.85

_IMAGE_TYPES = {"image", "img", "photo", "jpeg", "jpg", "png", "gif", "webp"}
_VIDEO_TYPES = {"video", "clip", "mp4", "mov", "mkv", "webm", "avi"}


class ArchiveFingerprinter:
    """Compute and compare perceptual hashes for archive media."""

    # ── hashing ────────────────────────────────────────────
    def hash_image(self, image_path: str) -> str:
        """pHash an image file, returned as a hex string."""
        with Image.open(image_path) as img:
            return self._phash(img)

    def hash_video_frame(self, video_path: str) -> str:
        """Extract the middle frame of a video and pHash it."""
        frame = self._extract_middle_frame(video_path)
        return self._phash(frame)

    def hash_from_bytes(self, data: bytes, media_type: str) -> str:
        """pHash raw bytes (e.g. an uploaded clip).

        Images are opened directly; videos are spooled to a temp file so the
        frame extractor can seek to the middle frame.
        """
        kind = (media_type or "").lower().split("/")[-1]
        if kind in _IMAGE_TYPES:
            with Image.open(io.BytesIO(data)) as img:
                return self._phash(img)
        if kind in _VIDEO_TYPES:
            suffix = f".{kind}" if kind not in {"video", "clip"} else ".mp4"
            tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            try:
                tmp.write(data)
                tmp.flush()
                tmp.close()
                return self.hash_video_frame(tmp.name)
            finally:
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass
        # Unknown type — best-effort treat as an image.
        with Image.open(io.BytesIO(data)) as img:
            return self._phash(img)

    # ── comparison / search ────────────────────────────────
    def compare(self, hash1: str, hash2: str) -> float:
        """Similarity 0.0–1.0 between two pHash hex strings.

        1.0 = identical, 0.0 = maximally different. Derived from Hamming
        distance over the 64-bit hash: similarity = 1 - distance / 64.
        """
        h1 = imagehash.hex_to_hash(hash1)
        h2 = imagehash.hex_to_hash(hash2)
        distance = h1 - h2  # Hamming distance
        return float(round(1.0 - (distance / _HASH_BITS), 4))

    def is_match(self, hash1: str, hash2: str) -> bool:
        h1 = imagehash.hex_to_hash(hash1)
        h2 = imagehash.hex_to_hash(hash2)
        return (h1 - h2) <= MATCH_HAMMING_THRESHOLD

    def search_archive(self, query_hash: str, db_hashes: list[dict]) -> list[dict]:
        """Compare query_hash against every db hash, return matches sorted desc.

        db_hashes: [{"clip_id": ..., "fingerprint_hash": ..., ...}, ...]
        Returns matches (similarity >= 0.85) as
            [{"clip_id", "similarity", "fingerprint_hash", "fingerprint_type"?}]
        """
        matches: list[dict] = []
        for row in db_hashes:
            candidate = row.get("fingerprint_hash")
            if not candidate:
                continue
            try:
                similarity = self.compare(query_hash, candidate)
            except (ValueError, TypeError):
                continue
            if similarity >= MATCH_SIMILARITY_THRESHOLD:
                match = {
                    "clip_id": row.get("clip_id"),
                    "similarity": similarity,
                    "fingerprint_hash": candidate,
                }
                if "fingerprint_type" in row:
                    match["fingerprint_type"] = row["fingerprint_type"]
                matches.append(match)
        matches.sort(key=lambda m: m["similarity"], reverse=True)
        return matches

    # ── internals ──────────────────────────────────────────
    def _phash(self, img: Image.Image) -> str:
        # Normalise mode so pHash is stable across source formats.
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        return str(imagehash.phash(img))

    def _extract_middle_frame(self, video_path: str) -> Image.Image:
        """Return the middle frame of a video as a PIL image.

        Uses OpenCV when available. Falls back to PIL for multi-frame images
        (e.g. animated GIF/WebP). Raises if neither can read the file.
        """
        try:
            import cv2  # optional heavy dep
        except ImportError:
            cv2 = None

        if cv2 is not None:
            cap = cv2.VideoCapture(video_path)
            try:
                total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
                target = total // 2 if total > 0 else 0
                if total > 0:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, target)
                ok, frame = cap.read()
                if not ok or frame is None:
                    raise RuntimeError(f"Could not read a frame from {video_path}")
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                return Image.fromarray(frame_rgb)
            finally:
                cap.release()

        # No cv2 — try PIL (works for animated images, not real video codecs).
        try:
            img = Image.open(video_path)
            n = getattr(img, "n_frames", 1)
            img.seek(n // 2)
            return img.convert("RGB")
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "Video frame extraction requires OpenCV (cv2), which is not "
                f"installed, and PIL could not decode {video_path!r}: {exc}. "
                "Install opencv-python-headless to fingerprint real video files."
            ) from exc


if __name__ == "__main__":
    # Smoke test with two synthetic images.
    fp = ArchiveFingerprinter()
    a = Image.new("RGB", (256, 256), (30, 30, 30))
    b = Image.new("RGB", (256, 256), (30, 30, 30))
    buf_a, buf_b = io.BytesIO(), io.BytesIO()
    a.save(buf_a, "PNG")
    b.save(buf_b, "PNG")
    ha = fp.hash_from_bytes(buf_a.getvalue(), "image/png")
    hb = fp.hash_from_bytes(buf_b.getvalue(), "image/png")
    print("hash a:", ha)
    print("hash b:", hb)
    print("similarity:", fp.compare(ha, hb))
    print("search:", fp.search_archive(ha, [{"clip_id": "c1", "fingerprint_hash": hb}]))
