"""Run the endpoint on localhost for testing: python3 serve_local.py [port]. Needs TIES_KEY in the environment.
Passes the Accept header through, so a browser at http://127.0.0.1:<port>/ sees the pages and curl sees JSON."""
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from lambda_function import lambda_handler

class H(BaseHTTPRequestHandler):
    def _go(self, method):
        n = int(self.headers.get("Content-Length") or 0)
        r = lambda_handler({"requestContext": {"http": {"method": method}}, "rawPath": self.path.split("?")[0],
                            "headers": {"accept": self.headers.get("Accept") or ""},
                            "body": self.rfile.read(n).decode("utf-8") if n else ""})
        body = r["body"].encode()
        self.send_response(r["statusCode"])
        for k, v in r["headers"].items(): self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def do_GET(self): self._go("GET")
    def do_HEAD(self): self._go("HEAD")
    def do_POST(self): self._go("POST")
    def log_message(self, *a): pass

HTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 8798), H).serve_forever()
