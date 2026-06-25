from __future__ import annotations

import argparse
import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .client import GovMapProxyClient, GovMapProxyError
from .config import GovMapSettings


ROOT = Path(__file__).resolve().parent.parent
PUBLIC_DIR = ROOT / "public"


class GovMapRequestHandler(BaseHTTPRequestHandler):
    server_version = "GovMapProxy/1.0"

    @property
    def settings(self) -> GovMapSettings:
        return self.server.settings  # type: ignore[attr-defined]

    @property
    def client(self) -> GovMapProxyClient:
        return self.server.client  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self.send_json({"ok": True})
            return
        if parsed.path == "/api/govmap/config":
            self.send_json(self.settings.public_dict())
            return
        self.serve_static(parsed.path)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        operation = self.operation_from_path(parsed.path)
        if not operation:
            self.send_json(
                {"error": "not_found", "message": "Unknown endpoint"},
                status=HTTPStatus.NOT_FOUND,
            )
            return

        try:
            payload = self.read_json_body()
            response = self.client.send(operation, payload)
            self.send_bytes(
                response.body,
                status=response.status,
                content_type=response.headers.get("Content-Type", "application/octet-stream"),
            )
        except GovMapProxyError as error:
            self.send_json(
                {"error": error.code, "message": str(error)},
                status=error.status,
            )
        except json.JSONDecodeError:
            self.send_json(
                {"error": "invalid_json", "message": "Request body must be valid JSON"},
                status=HTTPStatus.BAD_REQUEST,
            )

    def operation_from_path(self, path: str) -> str:
        prefix = "/api/govmap/"
        if not path.startswith(prefix):
            return ""
        operation = path.removeprefix(prefix)
        if operation in {"search", "spatial", "lookup", "proxy"}:
            return operation
        return ""

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length == 0:
            return {}
        raw_body = self.rfile.read(length)
        return json.loads(raw_body.decode("utf-8"))

    def serve_static(self, path: str) -> None:
        if path in {"", "/"}:
            path = "/index.html"
        relative = path.lstrip("/")
        candidate = (PUBLIC_DIR / relative).resolve()
        if not str(candidate).startswith(str(PUBLIC_DIR.resolve())) or not candidate.is_file():
            self.send_json(
                {"error": "not_found", "message": "Unknown endpoint"},
                status=HTTPStatus.NOT_FOUND,
            )
            return

        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_bytes(candidate.read_bytes(), content_type=content_type)

    def send_json(self, payload: dict[str, Any], status: int = HTTPStatus.OK) -> None:
        self.send_bytes(
            json.dumps(payload, indent=2).encode("utf-8"),
            status=status,
            content_type="application/json; charset=utf-8",
        )

    def send_bytes(
        self,
        body: bytes,
        status: int = HTTPStatus.OK,
        content_type: str = "application/octet-stream",
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def send_cors_headers(self) -> None:
        request_origin = self.headers.get("Origin")
        if not request_origin:
            return
        if request_origin in self.settings.cors_origins:
            self.send_header("Access-Control-Allow-Origin", request_origin)
            self.send_header("Vary", "Origin")

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")


class GovMapHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], handler_class: type[BaseHTTPRequestHandler]):
        self.settings = GovMapSettings.from_env()
        self.client = GovMapProxyClient(self.settings)
        super().__init__(server_address, handler_class)


def run(host: str, port: int) -> None:
    server = GovMapHTTPServer((host, port), GovMapRequestHandler)
    print(f"GovMap proxy listening on http://{host}:{port}")
    print(f"Using browser REST origin: {server.settings.origin or '(not configured)'}")
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the GovMap REST proxy")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=8000, type=int)
    args = parser.parse_args()
    run(args.host, args.port)


if __name__ == "__main__":
    main()
