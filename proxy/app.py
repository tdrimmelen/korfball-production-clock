import asyncio
import datetime
import os
import time
from contextlib import asynccontextmanager
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CACHE_TTL = float(os.getenv("CACHE_TTL_SECONDS", "0.1"))  # 100ms default

_raw_allowlist = os.getenv(
    "ALLOWED_UPSTREAM_HOSTS",
    "10.12.0.62,10.12.0.61,stproductiontl67df2.z16.web.core.windows.net",
)
ALLOWED_UPSTREAM_HOSTS: set[str] = {h.strip() for h in _raw_allowlist.split(",")}

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
# key → (body: bytes, headers: dict, status_code: int, fetched_at: float)
_cache: dict[str, tuple[bytes, dict, int, float]] = {}
_cache_locks: dict[str, asyncio.Lock] = {}
_locks_meta_lock = asyncio.Lock()

_http_client: httpx.AsyncClient | None = None

_timer: dict = {
    "mode": "game",   # "game" | "countdown"
    "target": None,   # datetime.time | None
    "label": "Timer", # shown in #match-time-label when mode=countdown
}

# ---------------------------------------------------------------------------
# App lifespan: manage shared httpx client
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _http_client
    _http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(5.0),
        follow_redirects=True,
    )
    yield
    await _http_client.aclose()


app = FastAPI(lifespan=lifespan)

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "*",
}


def _add_cors(headers: dict) -> dict:
    merged = dict(headers)
    for k, v in CORS_HEADERS.items():
        merged.setdefault(k, v)  # preserve upstream CORS headers if already present
    return merged


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------
async def _get_lock(url: str) -> asyncio.Lock:
    async with _locks_meta_lock:
        if url not in _cache_locks:
            _cache_locks[url] = asyncio.Lock()
        return _cache_locks[url]


async def _get_cached_or_fetch(url: str) -> tuple[bytes, dict, int]:
    lock = await _get_lock(url)
    async with lock:
        entry = _cache.get(url)
        if entry and (time.monotonic() - entry[3]) < CACHE_TTL:
            return entry[0], entry[1], entry[2]

        resp = await _http_client.get(url)
        body = resp.content
        headers = dict(resp.headers)
        status = resp.status_code
        _cache[url] = (body, headers, status, time.monotonic())
        return body, headers, status


# ---------------------------------------------------------------------------
# Timer helpers
# ---------------------------------------------------------------------------
def _calc_remaining() -> float | None:
    target: datetime.time | None = _timer["target"]
    if target is None:
        return None
    now = datetime.datetime.now()
    target_dt = now.replace(hour=target.hour, minute=target.minute, second=0, microsecond=0)
    return max((target_dt - now).total_seconds(), 0.0)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.options("/proxy")
async def proxy_preflight():
    """Handle CORS preflight without contacting the upstream."""
    return Response(status_code=204, headers=CORS_HEADERS)


@app.options("/{path:path}")
async def options_preflight(_path: str):
    return Response(status_code=204, headers=CORS_HEADERS)


@app.get("/proxy")
async def proxy(request: Request):
    target_url = request.query_params.get("url")
    if not target_url:
        return JSONResponse({"error": "Missing 'url' query parameter"}, status_code=400)

    parsed = urlparse(target_url)
    if parsed.hostname not in ALLOWED_UPSTREAM_HOSTS:
        return JSONResponse(
            {"error": f"Upstream host '{parsed.hostname}' is not in the allowlist"},
            status_code=403,
        )

    try:
        body, upstream_headers, status = await _get_cached_or_fetch(target_url)
    except httpx.TimeoutException:
        return JSONResponse({"error": "Upstream request timed out"}, status_code=504)
    except httpx.RequestError as exc:
        return JSONResponse({"error": f"Upstream unreachable: {exc}"}, status_code=502)

    content_type = upstream_headers.get("content-type", "application/octet-stream")
    response_headers = _add_cors({"content-type": content_type})

    return Response(content=body, status_code=status, headers=response_headers)


@app.get("/timer/status")
async def timer_status():
    remaining = _calc_remaining()
    target_str = _timer["target"].strftime("%H:%M") if _timer["target"] else None
    return JSONResponse(
        {"mode": _timer["mode"], "remaining_seconds": remaining, "target": target_str, "label": _timer["label"]},
        headers=_add_cors({"content-type": "application/json"}),
    )


@app.post("/timer/set")
async def timer_set(request: Request):
    body = await request.json()
    try:
        parsed = datetime.datetime.strptime(body.get("target", ""), "%H:%M").time()
    except ValueError:
        return JSONResponse(
            {"error": "Invalid target format, expected HH:MM"},
            status_code=400,
            headers=_add_cors({}),
        )
    _timer["target"] = parsed
    return JSONResponse({"ok": True, "target": body["target"]}, headers=_add_cors({}))


@app.post("/timer/mode")
async def timer_mode(request: Request):
    body = await request.json()
    mode = body.get("mode", "")
    if mode not in ("game", "countdown"):
        return JSONResponse(
            {"error": "mode must be 'game' or 'countdown'"},
            status_code=400,
            headers=_add_cors({}),
        )
    _timer["mode"] = mode
    return JSONResponse({"ok": True, "mode": mode}, headers=_add_cors({}))


@app.post("/timer/label")
async def timer_label(request: Request):
    body = await request.json()
    label = body.get("label", "").strip()
    if not label:
        return JSONResponse({"error": "label must not be empty"}, status_code=400, headers=_add_cors({}))
    _timer["label"] = label
    return JSONResponse({"ok": True, "label": label}, headers=_add_cors({}))


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/control")
async def control():
    return FileResponse("control.html", media_type="text/html")


@app.get("/")
async def index():
    return FileResponse("klokken.html", media_type="text/html")
