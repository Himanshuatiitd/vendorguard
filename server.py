"""Local web server for the VendorGuard dashboard (stdlib only).

Start it, open the printed URL, and the dashboard gains a live "Check a vendor"
panel. Inputs (form fields, pasted JSON, pasted text, or an uploaded .json/.txt
file) are posted to /api/verify, which queues the existing Python pipeline on a
background worker. The page polls /api/jobs/<id> and shows the verdict.

    python server.py            # http://127.0.0.1:8765
    python server.py --open     # also opens the browser
    python server.py --port 9000

No third-party dependencies, same as the rest of the project.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import traceback
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from engine import dashboard, intake  # noqa: E402
from engine import main as engine  # noqa: E402
from engine.main import RUNS_DIR  # noqa: E402

MAX_BODY = 2 * 1024 * 1024  # 2 MB is plenty for one submission
RUN_LOCK = threading.Lock()  # serialises pipeline runs + index/dashboard writes
JOB_LOCK = threading.Lock()
JOBS: dict = {}
POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="verify")

VERDICT = {
    "APPROVED": {"verdict": "REAL", "text": "verified vendor - checks passed"},
    "PENDING": {"verdict": "NEEDS_CHECK", "text": "not enough evidence yet - fixable issues"},
    "REJECTED": {"verdict": "FAKE", "text": "hard-stop fraud signals - do not onboard"},
}


def _job_update(job_id: str, **fields) -> None:
    with JOB_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(fields)


def _run_job(job_id: str, sub: dict, raw_text: str | None, source_name: str) -> None:
    _job_update(job_id, status="running")
    print(f"[verify] start {job_id}  {sub.get('legal_name', '(no name)')}")
    try:
        extra = {}
        if raw_text:
            extra["input.txt"] = raw_text
        with RUN_LOCK:
            result = engine.run_submission_data(
                sub,
                stem=sub.get("submission_id") or "WEB",
                master=engine.load_master(),
                verbose=False,
                extra_files=extra,
            )
            out = dashboard.build()

        data = result.to_dict()
        if result.followup_file:
            fp = RUNS_DIR / result.run_id / result.followup_file
            if fp.exists():
                data["followup_text"] = fp.read_text(encoding="utf-8")
        data["verdict"] = VERDICT.get(result.decision, VERDICT["PENDING"])

        _job_update(job_id, status="done", result=data)
        print(f"[verify] {result.decision:<8} risk {result.risk_score:>3}/100  "
              f"{result.run_id}  -> {out.name}")
    except Exception as exc:  # surface the failure to the page, keep serving
        traceback.print_exc()
        _job_update(job_id, status="error", error=f"{type(exc).__name__}: {exc}")


class Handler(BaseHTTPRequestHandler):
    server_version = "VendorGuard/1.1"

    # -- plumbing ---------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

    def log_message(self, fmt, *args):  # quieter default access log
        if "/api/jobs/" not in (args[0] if args else ""):
            super().log_message(fmt, *args)

    # -- GET --------------------------------------------------------------
    def do_GET(self):
        path = urlparse(self.path).path

        if path in ("/", "/index.html", "/dashboard.html"):
            try:
                with RUN_LOCK:
                    out = dashboard.build()
                self._send(200, out.read_bytes(), "text/html; charset=utf-8")
            except Exception as exc:
                traceback.print_exc()
                self._json(500, {"error": f"could not build dashboard: {exc}"})
            return

        if path == "/api/health":
            try:
                with RUN_LOCK:
                    n = len(dashboard.load_runs())
            except Exception:
                n = 0
            self._json(200, {"ok": True, "runs": n})
            return

        if path == "/api/runs":
            try:
                with RUN_LOCK:
                    runs = dashboard.load_runs()
                self._json(200, {"runs": runs})
            except Exception as exc:
                self._json(500, {"error": str(exc)})
            return

        if path.startswith("/api/jobs/"):
            job_id = path.rsplit("/", 1)[-1]
            with JOB_LOCK:
                job = dict(JOBS.get(job_id, {}))
            if not job:
                self._json(404, {"error": "unknown job"})
            else:
                self._json(200, job)
            return

        if path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return

        self._json(404, {"error": "not found"})

    # -- POST -------------------------------------------------------------
    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/api/verify":
            self._json(404, {"error": "not found"})
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            self._json(400, {"error": "empty request body"})
            return
        if length > MAX_BODY:
            self._json(413, {"error": "input too large (max 2 MB)"})
            return

        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as exc:
            self._json(400, {"error": f"request body must be JSON: {exc}"})
            return
        if not isinstance(body, dict):
            self._json(400, {"error": "request body must be a JSON object"})
            return

        mode = (body.get("mode") or "json").lower()
        warnings: list = []
        raw_text = None
        try:
            if mode == "text":
                raw_text = str(body.get("text") or "")
                sub, warnings = intake.parse_text(raw_text)
                if not sub:
                    raise ValueError("no vendor fields were recognised in the text")
            elif mode in ("json", "form"):
                raw = body.get("submission")
                if not isinstance(raw, dict):
                    raise ValueError("'submission' must be a JSON object")
                sub, warnings = intake.normalise_submission(raw)
            else:
                raise ValueError(f"unknown mode '{mode}' (expected form, json or text)")
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return

        sub = intake.default_metadata(sub)
        job_id = uuid.uuid4().hex[:12]
        with JOB_LOCK:
            JOBS[job_id] = {
                "status": "queued",
                "submission_id": sub.get("submission_id"),
                "legal_name": sub.get("legal_name", ""),
                "warnings": warnings,
            }
            # keep memory bounded on a long-lived demo server
            if len(JOBS) > 200:
                for old in [k for k, v in JOBS.items() if v.get("status") in ("done", "error")][:50]:
                    JOBS.pop(old, None)

        POOL.submit(_run_job, job_id, sub, raw_text, mode)
        self._json(202, {"job_id": job_id, "warnings": warnings})


def _make_server(host: str, port: int) -> ThreadingHTTPServer:
    last = None
    for candidate in range(port, port + 10):
        try:
            httpd = ThreadingHTTPServer((host, candidate), Handler)
            httpd.daemon_threads = True
            return httpd
        except OSError as exc:
            last = exc
    raise SystemExit(f"could not bind {host}:{port}-{port + 9}: {last}")


def main() -> None:
    ap = argparse.ArgumentParser(description="VendorGuard interactive dashboard server")
    port_env = os.environ.get("PORT")
    ap.add_argument(
        "--host",
        default=os.environ.get("HOST") or ("0.0.0.0" if port_env else "127.0.0.1"),
        help="interface to bind (default 127.0.0.1, or 0.0.0.0 when $PORT is set)",
    )
    ap.add_argument("--port", type=int, default=int(port_env or 8765),
                    help="port to listen on (default 8765, or $PORT)")
    ap.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    with RUN_LOCK:
        dashboard.build()  # make sure dashboard.html exists even before first GET

    httpd = _make_server(args.host, args.port)
    port = httpd.server_address[1]
    display_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    url = f"http://{display_host}:{port}/"
    print()
    print("=" * 62)
    print(" VendorGuard - interactive dashboard")
    print(f" open: {url}")
    print(f" bound: {args.host}:{port}")
    print(" inputs: form fields, JSON, labelled text, or file upload")
    print(" pipeline runs in the background; results are written to runs/")
    print(" Ctrl+C to stop")
    print("=" * 62)
    print()
    if args.open:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping server")
    finally:
        httpd.server_close()
        POOL.shutdown(wait=False, cancel_futures=False)


if __name__ == "__main__":
    main()
