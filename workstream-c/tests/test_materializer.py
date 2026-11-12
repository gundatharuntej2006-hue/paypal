"""Pure materialization and fake-agent graph tests; no benchmark execution."""

from collections import Counter
from dataclasses import FrozenInstanceError, asdict, replace
from html import unescape
import json
import socket
from urllib.request import OpenerDirector

import pytest

from agentguard_workstream_c.agent.anthropic_model import OUTPUT_SCHEMA
from agentguard_workstream_c.agent.graph import run_shopping
from agentguard_workstream_c.agent.state import Selection
from agentguard_workstream_c.attacks.fixtures import ATTACKS
from agentguard_workstream_c.attacks.poisoning import poisoned_detail
from agentguard_workstream_c.benchmark.materializer import (
    BASE_URL, ModelConfiguration, capability_report, content_scan_inputs, inventory_hash, materialize_library, materialize_scenario, verify_paired_inputs,
)
from agentguard_workstream_c.benchmark.scenario_loader import load_scenarios
from agentguard_workstream_c.benchmark.validator import ScenarioValidationError
from agentguard_workstream_c.merchants.visible_text import visible_text

SCENARIOS = load_scenarios()
BY_ID = {s.scenario_id: s for s in SCENARIOS}
CONFIG = ModelConfiguration()


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    attempts = []
    def forbidden(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("materialization tests forbid network and DNS")
    for name in ("create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex"):
        monkeypatch.setattr(socket, name, forbidden)
    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, forbidden)
    monkeypatch.setattr(OpenerDirector, "open", forbidden)
    yield
    assert not attempts


@pytest.fixture(scope="module")
def cases():
    return materialize_library(SCENARIOS, CONFIG)


def test_all_80_retained_with_explicit_capabilities(cases):
    assert len(cases) == len({c.scenario_id for c in cases}) == 80
    assert {c.scenario_id for c in cases} == set(BY_ID)
    assert Counter(c.capability for c in cases) == {"shopping_agent": 19, "future_gateway": 44, "unsupported": 17}
    assert Counter(c.category for c in cases) == {**{s.category: 10 for s in SCENARIOS if s.category != "benign"}, "benign": 20}
    assert all(c.reason for c in cases)
    assert len(capability_report(cases)) == 80
    assert not any(hasattr(c, "actual_decision") or hasattr(c, "actual_catching_layer") for c in cases)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.scenario_id)
def test_every_input_payload_operation_and_fixture_is_preserved(scenario):
    case = materialize_scenario(scenario, CONFIG)
    inputs = scenario.inputs
    assert case.scenario_id == scenario.scenario_id and case.category == scenario.category
    assert case.original_task == scenario.task
    assert case.attack_payload == inputs["payload"] and case.operations == tuple(inputs["operations"])
    assert json.loads(case.declared_inputs_json) == inputs
    assert case.merchant.merchant_id == inputs["merchant_id"] and case.product.product_id == inputs["product_id"]
    assert tuple(f.attack_id for f in case.fixtures) == tuple(inputs["fixture_refs"])
    for fixture, preview in zip(case.fixtures, case.fixture_previews):
        assert preview.html == poisoned_detail(case.merchant, case.product, fixture)
        assert preview.url == BASE_URL + fixture.poisoned_path
        assert fixture.malicious_content in unescape(preview.html)
    if inputs["payload"]:
        assert all(inputs["payload"] not in unescape(p.html) for p in case.pages)
    for page in case.pages:
        assert page.visible_text == visible_text(page.html)
        assert page.context.html == page.html and page.context.url == page.url
        assert page.context.ref == page.model_page.source_ref
        assert page.model_page.content == page.context.text
        assert page.context.text == (page.html if case.config.reader_mode == "raw_html" else page.visible_text)
    if case.capability != "shopping_agent":
        with pytest.raises(ValueError, match=case.capability): case.agent_input()
        with pytest.raises(ValueError, match=case.capability): case.merchant_client()


def test_all_15_fixture_variants_resolve(cases):
    assert {f.attack_id for c in cases for f in c.fixtures} == {f.attack_id for f in ATTACKS}
    hidden = materialize_scenario(BY_ID["ipi_offscreen"], CONFIG)
    page = next(p for p in hidden.pages if p.url.endswith(hidden.fixtures[0].poisoned_path))
    assert hidden.fixtures[0].malicious_content in page.html
    assert hidden.fixtures[0].malicious_content not in page.visible_text
    assert page.original_html != page.html
    assert hidden.attack_payload not in page.visible_text
    assert page.html == hidden.fixture_previews[0].html


def test_descriptions_are_metadata_and_not_fabricated_stimuli():
    for name in ("ipi_metadata", "merchant_homoglyph", "merchant_link_launder"):
        case = materialize_scenario(BY_ID[name], CONFIG)
        assert case.capability == "unsupported"
        assert all(page.html == page.original_html for page in case.pages)
        assert case.attack_payload == BY_ID[name].inputs["payload"]


def test_scan_projection_contains_only_original_15_fixtures(cases):
    contexts = content_scan_inputs()
    assert len(contexts) == 15 and tuple(c.ref for c in contexts) == tuple(f.attack_id for f in ATTACKS)
    assert contexts == content_scan_inputs()
    for context, fixture in zip(contexts, ATTACKS):
        case = next(c for c in cases if fixture in c.fixtures)
        original = poisoned_detail(case.merchant, case.product, fixture)
        assert context.html == original and context.text == visible_text(original)
        assert context.url.endswith(fixture.poisoned_path)
        assert 'data-runtime-payload' not in context.html
        # Exact HTML equality also preserves all original attribute/comment bytes.
        target = next(p for p in case.pages if p.url.endswith(fixture.poisoned_path))
        assert target.html == original


def test_inventory_is_order_independent_and_paired(cases):
    assert inventory_hash(cases) == inventory_hash(tuple(reversed(cases)))
    assert verify_paired_inputs(cases, cases) == inventory_hash(cases)
    assert inventory_hash(materialize_library(SCENARIOS, CONFIG)) == inventory_hash(cases)


@pytest.mark.parametrize("field", ["task", "payload", "model", "catalog"])
def test_changed_experiment_input_changes_digest(cases, field):
    original = materialize_scenario(BY_ID["ipi_bidi"], CONFIG)
    if field in ("task", "payload"):
        data = BY_ID["ipi_bidi"].to_dict()
        if field == "task": data["task"] += " Keep the original objective."
        else: data["input"]["payload"] += " Additional untrusted directive."
        changed = materialize_scenario(data, CONFIG)
    elif field == "model":
        changed = materialize_scenario(BY_ID["ipi_bidi"], ModelConfiguration("anthropic", "explicit-offline-model"))
    else:
        merchant = original.catalog_snapshot[0]
        product = replace(merchant.products[0], description="Changed catalog description")
        changed = replace(original, catalog_snapshot=(replace(merchant, products=(product, *merchant.products[1:])),
                                                      *original.catalog_snapshot[1:]))
    assert changed.input_sha256 != original.input_sha256
    with pytest.raises(ValueError, match="paired experiment inputs differ"):
        verify_paired_inputs((original,), (changed,))


def test_expected_labels_do_not_change_input_or_hash():
    original = BY_ID["ipi_offscreen"]
    data = original.to_dict()
    data.update(expected_catching_layer="risk", expected_decision="step_up")
    a, b = materialize_scenario(original, CONFIG), materialize_scenario(data, CONFIG)
    assert a.input_sha256 == b.input_sha256
    assert a.agent_input() == b.agent_input()


def test_evidence_only_change_changes_hash_and_missing_arm_case_is_rejected(cases):
    original = cases[0]
    page = replace(original.pages[0], html=original.pages[0].html + "<!-- changed evidence -->")
    changed = replace(original, pages=(page, *original.pages[1:]))
    assert changed.declared_inputs_json == original.declared_inputs_json
    assert changed.input_sha256 != original.input_sha256
    with pytest.raises(ValueError, match="paired experiment inputs differ"):
        verify_paired_inputs(cases, cases[:-1])


def test_no_expectation_keys_enter_agent_input(cases):
    for case in cases:
        if case.capability == "shopping_agent":
            request = asdict(case.agent_input())
            def visit(value):
                if isinstance(value, dict):
                    assert not {"expected_decision", "expected_catching_layer", "actual_catching_layer"} & set(value)
                    for child in value.values(): visit(child)
                elif isinstance(value, (list, tuple)):
                    for child in value: visit(child)
            visit(request)


def test_typed_snapshots_are_immutable_and_exports_are_fresh():
    case = materialize_scenario(BY_ID["ipi_bidi"], ModelConfiguration("anthropic", "explicit-model"))
    with pytest.raises(FrozenInstanceError): case.original_task = "mutated"
    exported = case.experiment_inputs()
    before = case.input_sha256
    exported["inputs"]["payload"] = "mutated"
    exported["output_schema"]["required"].clear()
    assert case.input_sha256 == before and OUTPUT_SCHEMA["required"]


@pytest.mark.parametrize("mutation", ["extra", "missing", "bad_fixture", "wrong_target", "bad_type", "secret"])
def test_malformed_definitions_fail_closed(mutation):
    data = BY_ID["ipi_offscreen"].to_dict()
    if mutation == "extra": data["unknown"] = True
    if mutation == "missing": del data["input"]["payload"]
    if mutation == "bad_fixture": data["input"]["fixture_refs"] = ["poison_99"]
    if mutation == "wrong_target": data["input"]["fixture_refs"] = ["poison_01"]
    if mutation == "bad_type": data["input"]["quantity"] = True
    if mutation == "secret": data["input"]["api_key"] = "forbidden"
    with pytest.raises(ScenarioValidationError): materialize_scenario(data, CONFIG)


def test_invalid_library_and_duplicate_ids_fail_before_preparation():
    with pytest.raises(ScenarioValidationError): materialize_library(SCENARIOS[:-1], CONFIG)
    with pytest.raises(ScenarioValidationError): materialize_library((*SCENARIOS, SCENARIOS[0]), CONFIG)
    case = materialize_scenario(BY_ID["benign_01"], CONFIG)
    with pytest.raises(ValueError): inventory_hash((case, case))
    with pytest.raises(ValueError): inventory_hash(())


def test_unknown_operations_are_retained_and_not_supported():
    data = BY_ID["ipi_bidi"].to_dict()
    data["input"]["operations"].append("perform an unspecified lifecycle action")
    case = materialize_scenario(data, CONFIG)
    assert case.capability == "unsupported"
    assert case.operations[-1] == "perform an unspecified lifecycle action"


def test_multiple_fixture_refs_are_retained_but_not_silently_composed():
    data = BY_ID["ipi_hidden_css"].to_dict()
    data["input"]["fixture_refs"] = ["poison_01", "poison_02"]
    case = materialize_scenario(data, CONFIG)
    assert case.capability == "unsupported" and len(case.fixture_previews) == 2


def test_changed_declared_values_and_unknown_scenario_are_not_silently_supported():
    data = BY_ID["ipi_offscreen"].to_dict()
    data["input"]["amount"] = "199.99"
    assert materialize_scenario(data, CONFIG).capability == "future_gateway"
    data = BY_ID["ipi_bidi"].to_dict()
    data["scenario_id"] = "new_unspecified_case"
    assert materialize_scenario(data, CONFIG).capability == "unsupported"


def test_current_task_parser_limitation_retains_case():
    data = BY_ID["ipi_bidi"].to_dict()
    data["task"] += " EUR and USD are both required."
    case = materialize_scenario(data, CONFIG)
    assert case.capability == "unsupported" and case.original_task == data["task"]


@pytest.mark.parametrize("kwargs", [{"provider": "unknown"}, {"provider": "anthropic"}, {"model_id": ""}, {"timeout_seconds": float("nan")},
                                   {"timeout_seconds": 0}, {"max_tokens": True}])
def test_invalid_model_configuration_is_rejected(kwargs):
    with pytest.raises(ValueError): ModelConfiguration(**kwargs)


@pytest.mark.parametrize("scenario_id", ["benign_01", "benign_02", "auth_system_banner", "ipi_offscreen", "merchant_lookalike"])
def test_existing_graph_consumes_identical_snapshot_input(scenario_id):
    case = materialize_scenario(BY_ID[scenario_id], CONFIG)
    requests = []
    class FakeModel:
        name = "offline_materialization_double"
        def select(self, request):
            requests.append(request)
            candidate = next(p for p in request.product_candidates
                             if (p.merchant_id, p.product_id) == (case.merchant.merchant_id, case.product.product_id))
            ref = next(p.source_ref for p in request.pages if p.url == candidate.url)
            return Selection(candidate.merchant_id, candidate.product_id, "Offline fixture selection, not a benchmark result.", (ref,))
    state = run_shopping(case.original_task, model=FakeModel(), merchant_client=case.merchant_client())
    assert not state["execution_errors"]
    assert requests == [case.agent_input()]
    assert tuple(state["source_contexts"]) == tuple(p.context for p in case.pages)
    assert state["original_task"] == case.original_task
    client = case.merchant_client()
    with pytest.raises(ValueError): client.fetch("https://external.invalid/")


def test_graph_rejects_mismatched_reader_config():
    from agentguard_workstream_c.agent.graph import build_graph
    from agentguard_workstream_c.agent.state import AgentConfig
    case = materialize_scenario(BY_ID["benign_01"], CONFIG)
    with pytest.raises(ValueError, match="config must match"):
        build_graph(AgentConfig(reader_mode="visible_text"), merchant_client=case.merchant_client())
