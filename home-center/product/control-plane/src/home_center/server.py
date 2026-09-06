"""TLS server bootstrap and process lifecycle."""

from __future__ import annotations

import logging
import signal
import ssl
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from .api import PeerRequestHandler
from .api_v2 import RuntimeRequestHandlerV2
from .config import load_config
from .runtime import Runtime


LOG = logging.getLogger("home_center")

WEB_MINIMUM_TLS_VERSION = ssl.TLSVersion.TLSv1_2
PEER_MINIMUM_TLS_VERSION = ssl.TLSVersion.TLSv1_3
WEB_CERTIFICATE = Path("/etc/home-center/pki/web/current/tls.crt")
WEB_PRIVATE_KEY = Path("/etc/home-center/pki/web/current/tls.key")


class HomeCenterServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], handler: type, runtime: Runtime) -> None:
        self.runtime = runtime
        super().__init__(address, handler)


def _server_context(certificate: Path, private_key: Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(certificate), str(private_key))
    return context


def _web_context(runtime: Runtime) -> ssl.SSLContext:
    separate = WEB_CERTIFICATE.is_file() and WEB_PRIVATE_KEY.is_file()
    certificate = WEB_CERTIFICATE if separate else runtime.config.tls_certificate
    private_key = WEB_PRIVATE_KEY if separate else runtime.config.tls_private_key
    context = _server_context(certificate, private_key)
    context.minimum_version = WEB_MINIMUM_TLS_VERSION
    return context


def _peer_context(runtime: Runtime) -> ssl.SSLContext:
    context = _server_context(runtime.config.tls_certificate, runtime.config.tls_private_key)
    context.minimum_version = PEER_MINIMUM_TLS_VERSION
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_verify_locations(cafile=str(runtime.config.cluster_ca))
    return context


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    config = load_config()
    runtime = Runtime(config)
    runtime.start()
    web = HomeCenterServer(config.web_bind, RuntimeRequestHandlerV2, runtime)
    peer = HomeCenterServer(config.peer_bind, PeerRequestHandler, runtime)
    web.socket = _web_context(runtime).wrap_socket(web.socket, server_side=True)
    peer.socket = _peer_context(runtime).wrap_socket(peer.socket, server_side=True)

    stop = threading.Event()

    def request_stop(signum: int, _frame: object) -> None:
        LOG.info("shutdown requested signal=%s", signum)
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    web_thread = threading.Thread(target=web.serve_forever, name="home-center-web", daemon=True)
    peer_thread = threading.Thread(target=peer.serve_forever, name="home-center-peer", daemon=True)
    web_thread.start()
    peer_thread.start()
    LOG.info("Home Center started node=%s role=%s web_tls=%s", config.node_name, config.role, "separate" if WEB_CERTIFICATE.is_file() else "legacy-fallback")
    stop.wait()
    web.shutdown()
    peer.shutdown()
    web.server_close()
    peer.server_close()
    runtime.stop()
    LOG.info("Home Center stopped")
