from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ComponentHealth:
  component: str
  status: str
  details: dict
