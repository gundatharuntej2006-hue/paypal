"""Observed intelligence metadata: no config defaults and no service calls.

Collectors pass actual decoded responses. This parser cannot prove provenance;
offline tests use explicitly labeled fake responses, never performance evidence.
"""

from collections.abc import Mapping
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class SignalMetadata:
    version: str | None = None
    latency_ms: int | None = None

    def __post_init__(self):
        if self.version is not None and (not isinstance(self.version, str) or not self.version.strip()):
            raise ValueError("signal version must be a nonempty observed string or null")
        if self.latency_ms is not None and (type(self.latency_ms) is not int or self.latency_ms < 0):
            raise ValueError("signal latency must be an observed nonnegative integer or null")

    @classmethod
    def from_response(cls, response: Mapping | None):
        if response is None:
            return None
        if not isinstance(response, Mapping):
            raise ValueError("signal metadata requires a decoded response object")
        return cls(response.get("version"), response.get("latency_ms"))


@dataclass(frozen=True)
class IntelligenceEvidence:
    scan: SignalMetadata | None = None
    judge: SignalMetadata | None = None
    risk: SignalMetadata | None = None

    def __post_init__(self):
        if any(value is not None and not isinstance(value, SignalMetadata) for value in (self.scan, self.judge, self.risk)):
            raise ValueError("intelligence evidence must contain observed SignalMetadata or null")

    @classmethod
    def from_responses(cls, *, scan_response=None, judge_response=None, risk_response=None):
        return cls(*(SignalMetadata.from_response(r) for r in (scan_response, judge_response, risk_response)))

    @property
    def active_risk_policy_version(self) -> str | None:
        # No inference from declared configuration, calibration metadata or model ID.
        return None if self.risk is None else self.risk.version

    def to_dict(self):
        return {**asdict(self), "active_risk_policy_version": self.active_risk_policy_version}


def methodology_metadata() -> dict:
    """Reference-method disclosure, not a claim about a measured active engine."""
    return {"risk_calibration": {
        "reference_policy_version": "risk_rules_v2",
        "calibration_scenario_ids": [f"benign_{n:02d}" for n in range(1, 21)],
        "false_positive_rate_scope": "in_sample_on_these_calibration_cases",
        "disclosure": "The existing 20 benign scenarios were used during risk_rules_v2 calibration. "
                      "False-positive rates on those cases are in-sample, not held-out generalization results. "
                      "A separate held-out benign set is required for independent evaluation.",
        "active_version_source": "Per-scenario, per-arm observed intelligence.risk.version; unavailable remains null.",
    }}
