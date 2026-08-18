#!/usr/bin/env python3
"""EXAMPLE, not shipped code — a minimal Alertmanager→nettools bridge.

Localhost only. No real authentication (a shared-secret header is a speed bump,
not auth). Read SECURITY.md's unsupported-deployment modes before exposing any
listener beyond localhost. All logic lives in `nettools route-event`; this only
moves bytes and executes the suggested argv AS A LIST — never through a shell.

Hardening added after the 2026-08-18 holistic review found the first draft
trivially DoS-able and CSRF-open:

  * ThreadingHTTPServer — one slow/held connection cannot starve the server.
  * Content-Length is bounded and a negative/oversized value is rejected
    (a raw `Content-Length: -1` made the first draft `read(-1)` block forever).
  * The alerts[] fan-out is capped, so one POST cannot serialize N×timeout.
  * A required shared-secret header and a same-origin check reject a browser
    driving this from a malicious page while the operator is logged in.

None of this makes it safe to expose. It makes the EXAMPLE honest about the
shape a real bridge needs.
"""
import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 256 * 1024          # a webhook payload is small; anything larger is abuse
MAX_ALERTS = 25                # cap the fan-out; more is a burst to drop, not serve
SHARED_SECRET = os.environ.get("NETTOOLS_WEBHOOK_SECRET", "")  # set one; empty = refuse all


class Handler(BaseHTTPRequestHandler):
    def _refuse(self, code: int, why: str) -> None:
        self.send_response(code)
        self.end_headers()
        self.wfile.write(why.encode())

    def do_POST(self):
        if not SHARED_SECRET or self.headers.get("X-Webhook-Secret") != SHARED_SECRET:
            return self._refuse(403, "missing or wrong X-Webhook-Secret")
        # Reject a cross-origin browser POST (CSRF): Alertmanager sends no Origin.
        if self.headers.get("Origin"):
            return self._refuse(403, "Origin header present — refusing a browser-driven request")
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return self._refuse(400, "bad Content-Length")
        if length < 0 or length > MAX_BODY:
            return self._refuse(413, "body missing, negative, or too large")

        body = self.rfile.read(length)
        routed = subprocess.run(
            ["nettools", "route-event", "--format", "json"],
            input=body, capture_output=True, timeout=30,
        )
        try:
            decisions = json.loads(routed.stdout).get("decisions", [])
        except (ValueError, AttributeError):
            return self._refuse(502, "route-event produced no parseable decision")

        for decision in decisions[:MAX_ALERTS]:
            if decision.get("routable") and decision.get("suggested_command"):
                subprocess.run(                       # argv LIST, never a shell
                    [*decision["suggested_command"], "--format", "json", "--notify"],
                    capture_output=True, timeout=120,
                )
        self.send_response(204)
        self.end_headers()


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 9099), Handler).serve_forever()
