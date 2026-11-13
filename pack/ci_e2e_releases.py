"""Local GitHub Releases stand-in for Windows CI auto-update tests."""

from __future__ import annotations

import argparse
import json
import ssl
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def _handler(setup: Path, sums: Path, tag: str, name: str, host: str):
    setup_bytes = setup.read_bytes()
    sums_bytes = sums.read_text(encoding="utf-8").encode("utf-8")
    release = {
        "tag_name": tag,
        "name": name or tag,
        "draft": False,
        "prerelease": "-" in tag,
        "html_url": f"https://github.com/AhmiDarrow/Hearth/releases/tag/{tag}",
        "assets": [
            {
                "name": "HearthSetup.exe",
                "browser_download_url": f"https://{host}/e2e-assets/HearthSetup.exe",
            },
            {
                "name": "SHA256SUMS.txt",
                "browser_download_url": f"https://{host}/e2e-assets/SHA256SUMS.txt",
            },
        ],
    }
    # HTTP overrides use the same host the client already requested.
    payload = json.dumps([release]).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args) -> None:
            sys.stderr.write("[e2e-releases] " + (fmt % args) + "\n")

        def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            scheme = "https" if self.server.e2e_tls else "http"  # type: ignore[attr-defined]
            listen_host = self.server.e2e_host  # type: ignore[attr-defined]
            if path.rstrip("/") in {
                "/repos/AhmiDarrow/Hearth/releases",
                "/releases",
                "/",
            } or path.endswith("/releases"):
                # Rewrite asset URLs to the scheme/host the client actually used.
                data = json.loads(payload.decode("utf-8"))
                for asset in data[0]["assets"]:
                    name = asset["name"]
                    asset["browser_download_url"] = f"{scheme}://{listen_host}/e2e-assets/{name}"
                body = json.dumps(data).encode("utf-8")
                self._send(body, "application/json")
                return
            if path in {"/e2e-assets/HearthSetup.exe", "/HearthSetup.exe"}:
                self._send(setup_bytes, "application/octet-stream")
                return
            if path in {"/e2e-assets/SHA256SUMS.txt", "/SHA256SUMS.txt"}:
                self._send(sums_bytes, "text/plain; charset=utf-8")
                return
            self._send(b"not found", "text/plain", 404)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup", required=True)
    parser.add_argument("--sums", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--name", default="")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--tls-cert", default="")
    parser.add_argument("--tls-key", default="")
    parser.add_argument("--pid-file", default="")
    args = parser.parse_args()
    setup = Path(args.setup)
    sums = Path(args.sums)
    if not setup.is_file() or not sums.is_file():
        raise SystemExit("setup or SHA256SUMS.txt is missing")

    bind_host = "0.0.0.0" if args.host in {"api.github.com", "0.0.0.0"} else args.host
    server = ThreadingHTTPServer((bind_host, args.port), _handler(setup, sums, args.tag, args.name, args.host))
    tls = bool(args.tls_cert and args.tls_key)
    server.e2e_tls = tls
    server.e2e_host = args.host if args.port in {0, 80, 443} or args.host == "api.github.com" else f"{args.host}:{server.server_address[1]}"
    if args.host == "api.github.com":
        # clients talk to https://api.github.com/... on 443
        server.e2e_host = "api.github.com"
    if tls:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(args.tls_cert, args.tls_key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    port = server.server_address[1]
    scheme = "https" if tls else "http"
    advertised = f"{scheme}://{server.e2e_host}"
    if args.host != "api.github.com" and port not in {80, 443}:
        advertised = f"{scheme}://{args.host}:{port}"
        server.e2e_host = f"{args.host}:{port}"
    print(f"E2E_RELEASES_URL={advertised}/repos/AhmiDarrow/Hearth/releases", flush=True)
    print(f"E2E_LISTEN={bind_host}:{port}", flush=True)
    if args.pid_file:
        Path(args.pid_file).write_text(str(__import__("os").getpid()), encoding="ascii")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
