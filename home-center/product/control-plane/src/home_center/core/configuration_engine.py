"""Desired-state and configuration-lock planning primitives."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


CONFIG_KEY = re.compile(r"^[a-z][a-z0-9_.-]{1,127}$")


class ConfigurationEngineError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ConfigurationLock:
    key: str
    owner: str
    reason: str


@dataclass(frozen=True, slots=True)
class ConfigurationPlan:
    key: str
    state: str
    blockers: tuple[str, ...]
    production_activation_enabled: bool = False


class ConfigurationEngine:
    def __init__(self, locks: Iterable[ConfigurationLock] = ()) -> None:
        self._locks: dict[str, ConfigurationLock] = {}
        for lock in locks:
            if CONFIG_KEY.fullmatch(lock.key) is None or not lock.owner or not lock.reason:
                raise ConfigurationEngineError("invalid_configuration_lock")
            if lock.key in self._locks:
                raise ConfigurationEngineError("duplicate_configuration_lock")
            self._locks[lock.key] = lock

    def plan_change(self, key: str) -> ConfigurationPlan:
        if CONFIG_KEY.fullmatch(key) is None:
            raise ConfigurationEngineError("invalid_configuration_key")
        lock = self._locks.get(key)
        blockers = () if lock is None else (f"locked_by:{lock.owner}",)
        return ConfigurationPlan(key=key, state="blocked" if blockers else "planned", blockers=blockers)
