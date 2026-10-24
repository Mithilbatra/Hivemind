
from app.services import context, repo_state
from app.services.tasks import create_tasks


def test_context_generation(repo, make_repo):
    ctx = context.build_context(repo.path)
    assert ctx["file_count"] >= 4 and "Python" in ctx["languages"]
    assert "tests/test_app.py" in ctx["tests"]
    assert any(m["dir"] == "src" for m in ctx["modules"])
    assert "README.md" in ctx["docs"] and ctx["git_log"]
    text = context.render(ctx, budget=300)
    assert len(text) < 400 and "truncated" in text


def test_context_cached_by_head_sha(db, repo):
    a = context.get_or_create_context(db, repo)
    b = context.get_or_create_context(db, repo)
    assert a.id == b.id
    from pathlib import Path
    (Path(repo.path) / "new.txt").write_text("x")
    repo_state.git(repo.path, "add", "-A")
    repo_state.git(repo.path, "commit", "-m", "more")
    assert context.get_or_create_context(db, repo).id != a.id


def test_suggest_validation():
    ctx = {"npm_scripts": {"test": "x", "lint": "y", "build": "z"}, "tests": [], "manifests": {}}
    assert [c["command"] for c in context.suggest_validation(ctx)] == ["npm test", "npm run lint", "npm run build"]


def test_diff_capture_and_snapshot(db, repo):
    from pathlib import Path
    t = create_tasks(db, title="x", repo_ids=[repo.id])[0]
    bw = repo_state.create_worktree(db, t, repo)
    assert Path(bw.path).is_dir() and bw.branch == f"orch/task-{t.id}"
    before = repo_state.snapshot(bw.path)
    (Path(bw.path) / "src/app.py").write_text("def hello():\n    return 'changed'\n")
    (Path(bw.path) / "new.py").write_text("a = 1\nb = 2\n")
    (Path(bw.path) / "__pycache__").mkdir()
    (Path(bw.path) / "__pycache__/x.pyc").write_text("junk")
    after = repo_state.snapshot(bw.path)
    assert repo_state.changed_between(before, after) == ["new.py", "src/app.py"]
    d = repo_state.capture_diff(db, t, bw)
    assert {f["path"] for f in d.files} == {"new.py", "src/app.py"}
    assert d.additions == 3 and "+b = 2" in d.patch
    assert bw.touched_files == ["new.py", "src/app.py"]
    # main checkout stays untouched
    assert "changed" not in (Path(repo.path) / "src/app.py").read_text()


def test_conflict_detection(db, repo):
    from pathlib import Path
    t1, t2 = create_tasks(db, title="a", repo_ids=[repo.id])[0], create_tasks(db, title="b", repo_ids=[repo.id])[0]
    bw1, bw2 = repo_state.create_worktree(db, t1, repo), repo_state.create_worktree(db, t2, repo)
    (Path(bw1.path) / "src/app.py").write_text("one\n")
    repo_state.capture_diff(db, t1, bw1)
    assert bw1.conflict_with == []
    (Path(bw2.path) / "src/app.py").write_text("two\n")
    repo_state.capture_diff(db, t2, bw2)
    assert bw2.conflict_with == [{"task_id": t1.id, "files": ["src/app.py"]}]
    db.refresh(bw1)
    assert bw1.conflict_with == []  # t2's worker must not write t1's row (that deadlocked parallel workers)...
    assert repo_state.find_conflicts(db, bw1) == [{"task_id": t2.id, "files": ["src/app.py"]}]  # ...readers see both


def test_suggest_validation_python():
    base = {"npm_scripts": {}, "tests": ["tests/test_a.py"], "manifests": {}}
    assert context.suggest_validation(base)[0]["command"] == "python -m unittest discover -s tests"
    base["manifests"] = {"pyproject.toml": "[tool.pytest.ini_options]\n[tool.ruff]"}
    assert [c["command"] for c in context.suggest_validation(base)] == ["python -m pytest -q", "ruff check ."]
