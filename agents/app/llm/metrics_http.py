import json
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from .client import get_llm_metrics


class _MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/metrics":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")
            return
        try:
            data = get_llm_metrics()
            payload = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode("utf-8"))


def start_metrics_server(port: int | None = None):
    port = port or int(os.getenv("LLM_METRICS_PORT", "9181"))
    server = HTTPServer(("", port), _MetricsHandler)

    thread = threading.Thread(target=server.serve_forever, daemon=True, name="llm-metrics-server")
    thread.start()
    return server
