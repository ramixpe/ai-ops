#!/usr/bin/env python3
"""EXAMPLE, not shipped code — a minimal Alertmanager→nettools bridge.

Localhost only. No authentication. Read SECURITY.md's unsupported-deployment
modes before exposing any listener beyond localhost. All the logic lives in
`nettools route-event`; this only moves bytes and executes the suggested argv
AS A LIST — never through a shell, so there is no quoting surface here.
"""
import json
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        routed = subprocess.run(
            ["nettools", "route-event", "--format", "json"],
            input=body, capture_output=True, timeout=30,
        )
        for decision in json.loads(routed.stdout).get("decisions", []):
            if decision.get("routable") and decision.get("suggested_command"):
                subprocess.run(
                    [*decision["suggested_command"], "--format", "json", "--notify"],
                    capture_output=True, timeout=300,
                )
        self.send_response(204)
        self.end_headers()


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 9099), Handler).serve_forever()
