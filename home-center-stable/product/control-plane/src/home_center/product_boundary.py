"""Fail-closed product-scope admission for Home Center runtime extensions.

Every admitted identifier must belong to an explicit Home Center taxonomy.
Unknown module categories and undeclared capabilities are rejected by default;
the policy does not infer safety from the absence of forbidden words.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable


class ProductBoundaryError(ValueError):
    """Stable, input-free rejection code for malformed boundary input."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


_TOKEN_SPLIT = re.compile(r"[.-]+")
_BOUNDARY_VALUE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,255}$")
_VERSIONED_SYMBOL = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*\.v[1-9][0-9]*$")
_HOME_CENTER_MODULE_PREFIX = "org.home-center."

# A module name is an approved infrastructure category followed by zero or
# more approved product-role qualifiers. Short names are accepted only when
# they are exactly a category; qualified names must use Home Center's module
# namespace. Adding a category or role is therefore an explicit policy change.
_HOME_CENTER_MODULE_CATEGORIES = frozenset(
    {
        "ad",
        "audit",
        "backup",
        "building",
        "certificate",
        "cluster",
        "compute",
        "directory",
        "dns",
        "external",
        "health",
        "home",
        "identity",
        "inventory",
        "module",
        "network",
        "node",
        "power",
        "reference",
        "security",
        "service",
        "storage",
        "system",
        "virtualization",
        "web",
    }
)
_HOME_CENTER_MODULE_QUALIFIERS = frozenset(
    {
        "access",
        "agent",
        "appliance",
        "automation",
        "controller",
        "gateway",
        "integration",
        "lab",
        "local",
        "manager",
        "peer",
        "provider",
        "proxy",
        "reference",
        "service",
        "stateful",
        "stateless",
        "tls",
    }
)

# Capabilities are security-relevant compatibility claims, so they are exact
# rather than prefix-based. An unknown claim must be reviewed before use.
_HOME_CENTER_CAPABILITIES = frozenset(
    {
        "ad.authentication.v1",
        "ad.directory.v1",
        "audit.v1",
        "backup.filesystem.v1",
        "backup.sqlite.v1",
        "building.climate.v1",
        "certificate.tls.v1",
        "cluster.peer-mtls.v1",
        "compute.ha.v1",
        "compute.lxc.v1",
        "compute.physical.v1",
        "compute.vm.v1",
        "directory.ad.v1",
        "dns.authoritative.v1",
        "external-access.proxy.v1",
        "health.v1",
        "identity.ad.v1",
        "identity.local.v1",
        "inventory.v1",
        "network.dhcp.v1",
        "network.dns.v1",
        "network.nfs.v1",
        "network.smb.v1",
        "network.tls.v1",
        "node.lifecycle.v1",
        "power.monitoring.v1",
        "security.audit.v1",
        "service.health.v1",
        "storage.local.v1",
        "storage.nfs.v1",
        "storage.smb.v1",
        "systemd.v1",
        "virtualization.lxc.v1",
        "virtualization.vm.v1",
        "web.https.v1",
    }
)

# Permissions and actions are closed by their first namespace. Their detailed
# grammar remains owned by the manifest contract, while this layer ensures the
# namespace itself is one of the reviewed Home Center product categories.
_HOME_CENTER_OPERATION_NAMESPACES = frozenset(
    _HOME_CENTER_MODULE_CATEGORIES
    | {
        "modules",
        "systemd",
    }
)


@dataclass(frozen=True, slots=True)
class ProductBoundaryDecision:
    allowed: bool
    code: str
    violations: tuple[str, ...]
    schema: str = "home-center.product-boundary-decision.v1"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "allowed": self.allowed,
            "code": self.code,
            "violations": list(self.violations),
        }


def _normalized(value: object) -> str:
    if not isinstance(value, str) or _BOUNDARY_VALUE.fullmatch(value) is None:
        raise ProductBoundaryError("invalid_boundary_identifier")
    return value


def _module_is_in_scope(value: object) -> bool:
    identifier = _normalized(value)
    if identifier.startswith(_HOME_CENTER_MODULE_PREFIX):
        name = identifier.removeprefix(_HOME_CENTER_MODULE_PREFIX)
    elif "." not in identifier and "-" not in identifier:
        name = identifier
    else:
        return False
    tokens = tuple(part for part in _TOKEN_SPLIT.split(name) if part)
    return bool(
        tokens
        and tokens[0] in _HOME_CENTER_MODULE_CATEGORIES
        and all(part in _HOME_CENTER_MODULE_QUALIFIERS for part in tokens[1:])
    )


def _capability_is_in_scope(value: object) -> bool:
    identifier = _normalized(value)
    return _VERSIONED_SYMBOL.fullmatch(identifier) is not None and identifier in _HOME_CENTER_CAPABILITIES


def _operation_is_in_scope(value: object) -> bool:
    identifier = _normalized(value)
    return identifier.split(".", 1)[0] in _HOME_CENTER_OPERATION_NAMESPACES


def evaluate_product_scope(
    *,
    module_id: str,
    dependencies: Iterable[str] = (),
    capabilities: Iterable[str] = (),
    permissions: Iterable[str] = (),
    actions: Iterable[str] = (),
) -> ProductBoundaryDecision:
    """Return a deterministic, default-deny decision without side effects."""

    categories: tuple[tuple[str, tuple[str, ...], Callable[[object], bool]], ...] = (
        ("module", (module_id,), _module_is_in_scope),
        ("dependency", tuple(dependencies), _module_is_in_scope),
        ("capability", tuple(capabilities), _capability_is_in_scope),
        ("permission", tuple(permissions), _operation_is_in_scope),
        ("action", tuple(actions), _operation_is_in_scope),
    )
    violations: list[str] = []
    for category, values, admitted in categories:
        for value in values:
            if not admitted(value):
                violations.append(category)

    normalized = tuple(sorted(set(violations)))
    if normalized:
        return ProductBoundaryDecision(
            allowed=False,
            code="product_scope_rejected",
            violations=normalized,
        )
    return ProductBoundaryDecision(
        allowed=True,
        code="product_scope_allowed",
        violations=(),
    )


def assert_product_scope(
    *,
    module_id: str,
    dependencies: Iterable[str] = (),
    capabilities: Iterable[str] = (),
    permissions: Iterable[str] = (),
    actions: Iterable[str] = (),
) -> None:
    decision = evaluate_product_scope(
        module_id=module_id,
        dependencies=dependencies,
        capabilities=capabilities,
        permissions=permissions,
        actions=actions,
    )
    if not decision.allowed:
        raise ProductBoundaryError(decision.code)
