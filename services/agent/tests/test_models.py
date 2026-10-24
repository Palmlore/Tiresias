import json
from datetime import date
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from tiresias_agent.models import (
    BranchRequest,
    EndState,
    ErrorResponse,
    EvidenceStatus,
    Link,
    LinkLabel,
    RunRequest,
    RunResult,
)

ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = sorted((ROOT / "input_examples").glob("*.json"))
CONTRACT = ROOT / "contracts" / "openapi" / "agent.yaml"


def test_there_are_three_input_examples():
    assert len(EXAMPLES) == 3


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_input_examples_validate_as_run_requests(path):
    request = RunRequest.model_validate(json.loads(path.read_text()))
    assert request.entity.ticker == "GE"
    assert isinstance(request.period.start, date)
    assert request.asker.role in {"analyst", "auditor", "board"}
    assert request.options.hitl_mode == "auto"


def _example() -> dict:
    return json.loads(EXAMPLES[0].read_text())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.pop("question"),
        lambda d: d.pop("asker"),
        lambda d: d["asker"].update(role="ceo"),
        lambda d: d["period"].update(start="not-a-date"),
        lambda d: d["options"].update(hitl_mode="yolo"),
        lambda d: d["options"].update(memory="lukewarm"),
    ],
)
def test_run_request_rejects_invalid_input(mutate):
    data = _example()
    mutate(data)
    with pytest.raises(ValidationError):
        RunRequest.model_validate(data)


def test_options_are_optional():
    data = _example()
    data.pop("options")
    assert RunRequest.model_validate(data).options.memory is None


def test_run_result_round_trips_with_why_tree():
    result = RunResult.model_validate(
        {
            "run_id": "run-20261023T000000Z-abcdef",
            "status": "success",
            "end_state": "branched",
            "action": "Re-base the model on continuing operations; residual open.",
            "residual": 0.08,
            "why_tree": [
                {"node_id": "n1", "claim": "Revenue fell", "label": "breakdown"},
                {
                    "node_id": "n2",
                    "parent_id": "n1",
                    "claim": "GE HealthCare spin-off",
                    "label": "causal",
                    "evidence_status": "evidenced",
                    "share_explained": 0.92,
                    "residual": 0.08,
                    "sql": "SELECT 1",
                    "rows_ref": "rows/n2.json",
                },
            ],
            "stats": {"hops": 2, "queries": 3},
        }
    )
    assert result.why_tree[1].share_explained == 0.92
    assert RunResult.model_validate(result.model_dump(mode="json")) == result


@pytest.mark.parametrize("end_state", get_args(EndState))
def test_all_six_end_states_accepted(end_state):
    assert RunResult(run_id="r", status="partial", end_state=end_state).end_state == end_state


def test_unknown_end_state_and_label_rejected():
    with pytest.raises(ValidationError):
        RunResult(run_id="r", status="success", end_state="done")
    with pytest.raises(ValidationError):
        Link(node_id="n", claim="c", label="correlation")


def test_branch_request_requires_node_id():
    assert BranchRequest(node_id="n2").asker is None
    with pytest.raises(ValidationError):
        BranchRequest.model_validate({"question": "why?"})


def test_error_envelope_matches_app_501_shape():
    body = {
        "status": "error",
        "error": {"type": "NotImplemented", "message": "under construction", "recoverable": False},
    }
    assert ErrorResponse.model_validate(body).error.type == "NotImplemented"
    with pytest.raises(ValidationError):
        ErrorResponse.model_validate({**body, "status": "success"})


def test_models_match_contract_enums():
    yaml = pytest.importorskip("yaml")
    schemas = yaml.safe_load(CONTRACT.read_text())["components"]["schemas"]
    result = schemas["RunResult"]["properties"]
    link = schemas["Link"]["properties"]
    assert result["end_state"]["enum"] == list(get_args(EndState))
    assert link["label"]["enum"] == list(get_args(LinkLabel))
    assert link["evidence_status"]["enum"] == list(get_args(EvidenceStatus))
    for name in ("action", "residual", "parent_node_id", "why_tree"):
        assert name in result and name in RunResult.model_fields
    for name in ("share_explained", "residual", "label"):
        assert name in link and name in Link.model_fields
    assert set(schemas["BranchRequest"]["properties"]) == set(BranchRequest.model_fields)
    assert set(schemas["RunRequest"]["required"]) <= {
        n for n, f in RunRequest.model_fields.items() if f.is_required()
    }


def test_result_shapes_match_the_contract_field_for_field():
    yaml = pytest.importorskip("yaml")
    from tiresias_agent import models

    schemas = yaml.safe_load(CONTRACT.read_text())["components"]["schemas"]
    for name in (
        "RunResult",
        "Link",
        "EvidenceItem",
        "Observation",
        "Effect",
        "EntityHint",
        "InformationNeed",
        "ScopeCheck",
        "ScopeReason",
        "Alternative",
        "Suggestion",
        "ExpectedWait",
        "EntityInfo",
        "EntityList",
        "SuggestionList",
        "ModelOpinion",
    ):
        model = getattr(models, name)
        assert set(schemas[name]["properties"]) == set(model.model_fields), name
    stats = set(schemas["RunStats"]["properties"])
    assert {"retries", "prunes", "gate_decisions"} <= stats
