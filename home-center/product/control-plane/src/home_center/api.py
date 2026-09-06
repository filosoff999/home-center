"""Versioned HTTPS API and static Web UI handler."""

from __future__ import annotations

import json
import logging
import mimetypes
import re
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .actions import (
    ActionIdempotencyConflict,
    ActionNotFound,
    ActionPermissionDenied,
    ActionRequestError,
    ActionTargetConflict,
)
from .auth import SessionManager
from .local_admin_auth import LocalAdminAuthError
from .runtime import Runtime
from .util import utc_now


LOG = logging.getLogger("home_center.api")
STATIC_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")


class RuntimeRequestHandler(BaseHTTPRequestHandler):
    server_version = "HomeCenter"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    @property
    def runtime(self) -> Runtime:
        return self.server.runtime  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: object) -> None:
        LOG.info("request remote=%s message=%s", self.client_address[0], fmt % args)

    def _correlation_id(self) -> str:
        supplied = self.headers.get("X-Correlation-ID", "")
        if re.fullmatch(r"[A-Za-z0-9._:-]{1,96}", supplied):
            return supplied
        return str(uuid.uuid4())

    def _base_headers(self, content_type: str, length: int, *, cache: str = "no-store") -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", cache)
        self.send_header("Strict-Transport-Security", "max-age=31536000")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")

    def _json(self, status: int, value: Any, *, cookie: str | None = None) -> None:
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        self.send_response(status)
        self._base_headers("application/json; charset=utf-8", len(body))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, code: str, message: str, correlation_id: str) -> None:
        self._json(
            status,
            {
                "schema": "home-center.error.v1",
                "error": {"code": code, "message": message},
                "correlation_id": correlation_id,
            },
        )

    def _actor(self) -> str | None:
        return self.runtime.sessions.actor_from_headers(self.headers.get("Cookie"))

    def _require_actor(self, correlation_id: str) -> str | None:
        actor = self._actor()
        if not actor:
            self._error(HTTPStatus.UNAUTHORIZED, "authentication_required", "Требуется вход", correlation_id)
        return actor

    def do_GET(self) -> None:  # noqa: N802
        correlation_id = self._correlation_id()
        parsed = urlsplit(self.path)
        path = parsed.path
        if path == "/healthz":
            self._json(200, {"schema": "home-center.health.v1", "status": "ok", "version": __version__, "node_id": self.runtime.config.node_id, "observed_at": utc_now()})
            return
        if path == "/readyz":
            ready, reasons = self.runtime.ready()
            self._json(200 if ready else 503, {"schema": "home-center.readiness.v1", "status": "ready" if ready else "not_ready", "reasons": reasons, "version": __version__, "node_id": self.runtime.config.node_id, "observed_at": utc_now()})
            return
        if path == "/api/v1/meta":
            self._json(200, {"schema": "home-center.meta.v1", "product": "Home Center", "version": __version__, "node_name": self.runtime.config.node_name, "role": self.runtime.config.role})
            return
        if path in {"/", "/index.html"}:
            self._static("index.html")
            return
        if path.startswith("/static/"):
            self._static(path.removeprefix("/static/"))
            return
        actor = self._require_actor(correlation_id)
        if not actor:
            return
        if path == "/api/v1/session":
            self._json(200, {"schema": "home-center.session.v1", "authenticated": True, "actor": actor})
        elif path in {"/api/v1/overview", "/api/v1/cluster"}:
            self._json(200, self.runtime.overview())
        elif path == "/api/v1/nodes":
            self._json(200, {"schema": "home-center.nodes.v1", "items": self.runtime.store.nodes()})
        elif path == "/api/v1/actions":
            self._json(200, self.runtime.actions.catalog())
        elif path == "/api/v1/jobs":
            self._json(200, {"schema": "home-center.jobs.v1", "items": self.runtime.store.jobs(100)})
        elif path == "/api/v1/audit":
            params = parse_qs(parsed.query)
            try:
                limit = int(params.get("limit", ["100"])[0])
            except ValueError:
                limit = 100
            self._json(200, {"schema": "home-center.audit-list.v1", "items": self.runtime.store.audit_events(limit), "chain_head": self.runtime.store.verify_audit_chain()})
        elif path == "/api/v1/desired-state":
            self._json(200, {"schema": "home-center.desired-state-list.v1", "items": self.runtime.store.desired_state()})
        elif path == "/api/v1/deployment-profile":
            self._json(200, self.runtime.profile)
        elif path == "/api/v1/backups":
            self._json(200, {"schema": "home-center.backup-list.v1", "items": self.runtime.backup_inventory()})
        else:
            self._error(404, "not_found", "Ресурс не найден", correlation_id)

    def do_POST(self) -> None:  # noqa: N802
        correlation_id = self._correlation_id()
        path = urlsplit(self.path).path
        if path == "/api/v1/session":
            self._login(correlation_id)
            return
        if path == "/api/v1/session/logout":
            self._json(200, {"schema": "home-center.session.v1", "authenticated": False}, cookie=SessionManager.expired_cookie())
            return
        actor = self._require_actor(correlation_id)
        if not actor:
            return
        if path.startswith("/api/v1/actions/"):
            action_id = path.removeprefix("/api/v1/actions/")
            try:
                body = self._read_json(max_bytes=8192)
                job, replay = self.runtime.actions.run(
                    actor=actor,
                    action_id=action_id,
                    request=body,
                    correlation_id=correlation_id,
                )
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                if isinstance(exc, ActionNotFound):
                    status, code, message = HTTPStatus.NOT_FOUND, exc.code, "Действие не зарегистрировано"
                elif isinstance(exc, ActionPermissionDenied):
                    status, code, message = HTTPStatus.FORBIDDEN, exc.code, "Недостаточно прав"
                elif isinstance(exc, ActionIdempotencyConflict):
                    status, code, message = HTTPStatus.CONFLICT, exc.code, "Ключ идемпотентности уже использован"
                elif isinstance(exc, ActionTargetConflict):
                    status, code, message = HTTPStatus.CONFLICT, exc.code, "Целевой узел не является локальным"
                else:
                    status, code, message = HTTPStatus.BAD_REQUEST, "invalid_action_request", "Некорректный запрос действия"
                safe_target = action_id if re.fullmatch(r"[a-z][a-z0-9.-]{0,127}", action_id) else "invalid-action"
                self.runtime.store.audit(
                    actor=actor,
                    action="action.request",
                    target=safe_target,
                    outcome="denied",
                    correlation_id=correlation_id,
                    details={"reason": code},
                )
                self._error(status, code, message, correlation_id)
                return
            except Exception:
                LOG.exception("typed action request failed")
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "action_internal_error", "Внутренняя ошибка действия", correlation_id)
                return
            self._json(
                HTTPStatus.OK,
                {
                    "schema": "home-center.action-result.v1",
                    "idempotent_replay": replay,
                    "job": job,
                },
            )
            return
        self._error(405, "typed_action_not_available", "Изменение не входит в эту сертифицированную версию", correlation_id)

    def do_PUT(self) -> None:  # noqa: N802
        self._reject_mutation()

    def do_PATCH(self) -> None:  # noqa: N802
        self._reject_mutation()

    def do_DELETE(self) -> None:  # noqa: N802
        self._reject_mutation()

    def _reject_mutation(self) -> None:
        correlation_id = self._correlation_id()
        if not self._require_actor(correlation_id):
            return
        self._error(405, "typed_action_not_available", "Изменение не входит в эту сертифицированную версию", correlation_id)

    def _read_json(self, max_bytes: int = 4096) -> dict[str, Any]:
        if self.headers.get_content_type() != "application/json":
            raise ValueError("content type")
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValueError("content length")
        length = int(raw_length)
        if length < 1 or length > max_bytes:
            raise ValueError("body size")
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError("object required")
        return value

    def _login(self, correlation_id: str) -> None:
        remote = self.client_address[0]
        if not self.runtime.login_limiter.allow(remote):
            self._error(429, "rate_limited", "Слишком много попыток входа", correlation_id)
            return
        try:
            body = self._read_json()
            if set(body) != {"username", "password"}:
                raise ValueError("invalid login shape")
            username = body.get("username")
            password = body.get("password")
            if not isinstance(username, str) or not isinstance(password, str):
                raise ValueError("invalid login types")
        except (ValueError, TypeError, json.JSONDecodeError):
            self.runtime.store.audit(actor=f"network:{remote}", action="session.login", target=self.runtime.config.node_id, outcome="denied", correlation_id=correlation_id, details={"reason": "invalid_credentials"})
            self._error(401, "invalid_credentials", "Неверные учётные данные", correlation_id)
            return

        try:
            canonical_username = self.runtime.local_admin.authenticate(username, password)
        except LocalAdminAuthError:
            LOG.error("local administrator authentication backend unavailable")
            self.runtime.store.audit(actor=f"network:{remote}", action="session.login", target=self.runtime.config.node_id, outcome="denied", correlation_id=correlation_id, details={"reason": "authentication_unavailable"})
            self._error(503, "authentication_unavailable", "Служба аутентификации недоступна", correlation_id)
            return
        if canonical_username is None:
            self.runtime.store.audit(actor=f"network:{remote}", action="session.login", target=self.runtime.config.node_id, outcome="denied", correlation_id=correlation_id, details={"reason": "invalid_credentials"})
            self._error(401, "invalid_credentials", "Неверные учётные данные", correlation_id)
            return

        self.runtime.login_limiter.clear(remote)
        actor = f"local-admin:{canonical_username}"
        session, expires = self.runtime.sessions.new_session(actor)
        self.runtime.store.audit(actor=actor, action="session.login", target=self.runtime.config.node_id, outcome="accepted", correlation_id=correlation_id, details={"remote_address": remote})
        self._json(200, {"schema": "home-center.session.v1", "authenticated": True, "actor": actor, "expires_at_epoch": expires}, cookie=SessionManager.cookie(session, expires - int(time.time())))

    def _static(self, name: str) -> None:
        if not STATIC_NAME.fullmatch(name):
            self._error(404, "not_found", "Ресурс не найден", self._correlation_id())
            return
        root = self.runtime.config.web_root.resolve()
        path = (root / name).resolve()
        if path.parent != root or not path.is_file():
            self._error(404, "not_found", "Ресурс не найден", self._correlation_id())
            return
        data = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        self.send_response(200)
        self._base_headers(content_type, len(data), cache="no-cache")
        self.end_headers()
        self.wfile.write(data)


class PeerRequestHandler(BaseHTTPRequestHandler):
    server_version = "HomeCenterPeer"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    @property
    def runtime(self) -> Runtime:
        return self.server.runtime  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: object) -> None:
        LOG.info("peer remote=%s message=%s", self.client_address[0], fmt % args)

    def do_GET(self) -> None:  # noqa: N802
        if not self._peer_identity_matches():
            self.send_error(403)
            return
        if urlsplit(self.path).path != "/internal/v1/node":
            self.send_error(404)
            return
        value = {"schema": "home-center.peer-node.v1", "cluster_id": self.runtime.config.cluster_id, "capability": self.runtime.reconciler.local_capability()}
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _peer_identity_matches(self) -> bool:
        certificate = self.connection.getpeercert()  # type: ignore[attr-defined]
        subjects = dict(item[0] for item in certificate.get("subject", ()))
        return subjects.get("commonName") == self.runtime.config.peer.certificate_name
