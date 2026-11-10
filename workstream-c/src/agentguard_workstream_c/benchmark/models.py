"""Internal experiment records, never payment requests or shared API contracts."""

from dataclasses import dataclass
from typing import Protocol
import json

from ..agent.state import ModelDecision
from .evidence import IntelligenceEvidence

CATEGORIES = (
    "indirect_prompt_injection", "authority_attacks", "budget_evasion",
    "merchant_attacks", "intent_drift", "operational_attacks",
)
DECISIONS = ("allow", "step_up", "block")
LAYERS = ("mandate", "injection", "intent", "risk", "idempotency", "state_machine")
SYSTEMS = ("llm_only", "full_stack")


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    name: str
    category: str
    description: str
    attack_vector: str
    task: str
    expected_decision: str
    expected_catching_layer: str | None
    severity: str
    tags: tuple[str, ...]
    input_json: str

    @property
    def inputs(self) -> dict:
        """Return a fresh snapshot so an executor cannot change another run."""
        return json.loads(self.input_json)

    def to_dict(self) -> dict:
        return {**{key: getattr(self, key) for key in (
            "scenario_id", "name", "category", "description", "attack_vector", "task",
            "expected_decision", "expected_catching_layer", "severity")},
            "tags": list(self.tags), "input": self.inputs}


@dataclass(frozen=True)
class Observation:
    """None/unknown/error/unsupported outcomes are not security decisions."""

    decision: str | None = None
    catching_layer: str | None = None
    latency_ms: float | None = None
    outcome: str = "missing"
    error_code: str | None = None
    intelligence: IntelligenceEvidence | None = None
    model_output: ModelDecision | None = None  # Actual adapter outcome, never a constructed mutation.


class Executor(Protocol):
    """Future dry-run boundary. No gateway transport is implemented here.

    Implementations report observed layers independently of scenario expectations.
    Current runner accepts synthetic executors only, not payment clients.
    """

    name: str
    data_source: str
    synthetic: bool

    def execute(self, scenario: Scenario, *, system: str) -> Observation:
        ...
