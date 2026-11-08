import pytest

from app.services import safety
from app.services.safety import UnsafeCommand, UnsafeRepo


@pytest.mark.parametrize("cmd", ["pytest -q", "python3 check.py", "npm run lint", "git status", "ruff check ."])
def test_allowed(cmd):
    assert safety.validate_command(cmd)


@pytest.mark.parametrize("cmd", [
    "rm -rf /", "pytest; rm x", "pytest && curl x", "echo $(whoami)", "pytest | tee f", "bash -c ls", "git push origin main",
    "git reset --hard", "python -c 'import os'", "npm publish", "npm install -g x", "node -e 1", "", "curl http://x",
])
def test_blocked(cmd):
    with pytest.raises(UnsafeCommand):
        safety.validate_command(cmd)


def test_repo_roots(env, make_repo):
    ok = make_repo("ok")
    assert safety.resolve_repo_path(str(ok)) == ok.resolve()
    with pytest.raises(UnsafeRepo, match="outside"):
        safety.resolve_repo_path("/etc")
    (env / "repos" / "plain").mkdir()
    with pytest.raises(UnsafeRepo, match="not a git"):
        safety.resolve_repo_path(str(env / "repos" / "plain"))
    with pytest.raises(UnsafeRepo, match="project"):
        safety.resolve_repo_path(str(ok), project_roots=[str(env / "elsewhere")])


def test_symlink_escape_rejected(env, tmp_path):
    outside = tmp_path / "outside"
    (outside / ".git").mkdir(parents=True)
    (env / "repos" / "link").symlink_to(outside)
    with pytest.raises(UnsafeRepo, match="outside"):
        safety.resolve_repo_path(str(env / "repos" / "link"))


def test_empty_roots_fail_closed(env, monkeypatch):
    from app.config import get_settings
    monkeypatch.setenv("ALLOWED_REPO_ROOTS", "")
    get_settings.cache_clear()
    with pytest.raises(UnsafeRepo, match="empty"):
        safety.resolve_repo_path(str(env))


def test_child_env_strips_secrets(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "s3")
    monkeypatch.setenv("REDIS_PASSWORD", "r3")
    assert not {"SECRET_KEY", "REDIS_PASSWORD"} & set(safety.child_env())


def test_checks_never_run_stale_bytecode(tmp_path):
    """A same-size edit with the same whole-second mtime used to be hidden by the .pyc of the previous run."""
    import os
    import sys

    from app.services.validation import run_cmd
    src = tmp_path / "mod.py"
    src.write_text("VALUE = 0\n")
    show = [sys.executable, "-c", "import mod; print(mod.VALUE)"]
    assert run_cmd(show, str(tmp_path), 30) == (0, "0\n")
    st = src.stat()
    src.write_text("VALUE = 1\n")
    os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns))  # what a fast agent produces: same size, same second
    assert run_cmd(show, str(tmp_path), 30) == (0, "1\n")
    assert not (tmp_path / "__pycache__").exists()  # and no bytecode lands in the worktree or its diff
