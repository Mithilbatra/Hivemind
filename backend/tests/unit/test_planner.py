import pytest

from app.services import planner
from app.services.planner import PlanError, Step


def test_heuristic_feature_plan_is_valid_dag():
    steps = planner.validate_plan(planner.heuristic_plan("Add OAuth login", "to the app", {"languages": {"Python": 3}}))
    assert [s.key for s in steps] == ["inspect", "design", "implement", "test", "review"]


def test_split_backend_frontend():
    steps = planner.heuristic_plan("Add login", "", {"languages": {"Python": 3, "TypeScript": 5}})
    assert {"implement_backend", "implement_frontend"} <= {s.key for s in steps}
    test = next(s for s in steps if s.key == "test")
    assert set(test.deps) == {"implement_backend", "implement_frontend"}


def test_bugfix_template():
    keys = [s.key for s in planner.heuristic_plan("Fix crash on save", "")]
    assert keys == ["inspect", "implement", "test", "review"]


def test_cycle_rejected():
    with pytest.raises(PlanError, match="cycle"):
        planner.validate_plan([Step("a", "a", "", "implement", ["b"]), Step("b", "b", "", "implement", ["a"])])


def test_unknown_dep_and_duplicates_rejected():
    with pytest.raises(PlanError, match="unknown"):
        planner.validate_plan([Step("a", "a", "", "implement", ["zzz"])])
    with pytest.raises(PlanError, match="duplicate"):
        planner.validate_plan([Step("a", "a", "", "implement"), Step("a", "b", "", "test")])
    with pytest.raises(PlanError):
        planner.validate_plan([])


def test_topological_order():
    out = planner.validate_plan([Step("b", "b", "", "test", ["a"]), Step("a", "a", "", "implement")])
    assert [s.key for s in out] == ["a", "b"]


def test_parse_agent_plan():
    text = 'Sure:\n{"steps": [{"key": "a", "title": "A", "kind": "weird"}, {"key": "b", "title": "B", "kind": "test", "deps": ["a"]}]}'
    steps = planner.parse_agent_plan(text)
    assert [s.kind for s in steps] == ["implement", "test"]
    with pytest.raises(PlanError):
        planner.parse_agent_plan("no json here")
    with pytest.raises(PlanError):
        planner.parse_agent_plan('{"steps": [{"title": "missing key"}]}')
