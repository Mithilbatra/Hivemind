"""Goal -> DAG of subtasks. Agent-produced JSON when valid, deterministic template otherwise."""
import json
import re
from dataclasses import dataclass, field

MAX_STEPS = 12


class PlanError(ValueError):
    pass


@dataclass
class Step:
    key: str
    title: str
    description: str
    kind: str  # inspect|design|implement|test|review
    deps: list[str] = field(default_factory=list)


def validate_plan(steps: list[Step]) -> list[Step]:
    """Check keys and dependencies, reject cycles, and return the steps in topological order."""
    if not steps:
        raise PlanError("plan has no steps")
    if len(steps) > MAX_STEPS:
        raise PlanError(f"plan has {len(steps)} steps (max {MAX_STEPS})")
    keys = [s.key for s in steps]
    if len(set(keys)) != len(keys):
        raise PlanError("duplicate step keys")
    for s in steps:
        for d in s.deps:
            if d not in keys:
                raise PlanError(f"step '{s.key}' depends on unknown step '{d}'")
            if d == s.key:
                raise PlanError(f"step '{s.key}' depends on itself")
    done: list[Step] = []
    placed: set[str] = set()
    remaining = list(steps)
    while remaining:
        ready = [s for s in remaining if set(s.deps) <= placed]
        if not ready:
            raise PlanError("dependency cycle: " + ", ".join(s.key for s in remaining))
        for s in ready:
            done.append(s)
            placed.add(s.key)
            remaining.remove(s)
    return done


def classify(text: str) -> str:
    t = text.lower()
    if re.search(r"\b(fix|bug|crash|error|broken|regression|fails?)\b", t):
        return "bugfix"
    if re.search(r"\b(refactor|cleanup|clean up|rename|restructure|migrate)\b", t):
        return "refactor"
    return "feature"


def heuristic_plan(title: str, description: str, ctx: dict | None = None) -> list[Step]:
    goal = f"{title}\n{description}".strip()
    kind = classify(goal)
    langs = set((ctx or {}).get("languages", {}))
    split = bool(langs & {"JavaScript", "TypeScript"}) and bool(langs & {"Python", "Go", "Java", "Ruby", "Rust"})
    inspect = Step("inspect", "Inspect repository", f"Find the code relevant to: {goal}. Report files, entry points and risks.", "inspect")
    review_desc = "Review the diff against the acceptance criteria and report defects."
    if kind == "bugfix":
        impl = Step("implement", "Implement fix", f"Reproduce and fix: {goal}", "implement", ["inspect"])
        test = Step("test", "Add regression test", "Add a test that fails before the fix and passes after.", "test", ["implement"])
        return [inspect, impl, test, Step("review", "Review changes", review_desc, "review", ["test"])]
    design = Step("design", "Design approach", f"Design the change for: {goal}. Define interfaces and affected files.", "design", ["inspect"])
    if split:
        impls = [Step("implement_backend", "Implement backend", f"Backend part of: {goal}", "implement", ["design"]),
                 Step("implement_frontend", "Implement frontend", f"Frontend part of: {goal}", "implement", ["design"])]
    else:
        impls = [Step("implement", "Implement change", f"Implement: {goal}", "implement", ["design"])]
    test = Step("test", "Write tests", "Add or update tests covering the change.", "test", [i.key for i in impls])
    return [inspect, design, *impls, test, Step("review", "Review changes", review_desc, "review", ["test"])]


def parse_agent_plan(text: str) -> list[Step]:
    """Extract {"steps": [{key,title,description,kind,deps}]} from agent output."""
    # Decode from each "{" until one parses: a greedy regex would swallow any braces in prose after the JSON.
    decoder, data = json.JSONDecoder(), None
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = decoder.raw_decode(text, m.start())
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "steps" in obj:
            data = obj
            break
    if data is None:
        raise PlanError("no plan JSON object in planner output")
    try:
        steps = [Step(key=str(s["key"]), title=str(s["title"])[:300], description=str(s.get("description", "")),
                      kind=str(s.get("kind", "implement")), deps=[str(d) for d in s.get("deps", [])]) for s in data["steps"]]
    except (KeyError, TypeError) as e:
        raise PlanError(f"malformed plan: {e}")
    for s in steps:
        if s.kind not in {"inspect", "design", "implement", "test", "review"}:
            s.kind = "implement"
    return validate_plan(steps)


PLAN_INSTRUCTIONS = (
    'Decompose the goal into at most 8 steps. Reply with ONLY a JSON object: {"steps": [{"key": "short_id", '
    '"title": "...", "description": "...", "kind": "inspect|design|implement|test|review", "deps": ["other_key"]}]}. '
    "Steps must form a DAG; each step must be doable by one agent without the others running at the same time."
)
