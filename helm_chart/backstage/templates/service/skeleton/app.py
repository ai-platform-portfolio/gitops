"""A small service with an explicit readiness endpoint and JSON logs."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"service": "${{ values.name }}", "status": "ok"}).encode()
        self.send_response(200 if self.path in {"/", "/healthz"} else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, message, *args):
        print(json.dumps({"message": message % args}), flush=True)


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
