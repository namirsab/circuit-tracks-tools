#!/usr/bin/env python3
"""Serve the repo over HTTPS on the local network, for phone testing.

getUserMedia and Web MIDI require a secure context, so plain http://<lan-ip>
silently disables recording/transfer on a phone. This serves the repo root
over HTTPS with a self-signed certificate that includes the machine's LAN IP,
so the full app works after accepting the one-time certificate warning.

Usage (from the repo root):
    python3 companion/tools/serve_https.py [port]     # default 8443

Then open https://<lan-ip>:<port>/companion/ on the phone (same Wi-Fi).
The certificate lives in companion/tools/certs/ (gitignored); delete that
directory to regenerate it, e.g. after the LAN IP changes.
"""

import http.server
import socket
import ssl
import subprocess
import sys
import tempfile
from pathlib import Path

CERT_DIR = Path(__file__).resolve().parent / "certs"
CERT = CERT_DIR / "dev.crt"
KEY = CERT_DIR / "dev.key"


def lan_ip() -> str:
    # UDP connect trick: no packets sent, just picks the outbound interface
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.1.1", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def ensure_cert(ip: str) -> None:
    if CERT.exists() and KEY.exists():
        return
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    hostname = socket.gethostname().split(".")[0]
    config = f"""
[req]
distinguished_name = dn
x509_extensions = ext
prompt = no
[dn]
CN = circuit-sampler-dev
[ext]
subjectAltName = DNS:localhost, DNS:{hostname}.local, IP:127.0.0.1, IP:{ip}
"""
    with tempfile.NamedTemporaryFile("w", suffix=".cnf") as f:
        f.write(config)
        f.flush()
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-keyout",
                str(KEY),
                "-out",
                str(CERT),
                "-days",
                "365",
                "-nodes",
                "-config",
                f.name,
            ],
            check=True,
            capture_output=True,
        )
    print(f"Generated self-signed cert for {ip} in {CERT_DIR}")


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8443
    ip = lan_ip()
    ensure_cert(ip)

    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), http.server.SimpleHTTPRequestHandler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT, KEY)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)

    print(f"Serving HTTPS on 0.0.0.0:{port}")
    print(f"  On this machine: https://localhost:{port}/companion/")
    print(f"  On your phone:   https://{ip}:{port}/companion/")
    print("Accept the self-signed certificate warning once on each device.")
    server.serve_forever()


if __name__ == "__main__":
    main()
