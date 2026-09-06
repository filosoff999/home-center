from __future__ import annotations

from http import HTTPStatus
from urllib.parse import urlsplit

from .api import RuntimeRequestHandler
from .tls_status import status as tls_status


class RuntimeRequestHandlerV2(RuntimeRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/api/v1/tls/ca.crt":
            data = self.runtime.config.cluster_ca.read_bytes()
            self.send_response(HTTPStatus.OK)
            self._base_headers("application/x-pem-file", len(data), cache="public, max-age=300")
            self.send_header("Content-Disposition", 'attachment; filename="home-center-hm-dm-ca.crt"')
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/v1/tls":
            correlation_id = self._correlation_id()
            if not self._require_actor(correlation_id):
                return
            try:
                value = tls_status(self.runtime.config)
            except Exception:
                self._error(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "tls_status_unavailable",
                    "Статус TLS временно недоступен",
                    correlation_id,
                )
                return
            self._json(HTTPStatus.OK, value)
            return
        super().do_GET()
