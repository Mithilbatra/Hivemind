"""Agent role registry and task->role routing."""
from .models import Agent

READ_TOOLS = ["Read", "Grep", "Glob"]
WRITE_TOOLS = READ_TOOLS + ["Edit", "Write"]
BASH_OK = ["Bash(git diff:*)", "Bash(git status:*)", "Bash(git log:*)", "Bash(pytest:*)", "Bash(python -m pytest:*)",
           "Bash(npm test:*)", "Bash(npm run:*)", "Bash(ruff:*)", "Bash(tsc:*)", "Bash(ls:*)"]
DENY = ["Bash(rm:*)", "Bash(sudo:*)", "Bash(curl:*)", "Bash(wget:*)", "Bash(git push:*)", "Bash(git reset:*)",
        "Bash(git clean:*)", "Bash(git checkout:*)"]
# Globs with a "/" match the whole path; the rest match the file name (see sessions.policy_violation).
TEST_GLOBS = ["tests/*", "test/*", "*/tests/*", "*/test/*", "__tests__/*", "*/__tests__/*",
              "test_*", "*_test.*", "*.test.*", "*.spec.*", "conftest.py"]

DEFAULT_AGENTS = [
    dict(role="planner", can_write=False,
         description="Designs the approach and API contracts; never edits files.",
         system_prompt="You are the planning agent. Produce designs and plans only. Do not modify any file.",
         constraints=["Read-only", "Output a concise design, not code"],
         tool_policy={"allowed": READ_TOOLS, "disallowed": DENY}),
    dict(role="researcher", can_write=False,
         description="Inspects the repository and reports findings.",
         system_prompt="You are the research agent. Inspect the repository and report findings. Do not modify any file.",
         constraints=["Read-only", "Cite file paths for every claim"],
         tool_policy={"allowed": READ_TOOLS, "disallowed": DENY}),
    dict(role="writer", can_write=True,
         description="Implements code changes.",
         system_prompt="You are the implementation agent. Make the smallest correct change that satisfies the task.",
         constraints=["Stay within the repository", "Do not touch unrelated files", "Do not commit"],
         tool_policy={"allowed": WRITE_TOOLS + BASH_OK, "disallowed": DENY}),
    dict(role="tester", can_write=True, writable_globs=TEST_GLOBS,
         description="Writes and updates tests only.",
         system_prompt="You are the test agent. Add or update tests for the change. Do not modify production code.",
         constraints=["Only edit test files", "Do not commit"],
         tool_policy={"allowed": WRITE_TOOLS + BASH_OK, "disallowed": DENY}),
    dict(role="reviewer", can_write=False,
         description="Reviews the diff against the acceptance criteria.",
         system_prompt="You are the review agent. Review the working-tree diff and report problems. Do not modify any file.",
         constraints=["Read-only", "Report concrete defects with file:line"],
         tool_policy={"allowed": READ_TOOLS + ["Bash(git diff:*)", "Bash(git status:*)"], "disallowed": DENY}),
]

ROUTES = {"inspect": "researcher", "design": "planner", "implement": "writer", "fix": "writer",
          "test": "tester", "review": "reviewer"}


def route(kind: str) -> str:
    return ROUTES.get(kind, "writer")


def seed_agents(db) -> None:
    """Insert or refresh the built-in roles, so policy fixes reach existing databases."""
    have = {a.role: a for a in db.query(Agent).all()}
    for spec in DEFAULT_AGENTS:
        row = have.get(spec["role"]) or Agent(role=spec["role"])
        for k, v in spec.items():
            setattr(row, k, v)
        db.add(row)
    db.commit()


def get_agent(db, role: str) -> Agent:
    return db.query(Agent).filter_by(role=role).one()
