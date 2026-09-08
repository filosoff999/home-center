"""Home Center module admission: structural validation plus product-scope policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, overload

from .module_manifest import ModuleManifestIdentity, load_manifest, validate_manifest
from .product_boundary import ProductBoundaryDecision, ProductBoundaryError, evaluate_product_scope


class ModuleAdmissionError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class AdmittedModule:
    identity: ModuleManifestIdentity
    boundary: ProductBoundaryDecision


@dataclass(frozen=True, slots=True)
class ModuleInstallAdmission:
    """The bounded product-scope facts available before artifact retrieval."""

    module_id: str


@dataclass(frozen=True, slots=True)
class AdmittedModuleInstall:
    module_id: str
    boundary: ProductBoundaryDecision


def _evaluate_boundary(
    *,
    module_id: str,
    dependencies: tuple[str, ...] = (),
    capabilities: tuple[str, ...] = (),
    permissions: tuple[str, ...] = (),
    actions: tuple[str, ...] = (),
) -> ProductBoundaryDecision:
    try:
        boundary = evaluate_product_scope(
            module_id=module_id,
            dependencies=dependencies,
            capabilities=capabilities,
            permissions=permissions,
            actions=actions,
        )
    except ProductBoundaryError as exc:
        raise ModuleAdmissionError("product_scope_rejected") from exc
    if not boundary.allowed:
        raise ModuleAdmissionError(boundary.code)
    return boundary


@overload
def validate_module_admission(value: dict[str, Any]) -> AdmittedModule: ...


@overload
def validate_module_admission(value: ModuleInstallAdmission) -> AdmittedModuleInstall: ...


def validate_module_admission(
    value: dict[str, Any] | ModuleInstallAdmission,
) -> AdmittedModule | AdmittedModuleInstall:
    """Apply the same fail-closed scope gate before planning and staging.

    Install planning has only a module identifier. Artifact admission repeats
    the check over the complete, structurally validated manifest.
    """

    if isinstance(value, ModuleInstallAdmission):
        boundary = _evaluate_boundary(module_id=value.module_id)
        return AdmittedModuleInstall(module_id=value.module_id, boundary=boundary)

    identity = validate_manifest(value)
    dependencies = tuple(item["id"] for item in value["dependencies"])
    capabilities = tuple(value["capabilities"])
    permissions = tuple(value["permissions"])
    actions = tuple(item["id"] for item in value["actions"])
    boundary = _evaluate_boundary(
        module_id=identity.module_id,
        dependencies=dependencies,
        capabilities=capabilities,
        permissions=permissions,
        actions=actions,
    )
    return AdmittedModule(identity=identity, boundary=boundary)


def load_and_validate_module_admission(payload: bytes) -> AdmittedModule:
    return validate_module_admission(load_manifest(payload))
