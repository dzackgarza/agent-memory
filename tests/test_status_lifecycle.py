from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from agent_memory.cards import CardSystemConfig, load_card_system_config

# Minimal config carrying workflow_roles: each role lists statuses drawn from the catalog.
CONFIG: dict[str, Any] = {
    "statuses": ["unstarted", "in-progress", "complete"],
    "status_sets": {"standard": {"default": "unstarted", "options": ["unstarted", "in-progress", "complete"]}},
    "card_types": [
        {
            "name": "feature",
            "id_prefix": "FEATURE",
            "status_set": "standard",
            "parents": [],
            "own_dir": True,
            "fields": [
                {"name": "id", "type": "string", "required": True},
                {"name": "title", "type": "string", "required": True},
                {"name": "status", "type": "status"},
            ],
        },
    ],
    "workflow_roles": {
        "started": ["in-progress", "complete"],
        "complete": ["complete"],
        "unstarted": ["unstarted"],
    },
}


def test_config_exposes_workflow_role_membership() -> None:
    config = CardSystemConfig.model_validate(CONFIG)
    assert config.statuses_with_role("started") == {"in-progress", "complete"}
    assert config.statuses_with_role("unstarted") == {"unstarted"}
    assert config.statuses_with_role("complete") == {"complete"}


def test_statuses_with_role_rejects_unknown_role() -> None:
    config = CardSystemConfig.model_validate(CONFIG)
    with pytest.raises(AssertionError):
        config.statuses_with_role("nonexistent-role")


def test_config_rejects_workflow_role_status_absent_from_catalog() -> None:
    bad = deepcopy(CONFIG)
    bad["workflow_roles"]["started"].append("not-a-status")
    with pytest.raises(ValidationError):
        CardSystemConfig.model_validate(bad)


def test_shipped_config_ports_status_catalog_workflow_roles() -> None:
    config = load_card_system_config()
    started = config.statuses_with_role("started")
    complete = config.statuses_with_role("complete")
    unstarted = config.statuses_with_role("unstarted")
    # Ported from ~/ai/planning/status-catalog.yaml, minus "done" (not a status here).
    assert {"in-progress", "needs-agent-review", "complete", "blocked", "decided", "implemented"} <= started
    assert {"complete", "decided", "implemented"} == complete
    assert {"unstarted", "approved-and-unstarted"} == unstarted
    # complete statuses are a subset of started (a completed card has been started).
    assert complete <= started
    # every role member is a real catalog status.
    catalog = set(config.statuses)
    for role in ("started", "complete", "unstarted"):
        assert config.statuses_with_role(role) <= catalog
