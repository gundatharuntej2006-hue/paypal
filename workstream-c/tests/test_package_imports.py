"""Verify that all Workstream C packages can be imported independently."""

from importlib import import_module

import pytest


@pytest.mark.parametrize(
    "module_name",
    [
        "agentguard_workstream_c",
        "agentguard_workstream_c.sdk",
        "agentguard_workstream_c.merchants",
        "agentguard_workstream_c.agent",
        "agentguard_workstream_c.attacks",
        "agentguard_workstream_c.benchmark",
    ],
)
def test_package_imports(module_name):
    assert import_module(module_name).__name__ == module_name
