"""Structured prompt assembly with a hard character budget."""
from pathlib import Path

from ..config import get_settings

# Sections are trimmed in this order when the prompt is over budget.
TRIM_ORDER = ["team", "prior", "files", "repo", "goal", "criteria", "constraints", "validation", "failure", "feedback",
              "task"]


def select_relevant_files(worktree, tree: list[str], text: str, limit: int = 4, lines: int = 60) -> list[tuple[str, str]]:
    words = {w for w in "".join(c if c.isalnum() else " " for c in text.lower()).split() if len(w) > 3}
    scored = sorted(((sum(w in f.lower() for w in words), f) for f in tree), reverse=True)
    out = []
    for score, f in scored[:limit]:
        if score == 0:
            break
        p = Path(worktree) / f
        if p.is_file():
            out.append((f, "\n".join(p.read_text(errors="replace").splitlines()[:lines])))
    return out


def _fit(sections: dict[str, str], budget: int) -> dict[str, str]:
    over = sum(len(v) for v in sections.values()) - budget
    for name in TRIM_ORDER:
        if over <= 0:
            break
        text = sections.get(name)
        if not text:
            continue
        cut = min(over, max(0, len(text) - 200))  # keep at least a stub of each section
        if cut:
            sections[name] = text[: len(text) - cut] + "\n…[truncated]"
            over -= cut - len("\n…[truncated]")
    return sections


def build_prompt(*, agent, task, top, repo_text: str, files: list[tuple[str, str]], validation: list[dict],
                 kind: str = "task", failure: str = "", feedback: str = "", prior: list[str] | None = None,
                 extra: str = "", project_goal: str = "", team: list[str] | None = None) -> str:
    s: dict[str, str] = {
        "task": f"## Task: {task.title}\n{task.description}\n{extra}".strip(),
        "goal": f"## Project goal (this task is one part of it)\n{project_goal}" if project_goal else "",
        "team": "## Other agents are working on these tasks in the same project; do not edit their files\n"
        + "\n".join(team) if team else "",
        "criteria": "## Acceptance criteria\n" + (top.acceptance_criteria or "(none given)"),
        "constraints": "## Constraints\n" + "\n".join(f"- {c}" for c in agent.constraints)
        + "\n- Work only inside the current directory.\n- Do not run git commit, push or reset.",
        "repo": "## Repository summary\n" + repo_text,
        "files": "## Possibly relevant files\n" + "\n\n".join(f"### {p}\n{c}" for p, c in files) if files else "",
        "validation": "## Validation (run after you finish)\n" + ("\n".join(f"- {v['kind']}: {v['command']}" for v in validation) or "(none configured)"),
        "failure": f"## Validation failed - fix this\n{failure}" if failure else "",
        "feedback": f"## Reviewer feedback to address\n{feedback}" if feedback else "",
        "prior": "## Earlier steps in this task\n" + "\n".join(prior) if prior else "",
    }
    s = _fit(s, get_settings().max_prompt_chars)
    order = ["task", "failure", "feedback", "goal", "criteria", "constraints", "validation", "team", "prior", "repo",
             "files"]
    body = "\n\n".join(s[k] for k in order if s.get(k))
    return f"{agent.system_prompt}\n\n{body}"


def failure_report(results) -> str:
    """Focused text about failing checks for a debug prompt."""
    parts = []
    for r in results:
        if not r.passed:
            parts.append(f"$ {r.command}   (exit {r.exit_code})\n{r.output_tail[-3000:]}")
    return "\n\n".join(parts)
