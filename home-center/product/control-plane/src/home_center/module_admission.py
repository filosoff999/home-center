"""Deterministic, side-effect-free admission planning for Home Center modules."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from home_center.module_manifest import ModuleManifestError, validate_manifest


MAX_ADMISSION_REQUEST_BYTES = 1024 * 1024
MAX_CANDIDATES = 128
MAX_INSTALLED_MODULES = 128
MAX_REQUESTED_MODULES = 32
MAX_CAPABILITIES = 256
PRODUCTION_ACTIVATION_ENABLED = False

SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
MODULE_ID = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,126}[a-z0-9])?$")
SYMBOLIC_ID = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$")


class ModuleAdmissionError(ValueError):
    """A bounded planner failure that never includes untrusted input."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, order=True)
class ModuleVersion:
    module_id: str
    version: str


@dataclass(frozen=True)
class InstalledModuleState:
    module_id: str
    version: str
    conflicts: tuple[str, ...]


@dataclass(frozen=True)
class ModuleInstallStep:
    module_id: str
    version: str
    artifact_sha256: str


@dataclass(frozen=True)
class ModuleAdmissionPlan:
    requested_modules: tuple[ModuleVersion, ...]
    install_order: tuple[ModuleInstallStep, ...]
    satisfied_by_installed: tuple[ModuleVersion, ...]
    required_capabilities: tuple[str, ...]
    requested_permissions: tuple[str, ...]
    production_activation_enabled: bool = PRODUCTION_ACTIVATION_ENABLED

    def to_dict(self) -> dict[str, Any]:
        """Return the closed canonical result contract."""

        return {
            "schema": "home-center.module-admission-result.v1",
            "status": "admitted",
            "requested_modules": [
                {"id": item.module_id, "version": item.version} for item in self.requested_modules
            ],
            "install_order": [
                {
                    "id": item.module_id,
                    "version": item.version,
                    "artifact_sha256": item.artifact_sha256,
                }
                for item in self.install_order
            ],
            "satisfied_by_installed": [
                {"id": item.module_id, "version": item.version} for item in self.satisfied_by_installed
            ],
            "required_capabilities": list(self.required_capabilities),
            "requested_permissions": list(self.requested_permissions),
            "production_activation_enabled": self.production_activation_enabled,
        }


def _duplicate_rejecting_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ModuleAdmissionError("planner_duplicate_key")
        value[key] = item
    return value


def _reject_float(_: str) -> None:
    raise ModuleAdmissionError("planner_float_rejected")


def _reject_constant(_: str) -> None:
    raise ModuleAdmissionError("planner_constant_rejected")


def load_admission_request(payload: bytes) -> dict[str, Any]:
    """Decode bounded JSON while rejecting ambiguous representations."""

    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_ADMISSION_REQUEST_BYTES:
        raise ModuleAdmissionError("planner_request_size_rejected")
    if payload.startswith(b"\xef\xbb\xbf"):
        raise ModuleAdmissionError("planner_request_bom_rejected")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ModuleAdmissionError("planner_request_encoding_rejected") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_duplicate_rejecting_object,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except ModuleAdmissionError:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ModuleAdmissionError("planner_request_json_rejected") from exc
    if not isinstance(value, dict):
        raise ModuleAdmissionError("planner_request_root_rejected")
    return value


def _object(value: Any, keys: set[str], code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ModuleAdmissionError(code)
    return value


def _array(value: Any, code: str, *, minimum: int = 0, maximum: int) -> list[Any]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ModuleAdmissionError(code)
    return value


def _string(value: Any, pattern: re.Pattern[str], code: str, *, maximum: int = 128) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum or pattern.fullmatch(value) is None:
        raise ModuleAdmissionError(code)
    return value


def _semver(value: Any, code: str) -> tuple[int, int, int]:
    text = _string(value, SEMVER, code, maximum=64)
    return tuple(int(part) for part in text.split("."))  # type: ignore[return-value]


def _in_interval(version: str, minimum: str, maximum_exclusive: str) -> bool:
    parsed = _semver(version, "planner_internal_version_rejected")
    return _semver(minimum, "planner_internal_version_rejected") <= parsed < _semver(
        maximum_exclusive, "planner_internal_version_rejected"
    )


def _validate_environment(value: Any) -> tuple[str, str, str, frozenset[str]]:
    environment = _object(
        value,
        {"home_center_version", "architecture", "operating_system", "capabilities"},
        "planner_environment_rejected",
    )
    home_center_version = _string(
        environment["home_center_version"], SEMVER, "planner_home_center_version_rejected", maximum=64
    )
    architecture = _string(environment["architecture"], SYMBOLIC_ID, "planner_architecture_rejected")
    operating_system = _string(
        environment["operating_system"], SYMBOLIC_ID, "planner_operating_system_rejected"
    )
    raw_capabilities = _array(
        environment["capabilities"], "planner_capabilities_rejected", maximum=MAX_CAPABILITIES
    )
    capabilities = [
        _string(item, SYMBOLIC_ID, "planner_capabilities_rejected") for item in raw_capabilities
    ]
    if len(capabilities) != len(set(capabilities)):
        raise ModuleAdmissionError("planner_capabilities_rejected")
    return home_center_version, architecture, operating_system, frozenset(capabilities)


def _validate_installed(value: Any) -> dict[str, InstalledModuleState]:
    installed_items = _array(
        value, "planner_installed_modules_rejected", maximum=MAX_INSTALLED_MODULES
    )
    installed: dict[str, InstalledModuleState] = {}
    for value_item in installed_items:
        item = _object(
            value_item, {"id", "version", "conflicts"}, "planner_installed_module_rejected"
        )
        module_id = _string(item["id"], MODULE_ID, "planner_installed_module_rejected")
        version = _string(item["version"], SEMVER, "planner_installed_module_rejected", maximum=64)
        raw_conflicts = _array(
            item["conflicts"], "planner_installed_module_rejected", maximum=MAX_CANDIDATES
        )
        conflicts = tuple(
            _string(conflict, MODULE_ID, "planner_installed_module_rejected")
            for conflict in raw_conflicts
        )
        if len(conflicts) != len(set(conflicts)) or module_id in conflicts:
            raise ModuleAdmissionError("planner_installed_module_rejected")
        if module_id in installed:
            raise ModuleAdmissionError("planner_installed_module_duplicate")
        installed[module_id] = InstalledModuleState(module_id, version, conflicts)
    return installed


def _validate_requested(value: Any) -> tuple[ModuleVersion, ...]:
    requested_items = _array(
        value,
        "planner_requested_modules_rejected",
        minimum=1,
        maximum=MAX_REQUESTED_MODULES,
    )
    requested: list[ModuleVersion] = []
    seen: set[str] = set()
    for value_item in requested_items:
        item = _object(value_item, {"id", "version"}, "planner_requested_module_rejected")
        module_id = _string(item["id"], MODULE_ID, "planner_requested_module_rejected")
        version = _string(item["version"], SEMVER, "planner_requested_module_rejected", maximum=64)
        if module_id in seen:
            raise ModuleAdmissionError("planner_requested_module_duplicate")
        seen.add(module_id)
        requested.append(ModuleVersion(module_id, version))
    return tuple(sorted(requested))


def _validate_candidates(
    value: Any,
) -> tuple[dict[str, list[dict[str, Any]]], dict[tuple[str, str], dict[str, Any]]]:
    candidate_items = _array(
        value, "planner_candidates_rejected", minimum=1, maximum=MAX_CANDIDATES
    )
    candidates: dict[str, list[dict[str, Any]]] = {}
    identities: dict[tuple[str, str], dict[str, Any]] = {}
    for item in candidate_items:
        if not isinstance(item, dict):
            raise ModuleAdmissionError("planner_manifest_rejected")
        try:
            identity = validate_manifest(item)
        except ModuleManifestError as exc:
            raise ModuleAdmissionError("planner_manifest_rejected") from exc
        key = (identity.module_id, identity.version)
        if key in identities:
            raise ModuleAdmissionError("planner_candidate_duplicate")
        identities[key] = item
        candidates.setdefault(identity.module_id, []).append(item)
    for versions in candidates.values():
        versions.sort(key=lambda manifest: _semver(manifest["module"]["version"], "planner_manifest_rejected"))
    return candidates, identities


def plan_module_admission(request: dict[str, Any]) -> ModuleAdmissionPlan:
    """Produce an immutable install-only plan without I/O or lifecycle authority."""

    root = _object(
        request,
        {"schema", "environment", "installed_modules", "candidates", "requested_modules"},
        "planner_request_fields_rejected",
    )
    if root["schema"] != "home-center.module-admission-request.v1":
        raise ModuleAdmissionError("planner_request_schema_rejected")

    home_center_version, architecture, operating_system, capabilities = _validate_environment(
        root["environment"]
    )
    installed = _validate_installed(root["installed_modules"])
    requested = _validate_requested(root["requested_modules"])
    candidates, identities = _validate_candidates(root["candidates"])

    selected: dict[str, dict[str, Any]] = {}
    satisfied: dict[str, str] = {}
    visiting: set[str] = set()
    visited: set[str] = set()

    def check_compatibility(manifest: dict[str, Any]) -> None:
        compatibility = manifest["compatibility"]
        home_center = compatibility["home_center"]
        if not _in_interval(
            home_center_version, home_center["minimum"], home_center["maximum_exclusive"]
        ):
            raise ModuleAdmissionError("planner_home_center_incompatible")
        if architecture not in compatibility["architectures"]:
            raise ModuleAdmissionError("planner_architecture_incompatible")
        if operating_system not in compatibility["operating_systems"]:
            raise ModuleAdmissionError("planner_operating_system_incompatible")
        if not set(manifest["capabilities"]).issubset(capabilities):
            raise ModuleAdmissionError("planner_capability_missing")

    def choose_candidate(
        module_id: str,
        minimum: str,
        maximum_exclusive: str,
        *,
        optional: bool,
    ) -> dict[str, Any] | None:
        if module_id in installed:
            if _in_interval(installed[module_id].version, minimum, maximum_exclusive):
                satisfied[module_id] = installed[module_id].version
                return None
            raise ModuleAdmissionError("planner_dependency_version_incompatible")
        available = candidates.get(module_id, [])
        matching = [
            item
            for item in available
            if _in_interval(item["module"]["version"], minimum, maximum_exclusive)
        ]
        if not available and optional:
            return None
        if not matching:
            code = "planner_dependency_missing" if not available else "planner_dependency_version_incompatible"
            raise ModuleAdmissionError(code)
        if len(matching) != 1:
            raise ModuleAdmissionError("planner_candidate_ambiguous")
        return matching[0]

    def visit(manifest: dict[str, Any]) -> None:
        module_id = manifest["module"]["id"]
        version = manifest["module"]["version"]
        already_selected = selected.get(module_id)
        if already_selected is not None and already_selected["module"]["version"] != version:
            raise ModuleAdmissionError("planner_candidate_resolution_conflict")
        if module_id in visiting:
            raise ModuleAdmissionError("planner_dependency_cycle")
        if module_id in visited:
            return
        selected[module_id] = manifest
        check_compatibility(manifest)
        visiting.add(module_id)
        for dependency in sorted(manifest["dependencies"], key=lambda item: item["id"]):
            dependency_id = dependency["id"]
            if dependency["optional"]:
                continue
            if dependency_id in selected:
                selected_version = selected[dependency_id]["module"]["version"]
                if not _in_interval(
                    selected_version,
                    dependency["minimum_version"],
                    dependency["maximum_version_exclusive"],
                ):
                    raise ModuleAdmissionError("planner_dependency_version_incompatible")
                if dependency_id in visiting:
                    raise ModuleAdmissionError("planner_dependency_cycle")
                continue
            dependency_manifest = choose_candidate(
                dependency_id,
                dependency["minimum_version"],
                dependency["maximum_version_exclusive"],
                optional=dependency["optional"],
            )
            if dependency_manifest is not None:
                visit(dependency_manifest)
        visiting.remove(module_id)
        visited.add(module_id)

    for target in requested:
        if target.module_id in installed:
            raise ModuleAdmissionError("planner_requested_module_already_installed")
        manifest = identities.get((target.module_id, target.version))
        if manifest is None:
            raise ModuleAdmissionError("planner_requested_candidate_missing")
        visit(manifest)

    for manifest in selected.values():
        for dependency in manifest["dependencies"]:
            dependency_id = dependency["id"]
            if dependency_id in installed:
                if not _in_interval(
                    installed[dependency_id].version,
                    dependency["minimum_version"],
                    dependency["maximum_version_exclusive"],
                ):
                    raise ModuleAdmissionError("planner_dependency_version_incompatible")
                satisfied[dependency_id] = installed[dependency_id].version
            elif dependency_id in selected:
                if not _in_interval(
                    selected[dependency_id]["module"]["version"],
                    dependency["minimum_version"],
                    dependency["maximum_version_exclusive"],
                ):
                    raise ModuleAdmissionError("planner_dependency_version_incompatible")
            elif not dependency["optional"]:
                raise ModuleAdmissionError("planner_dependency_missing")

    order: list[str] = []
    ordering: set[str] = set()
    ordered: set[str] = set()

    def add_in_dependency_order(module_id: str) -> None:
        if module_id in ordering:
            raise ModuleAdmissionError("planner_dependency_cycle")
        if module_id in ordered:
            return
        ordering.add(module_id)
        for dependency in sorted(selected[module_id]["dependencies"], key=lambda item: item["id"]):
            if dependency["id"] in selected:
                add_in_dependency_order(dependency["id"])
        ordering.remove(module_id)
        ordered.add(module_id)
        order.append(module_id)

    for target in requested:
        add_in_dependency_order(target.module_id)

    present_ids = set(selected).union(installed)
    for module_id, manifest in selected.items():
        if set(manifest["conflicts"]).intersection(present_ids - {module_id}):
            raise ModuleAdmissionError("planner_module_conflict")
    selected_ids = set(selected)
    for state in installed.values():
        if set(state.conflicts).intersection(selected_ids):
            raise ModuleAdmissionError("planner_module_conflict")

    required_capabilities = tuple(
        sorted({capability for manifest in selected.values() for capability in manifest["capabilities"]})
    )
    requested_permissions = tuple(
        sorted({permission for manifest in selected.values() for permission in manifest["permissions"]})
    )
    install_order = tuple(
        ModuleInstallStep(
            module_id,
            selected[module_id]["module"]["version"],
            selected[module_id]["artifact"]["sha256"],
        )
        for module_id in order
    )
    satisfied_by_installed = tuple(
        ModuleVersion(module_id, version) for module_id, version in sorted(satisfied.items())
    )
    return ModuleAdmissionPlan(
        requested_modules=requested,
        install_order=install_order,
        satisfied_by_installed=satisfied_by_installed,
        required_capabilities=required_capabilities,
        requested_permissions=requested_permissions,
    )


def load_and_plan_module_admission(payload: bytes) -> ModuleAdmissionPlan:
    return plan_module_admission(load_admission_request(payload))
