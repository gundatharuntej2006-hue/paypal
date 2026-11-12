"""Capability-aware model execution, separate from official security observations.

No graph default model, merchant transport, gateway, or intelligence client is
used. Injected Python models are trusted code, not sandboxed by this interface.
"""

from collections import Counter
from dataclasses import asdict, dataclass
from time import perf_counter
from typing import Literal

from ..agent.anthropic_model import AnthropicShoppingModel
from ..agent.model import ShoppingModel, ShoppingModelError
from ..agent.state import ModelDecision, Selection
from .materializer import ModelConfiguration, RuntimeCase, inventory_hash, materialize_library


@dataclass(frozen=True)
class ModelExecution:
    outcome: Literal["purchase_proposal", "refusal", "no_match", "invalid_response", "provider_error"]
    model_output: ModelDecision | Selection | None
    latency_ms: float
    error_code: str | None = None


@dataclass(frozen=True)
class LLMCaseRecord:
    case: RuntimeCase
    execution: ModelExecution | None  # None means not executed, never a fabricated observation.


@dataclass(frozen=True)
class LLMExecutionReport:
    records: tuple[LLMCaseRecord, ...]
    model_name: str
    provenance: Literal["test_double", "genuine_provider"]
    input_sha256: str

    def to_dict(self):
        return {
            "report_type": "llm_only_model_execution", "report_version": 1,
            "model_name": self.model_name, "provenance": self.provenance,
            "synthetic": self.provenance == "test_double", "input_sha256": self.input_sha256,
            "capability_counts": dict(Counter(r.case.capability for r in self.records)),
            "notice": "Model outcomes are not gateway decisions or official block-rate observations.",
            "records": [{"scenario_id": r.case.scenario_id, "capability": r.case.capability,
                         "reason": r.case.reason, "input_sha256": r.case.input_sha256,
                         "inputs": r.case.experiment_inputs(),
                         "execution": None if r.execution is None else asdict(r.execution)}
                        for r in self.records],
        }


def run_llm_only(scenarios, *, model: ShoppingModel, model_configuration: ModelConfiguration,
                 provenance: Literal["test_double", "genuine_provider"],
                 allow_provider_execution: bool = False) -> LLMExecutionReport:
    """Explicit Python entry point; does not enable the benchmark CLI.

    Re-materialize the complete library to revalidate capability before calling
    any model. Test doubles cannot acquire genuine provenance. Genuine mode
    permits only the concrete provider adapter without injected client/factory.
    """
    if provenance not in ("test_double", "genuine_provider"):
        raise ValueError("explicit execution provenance required")
    if model is None or not callable(getattr(model, "select", None)):
        raise ValueError("an injected ShoppingModel is required")
    if not isinstance(getattr(model, "name", None), str) or not model.name.strip():
        raise ValueError("model name required")
    if provenance == "genuine_provider":
        if allow_provider_execution is not True:
            raise ValueError("explicit provider execution opt-in required")
        if (type(model) is not AnthropicShoppingModel or model._client is not None
                or model._client_factory is not None):
            raise ValueError("genuine provenance requires an uninjected provider adapter")
        if (model_configuration.provider != "anthropic"
                or model_configuration.model_id != model.model_id
                or model_configuration.timeout_seconds != model.timeout
                or model_configuration.max_tokens != model.max_tokens):
            raise ValueError("provider configuration must match materialized configuration")
    elif isinstance(model, AnthropicShoppingModel):
        raise ValueError("provider adapters require genuine mode and explicit opt-in")
    cases = materialize_library(scenarios, model_configuration)
    digest = inventory_hash(cases)
    records = []
    for case in cases:
        execution = None
        if case.capability == "shopping_agent":
            request = case.agent_input()
            start = perf_counter()
            output, code = None, None
            try:
                output = model.select(request)
                if isinstance(output, ModelDecision):
                    outcome = output.outcome
                elif isinstance(output, Selection):
                    # Legacy ShoppingModel interface: retain the selection as returned.
                    outcome = "purchase_proposal"
                elif output is None:
                    outcome = "no_match"
                else:
                    output, outcome = None, "invalid_response"
            except ShoppingModelError as exc:
                outcome, code = "provider_error", exc.code
            except Exception:
                # Arbitrary provider exceptions can contain credentials and prompts.
                outcome, code = "provider_error", "provider"
            elapsed = (perf_counter() - start) * 1000
            execution = ModelExecution(outcome, output, elapsed, code)
        records.append(LLMCaseRecord(case, execution))
    return LLMExecutionReport(tuple(records), model.name, provenance, digest)
