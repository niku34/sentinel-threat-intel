"""Persistent embedding server for Veriff.

Loads all-MiniLM-L6-v2 ONCE on startup, then serves encode requests over
HTTP so every Veriff call gets sub-millisecond embedding instead of the
~10s cold-start penalty of loading SentenceTransformer in each subprocess.

Usage:
    agents/.venv/bin/python agents/veriff/embedding_server.py

Endpoints:
    GET  /health  → {"status": "ok", "model": "all-MiniLM-L6-v2"}
    POST /embed   → {"texts": ["..."]} → {"embeddings": [[...]]}

Listens on 127.0.0.1:11435 (internal only).
NOTE: If Nginx is deployed for Ollama proxy, use port 11436 for that
      config to avoid conflict with this server on 11435.
"""
from __future__ import annotations

import json
import logging
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("embedding_server")

HOST = "127.0.0.1"
PORT = 11435
MODEL_NAME = "all-MiniLM-L6-v2"

logger.info("Loading model %s — this takes ~10s on first run ...", MODEL_NAME)
try:
    from sentence_transformers import SentenceTransformer  # noqa: E402
    _MODEL = SentenceTransformer(MODEL_NAME)
    logger.info("Model %s loaded and ready.", MODEL_NAME)
except Exception as exc:
    logger.error("Failed to load model %s: %s", MODEL_NAME, exc)
    sys.exit(1)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        pass  # suppress per-request CLF log; errors still go to logger

    def _send_json(self, code: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {"status": "ok", "model": MODEL_NAME})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/embed":
            self._send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length))
            texts = payload.get("texts", [])
            if not isinstance(texts, list) or not texts:
                self._send_json(400, {"error": "texts must be a non-empty list"})
                return
            vecs = _MODEL.encode(texts, normalize_embeddings=True)
            self._send_json(200, {"embeddings": [v.tolist() for v in vecs]})
        except Exception as exc:
            logger.warning("embed error: %s", exc)
            self._send_json(500, {"error": str(exc)})


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), _Handler)
    logger.info("Embedding server listening on %s:%d", HOST, PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down.")
        server.shutdown()
