from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass

from workflow_engine.models import StepContext

Handler = Callable[[StepContext], dict[str, object]]


@dataclass(frozen=True)
class Step:
    name: str
    handler: Handler
    max_attempts: int = 3
    timeout_seconds: int = 15


@dataclass(frozen=True)
class Workflow:
    name: str
    steps: list[Step]


class Registry:
    def __init__(self) -> None:
        self._workflows: dict[str, Workflow] = {}

    def register(self, workflow: Workflow) -> None:
        if not workflow.steps:
            raise ValueError(f"workflow {workflow.name} has no steps")
        names = [s.name for s in workflow.steps]
        if len(names) != len(set(names)):
            raise ValueError(f"workflow {workflow.name} has duplicate step names")
        self._workflows[workflow.name] = workflow

    def get(self, name: str) -> Workflow | None:
        return self._workflows.get(name)

    def names(self) -> list[str]:
        return sorted(self._workflows)

    def handler(self, workflow_name: str, step_name: str) -> Handler | None:
        wf = self.get(workflow_name)
        if wf is None:
            return None
        for step in wf.steps:
            if step.name == step_name:
                return step.handler
        return None


def full_jitter(attempt_count: int, base: float, max_backoff: float, rng: random.Random) -> float:
    """Full jitter: delay ~ U(0, min(max, base * 2^(attempt-1)))."""
    if attempt_count < 1:
        attempt_count = 1
    cap = min(max_backoff, base * (2 ** (attempt_count - 1)))
    return float(rng.uniform(0, cap))


REGISTRY = Registry()
