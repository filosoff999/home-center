"""Node identity registry and side-effect-free lifecycle planning."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable


NODE_ID = re.compile(r"^[a-z][a-z0-9_.:-]{1,127}$")
HOSTNAME = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$", re.IGNORECASE)


class NodeManagerError(ValueError):
    pass


class NodeState(StrEnum):
    DISCOVERED = "discovered"
    ENROLLED = "enrolled"
    ACTIVE = "active"
    MAINTENANCE = "maintenance"
    DRAINING = "draining"
    DECOMMISSIONED = "decommissioned"


@dataclass(frozen=True, slots=True)
class NodeDescriptor:
    node_id: str
    hostname: str
    state: NodeState
    capabilities: tuple[str, ...]

    @classmethod
    def create(
        cls, *, node_id: str, hostname: str, state: NodeState, capabilities: Iterable[str]
    ) -> "NodeDescriptor":
        if NODE_ID.fullmatch(node_id) is None or HOSTNAME.fullmatch(hostname) is None:
            raise NodeManagerError("invalid_node_identity")
        normalized = tuple(sorted(set(capabilities)))
        if any(NODE_ID.fullmatch(item) is None for item in normalized):
            raise NodeManagerError("invalid_capability")
        return cls(node_id=node_id, hostname=hostname, state=state, capabilities=normalized)


@dataclass(frozen=True, slots=True)
class NodeLifecyclePlan:
    node_id: str
    action: str
    state: str
    blockers: tuple[str, ...]
    production_activation_enabled: bool = False


class NodeManager:
    def __init__(self, nodes: Iterable[NodeDescriptor] = ()) -> None:
        self._nodes: dict[str, NodeDescriptor] = {}
        for node in nodes:
            self.register(node)

    def register(self, node: NodeDescriptor) -> NodeDescriptor:
        current = self._nodes.get(node.node_id)
        if current is not None and current != node:
            raise NodeManagerError("node_identity_conflict")
        self._nodes[node.node_id] = node
        return node

    def list_nodes(self) -> tuple[NodeDescriptor, ...]:
        return tuple(self._nodes[key] for key in sorted(self._nodes))

    def plan_drain(self, node_id: str, *, quorum_safe: bool, mandatory_services_safe: bool) -> NodeLifecyclePlan:
        node = self._nodes.get(node_id)
        if node is None:
            raise NodeManagerError("node_not_found")
        blockers: list[str] = []
        if node.state not in {NodeState.ACTIVE, NodeState.MAINTENANCE}:
            blockers.append("node_state_not_drainable")
        if not quorum_safe:
            blockers.append("quorum_not_safe")
        if not mandatory_services_safe:
            blockers.append("mandatory_services_not_safe")
        return NodeLifecyclePlan(
            node_id=node_id,
            action="drain",
            state="blocked" if blockers else "planned",
            blockers=tuple(blockers),
        )
