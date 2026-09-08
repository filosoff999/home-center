"""Plan-only Home Lab environments backed by the 0.14 compute framework."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from .compute_framework import (
    ComputePlan,
    ComputePlanner,
    ComputeProviderDescriptor,
    ComputeResourceKind,
    ComputeResourceRequest,
)


LAB_ID = re.compile(r"^[a-z][a-z0-9_.:-]{1,127}$")


class HomeLabError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class HomeLabPlanState(StrEnum):
    PLANNED = "planned"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class HomeLabTemplate:
    template_id: str
    resource_kind: ComputeResourceKind
    vcpu: int
    memory_mib: int
    disk_gib: int

    def __post_init__(self) -> None:
        if LAB_ID.fullmatch(self.template_id) is None:
            raise HomeLabError("invalid_template_id")
        ComputeResourceRequest(
            resource_id="template-validation",
            kind=self.resource_kind,
            vcpu=self.vcpu,
            memory_mib=self.memory_mib,
            disk_gib=self.disk_gib,
        )


@dataclass(frozen=True, slots=True)
class HomeLabQuota:
    max_instances: int
    max_vcpu: int
    max_memory_mib: int
    max_storage_gib: int

    def __post_init__(self) -> None:
        bounds = (
            (self.max_instances, 1, 10_000, "invalid_lab_instance_quota"),
            (self.max_vcpu, 1, 65_536, "invalid_lab_cpu_quota"),
            (self.max_memory_mib, 256, 16_777_216, "invalid_lab_memory_quota"),
            (self.max_storage_gib, 1, 16_777_216, "invalid_lab_storage_quota"),
        )
        for value, minimum, maximum, code in bounds:
            if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
                raise HomeLabError(code)


@dataclass(frozen=True, slots=True)
class HomeLabUsage:
    instances: int
    vcpu: int
    memory_mib: int
    storage_gib: int

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in (self.instances, self.vcpu, self.memory_mib, self.storage_gib)
        ):
            raise HomeLabError("invalid_lab_usage")


@dataclass(frozen=True, slots=True)
class HomeLabPlan:
    lab_id: str
    template_id: str
    state: HomeLabPlanState
    blockers: tuple[str, ...]
    compute_plan: ComputePlan | None
    production_mutation_enabled: bool = False
    schema: str = field(default="home-center.home-lab-plan.v1", init=False)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "lab_id": self.lab_id,
            "template_id": self.template_id,
            "state": self.state.value,
            "blockers": list(self.blockers),
            "compute_plan": None if self.compute_plan is None else self.compute_plan.to_dict(),
            "production_mutation_enabled": self.production_mutation_enabled,
        }


class HomeLabPlanner:
    """Compose quotas and compute facts into a deterministic environment plan."""

    def __init__(self, compute_planner: ComputePlanner | None = None) -> None:
        self._compute = compute_planner or ComputePlanner()

    def plan_create(
        self,
        *,
        lab_id: str,
        template: HomeLabTemplate,
        quota: HomeLabQuota,
        usage: HomeLabUsage,
        provider: ComputeProviderDescriptor,
        available_cpu: int,
        available_memory_mib: int,
        available_storage_gib: int,
        high_availability: bool = False,
    ) -> HomeLabPlan:
        if LAB_ID.fullmatch(lab_id) is None:
            raise HomeLabError("invalid_lab_id")
        blockers: list[str] = []
        if usage.instances + 1 > quota.max_instances:
            blockers.append("lab_instance_quota_exceeded")
        if usage.vcpu + template.vcpu > quota.max_vcpu:
            blockers.append("lab_cpu_quota_exceeded")
        if usage.memory_mib + template.memory_mib > quota.max_memory_mib:
            blockers.append("lab_memory_quota_exceeded")
        if usage.storage_gib + template.disk_gib > quota.max_storage_gib:
            blockers.append("lab_storage_quota_exceeded")
        if blockers:
            return HomeLabPlan(
                lab_id=lab_id,
                template_id=template.template_id,
                state=HomeLabPlanState.BLOCKED,
                blockers=tuple(blockers),
                compute_plan=None,
            )

        request = ComputeResourceRequest(
            resource_id=lab_id,
            kind=template.resource_kind,
            vcpu=template.vcpu,
            memory_mib=template.memory_mib,
            disk_gib=template.disk_gib,
            high_availability=high_availability,
        )
        compute_plan = self._compute.plan_create(
            provider,
            request,
            available_cpu=available_cpu,
            available_memory_mib=available_memory_mib,
            available_storage_gib=available_storage_gib,
        )
        if compute_plan.blockers:
            return HomeLabPlan(
                lab_id=lab_id,
                template_id=template.template_id,
                state=HomeLabPlanState.BLOCKED,
                blockers=compute_plan.blockers,
                compute_plan=compute_plan,
            )
        return HomeLabPlan(
            lab_id=lab_id,
            template_id=template.template_id,
            state=HomeLabPlanState.PLANNED,
            blockers=(),
            compute_plan=compute_plan,
        )
