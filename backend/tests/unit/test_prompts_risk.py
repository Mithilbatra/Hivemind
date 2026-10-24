from types import SimpleNamespace as NS

from app.config import get_settings
from app.services import prompts, risk


def agent():
    return NS(system_prompt="SYS", constraints=["c1"], role="writer")


def task(**kw):
    d = dict(title="T", description="D", acceptance_criteria="AC", risk_level="low")
    d.update(kw)
    return NS(**d)


def test_prompt_contains_all_sections(env):
    t = task()
    p = prompts.build_prompt(agent=agent(), task=t, top=t, repo_text="REPO", files=[("a.py", "code")],
                             validation=[{"kind": "test", "command": "pytest"}], failure="BOOM", prior=["- earlier"])
    for needle in ["SYS", "## Task: T", "AC", "c1", "REPO", "### a.py", "pytest", "BOOM", "earlier"]:
        assert needle in p


def test_prompt_budget_trims_low_priority_first(env, monkeypatch):
    monkeypatch.setenv("MAX_PROMPT_CHARS", "2000")
    get_settings.cache_clear()
    t = task(description="KEEP-ME")
    p = prompts.build_prompt(agent=agent(), task=t, top=t, repo_text="R" * 10_000, files=[("a", "F" * 5000)], validation=[],
                             failure="FAIL-TEXT")
    assert len(p) < 3500
    assert "KEEP-ME" in p and "FAIL-TEXT" in p and "[truncated]" in p


def test_relevant_files(tmp_path):
    (tmp_path / "login.py").write_text("x = 1\n")
    (tmp_path / "other.py").write_text("y = 1\n")
    out = prompts.select_relevant_files(tmp_path, ["login.py", "other.py"], "Add login flow")
    assert [f for f, _ in out] == ["login.py"]


def test_risk_score_factors():
    s, reasons = risk.assess(task(risk_level="low"))
    assert s == 10
    s2, r2 = risk.assess(task(risk_level="medium", description="migrate production db"),
                         files=[{"path": "db/migrations/001.py", "additions": 100, "deletions": 0}], validation_failed=True,
                         conflicts=1)
    assert s2 > 60 and any("sensitive" in r for r in r2) and any("validation failed" in r for r in r2)
    assert risk.needs_pre_run_approval(task(risk_level="high"), 10)
    assert not risk.needs_pre_run_approval(task(risk_level="low"), 10)
    assert risk.assess(task(risk_level="critical"), files=[{"path": ".env", "additions": 9999, "deletions": 0}])[0] <= 100
