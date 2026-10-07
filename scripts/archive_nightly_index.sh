#!/usr/bin/env bash
# Nightly archive indexing — off-peak (2 AM) automation for POST /api/archive/index.
#
# This does NOT run a disk walk itself and does NOT contain any indexing
# logic of its own — it only calls the existing, already-tested
# POST /api/archive/index endpoint over HTTP, exactly like a manual click
# from Admin would. That endpoint is defensive/best-effort per file
# (agents/archive/fingerprinter._index_walk in api/routers/archive.py) and
# skips indexing entirely if storage_path doesn't exist on disk.
#
# Live checks (POST /api/archive/fingerprint, used by Veriff/Kavasam) only
# ever call _load_db_hashes() — a plain SELECT against archive_fingerprints —
# never the disk walk. This job cannot interfere with them: it is a single
# extra HTTP request to the already-running API, and the disk walk it
# triggers runs in a FastAPI BackgroundTask on the API's own event loop, the
# same as when an operator clicks "Index now" in Admin during business hours
# today. Scheduling it at 2 AM is about avoiding unnecessary I/O contention
# and keeping "when did the archive last change" predictable, not because
# concurrent access is unsafe.
#
# Safe by construction if storage_path isn't configured: reads it from the
# live API (GET /api/archive/stats, which reads platform_config) and skips
# with a clear log line rather than guessing a path or erroring.

set -uo pipefail

API_URL="${SENTINEL_API_URL:-http://localhost:8000}"
LOG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/logs"
LOG_FILE="$LOG_DIR/archive_nightly_index.log"
DEFAULT_PLACEHOLDER="/mnt/archive (not configured)"

mkdir -p "$LOG_DIR"

log() {
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') $*" >> "$LOG_FILE"
}

log "=== nightly archive index run starting ==="

stats_json="$(curl -s --max-time 10 "$API_URL/api/archive/stats" 2>>"$LOG_FILE")"
if [ -z "$stats_json" ]; then
    log "SKIP: could not reach $API_URL/api/archive/stats (API down?) — no action taken"
    exit 0
fi

storage_path="$(printf '%s' "$stats_json" | python3 -c 'import json,sys
try:
    print(json.load(sys.stdin).get("storage_path") or "")
except Exception:
    print("")' 2>>"$LOG_FILE")"

if [ -z "$storage_path" ] || [ "$storage_path" = "$DEFAULT_PLACEHOLDER" ]; then
    log "SKIP: no archive_storage_path configured in platform_config — skipping gracefully"
    exit 0
fi

log "Triggering index for storage_path=$storage_path"
response="$(curl -s --max-time 30 -X POST "$API_URL/api/archive/index" \
    -H "Content-Type: application/json" \
    -d "{\"storage_path\": $(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$storage_path")}" \
    -w '\nHTTP_STATUS:%{http_code}' 2>>"$LOG_FILE")"

http_status="$(printf '%s' "$response" | grep -o 'HTTP_STATUS:[0-9]*' | cut -d: -f2)"
body="$(printf '%s' "$response" | sed '/HTTP_STATUS:/d')"

if [ "$http_status" = "200" ]; then
    log "OK: $body"
else
    log "WARN: unexpected HTTP $http_status from /api/archive/index: $body"
fi

log "=== nightly archive index run finished ==="
