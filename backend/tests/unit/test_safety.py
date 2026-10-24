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
    assert "SECRET_KEY" not in safety.child_env()
