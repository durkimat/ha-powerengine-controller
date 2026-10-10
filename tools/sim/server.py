"""The GUI's server: `pe_sim.py serve`. Standard library only, bound to 127.0.0.1 unless `--host` says otherwise
(there is no login: anyone who can reach the address can start runs and read the plans).

It serves the viewer (`viewer/`) and a small JSON API: what is available (days, branches, archived configs, saved
variants), the engine settings of any code version, running a batch of variants over days, and reading a finished run.
Runs happen in the same worker processes as the command line's, cached the same way, so both give the same numbers.

A page on another site must not be able to start runs: every request must name this host, and every POST must carry the
`X-PE-Sim` header (a cross-site page cannot send it without a permission this server never gives).
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import export
import runner
import workspace

VIEWER = Path(__file__).resolve().parent / "viewer"
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
}
KEY = re.compile(r"^[0-9a-f]{16}$")
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class Batches:
    """Batches of jobs the page started: progress is read by polling."""

    def __init__(self, ws: runner.Workspace, n_jobs: int):
        self.ws, self.n_jobs = ws, n_jobs
        self.items: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.last_catalogue: dict[str, dict] = {}

    def start(self, variants: list[runner.Variant], days: list[str], force: bool) -> dict:
        plan, jobs = [], []
        for v in variants:
            row = {"variant": v.as_dict(), "jobs": []}
            for d in days:
                job = self.ws.job(v, d)
                jobs.append(job)
                row["jobs"].append({"key": job["key"], "day": d, "status": "queued"})
            plan.append(row)
        batch = {"id": uuid.uuid4().hex[:10], "started": time.time(), "done": False, "plan": plan, "log": []}
        with self.lock:
            self.items[batch["id"]] = batch
        threading.Thread(target=self._run, args=(batch, jobs, variants, days, force), daemon=True).start()
        return batch

    def _run(self, batch, jobs, variants, days, force):
        def done(job, result):
            with self.lock:
                for row in batch["plan"]:
                    for j in row["jobs"]:
                        if j["key"] == job["key"]:
                            j["status"] = result.get("status", "failed")
                            j["reason"] = result.get("reason", "")
                            if result.get("status") == "ok":
                                j["score"] = result["score"]

        try:
            results = runner.run_jobs(self.ws, jobs, self.n_jobs, force, lambda m: batch["log"].append(m), done)
            ordered = {row["variant"]["name"]: [results[j["key"]] for j in row["jobs"]] for row in batch["plan"]}
            export.write_report(self.ws.work / "last", [row["variant"] for row in batch["plan"]], ordered)
            batch["report"] = str(self.ws.work / "last")
        except Exception as err:  # shown in the page; the server carries on
            batch["log"].append(f"batch failed: {err!r}")
        finally:
            batch["done"] = True


class Handler(BaseHTTPRequestHandler):
    server_version = "pe-sim"
    ws: runner.Workspace
    batches: Batches
    port: int
    bind: str = "127.0.0.1"

    def log_message(self, *_):  # quiet
        pass

    # --- plumbing ---
    def _send(self, status, body: bytes, ctype="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status=HTTPStatus.OK):
        self._send(status, json.dumps(obj, separators=(",", ":")).encode())

    def _bad(self, why, status=HTTPStatus.BAD_REQUEST):
        self._json({"error": why}, status)

    def _host_ok(self) -> bool:
        return (self.headers.get("Host") or "") in {f"{h}:{self.port}" for h in ("127.0.0.1", "localhost", self.bind)}

    # --- GET ---
    def do_GET(self):
        if not self._host_ok():
            return self._bad("wrong host", HTTPStatus.FORBIDDEN)
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path == "/api/state":
                return self._json(self._state())
            if url.path == "/api/catalogue":
                return self._catalogue(q.get("code") or "")
            if url.path.startswith("/api/batch/"):
                b = self.batches.items.get(url.path.rsplit("/", 1)[1])
                return self._json(b) if b else self._bad("no such batch", HTTPStatus.NOT_FOUND)
            if url.path.startswith("/api/result/"):
                key = url.path.rsplit("/", 1)[1]
                res = self.ws.cached(key) if KEY.match(key) else None
                return self._json(res) if res else self._bad("no such run", HTTPStatus.NOT_FOUND)
            if url.path == "/api/report":
                path = self.ws.work / "last" / ("report.md" if q.get("f") != "json" else "report.json")
                return self._send(
                    HTTPStatus.OK, path.read_bytes() if path.exists() else b"", "text/plain; charset=utf-8"
                )
        except SystemExit as err:  # workspace helpers say why with SystemExit
            return self._bad(str(err))
        except Exception as err:
            return self._bad(f"{type(err).__name__}: {err}", HTTPStatus.INTERNAL_SERVER_ERROR)
        return self._static(url.path)

    def _static(self, path):
        name = "index.html" if path in ("", "/") else path.lstrip("/")
        f = (VIEWER / name).resolve()
        if not workspace.inside(f, VIEWER) or not f.is_file():
            return self._bad("not found", HTTPStatus.NOT_FOUND)
        self._send(HTTPStatus.OK, f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"))

    def _state(self) -> dict:
        ws = self.ws
        label = workspace.code_label(workspace.REPO, None)
        return {
            "days": workspace.days_with_snapshots(ws.data),
            "refs": workspace.refs(),
            "working": label,
            "configs": [p.name for p in workspace.configs(ws.data)],
            "seed": {k: (p.name if p else None) for k, p in workspace.seed_files(ws.data).items()},
            "variants": self._saved(),
            "jobs": self.batches.n_jobs,
        }

    def _catalogue(self, code):
        root, label = workspace.resolve_code(code or None, self.ws.work)
        cid = workspace.code_id(root)
        if cid not in self.batches.last_catalogue:
            self.batches.last_catalogue[cid] = runner.catalogue(str(root))
        self._json({"code": label, "settings": self.batches.last_catalogue[cid]})

    def _saved(self) -> list:
        try:
            return json.loads((self.ws.work / "variants.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    # --- POST ---
    def do_POST(self):
        if not self._host_ok() or self.headers.get("X-PE-Sim") != "1":
            return self._bad("refused", HTTPStatus.FORBIDDEN)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            path = urlparse(self.path).path
            if path == "/api/run":
                variants = [runner.Variant.of(v) for v in body.get("variants", [])]
                days = [d for d in body.get("days", []) if DAY.match(str(d))]
                if not variants or not days:
                    return self._bad("choose at least one variant and one day")
                for v in variants:  # only refs of this repo, never a folder named by a page
                    if v.code and (v.code.startswith(("/", "~", ".")) and v.code != "."):
                        return self._bad("code must be a git ref of this repo, or the working tree")
                return self._json(self.batches.start(variants, days, bool(body.get("force"))))
            if path == "/api/variants":
                (self.ws.work / "variants.json").write_text(json.dumps(body.get("variants", [])), encoding="utf-8")
                return self._json({"ok": True})
        except SystemExit as err:
            return self._bad(str(err))
        except Exception as err:
            return self._bad(f"{type(err).__name__}: {err}", HTTPStatus.INTERNAL_SERVER_ERROR)
        return self._bad("not found", HTTPStatus.NOT_FOUND)


def serve(data: Path, work: Path, port: int, n_jobs: int, host: str = "127.0.0.1") -> int:
    ws = runner.Workspace(data, work)
    Handler.ws, Handler.batches, Handler.port, Handler.bind = ws, Batches(ws, n_jobs), port, host
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"PowerEngine simulator: http://{host}:{port}/   (archive {data}, work {work}; Ctrl-C to stop)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
