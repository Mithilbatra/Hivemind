import subprocess

import fakeredis
import pytest

from app import db as dbmod
from app.config import get_settings
from app.main import bootstrap
from app.models import Project
from app.services import runner
from app.worker import queue

CHECK = "import mock_feature\nassert mock_feature.VALUE == 1, 'VALUE is %r' % mock_feature.VALUE\n"


@pytest.fixture
def env(tmp_path, monkeypatch):
    repos = tmp_path / "repos"
    repos.mkdir()
    for k, v in {"DATABASE_URL": f"sqlite:///{tmp_path}/t.db", "ALLOWED_REPO_ROOTS": str(repos),
                 "WORKTREES_DIR": str(tmp_path / "wt"), "DATA_DIR": str(tmp_path / "data"), "QUEUE_INLINE": "true",
                 "AGENT_MODE": "mock", "RETRY_BACKOFF_S": "0", "SECRET_KEY": "test", "ADMIN_USERNAME": "admin",
                 "ADMIN_PASSWORD": "pw", "MOCK_FAIL_FIRST": "false", "ALLOWED_HOSTS": "testserver"}.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    dbmod.reset_engine()
    queue.set_redis(fakeredis.FakeRedis())
    runner.set_runner(None)
    bootstrap(create_tables=True)
    yield tmp_path
    runner.set_runner(None)
    dbmod.reset_engine()
    get_settings.cache_clear()


@pytest.fixture
def db(env):
    with dbmod.session_scope() as s:
        yield s


@pytest.fixture
def project(db):
    p = db.query(Project).first()
    p.validation_commands = [{"kind": "test", "command": "python3 check.py", "required": True}]
    db.commit()
    return p


def git_env():
    return {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
            "PATH": __import__("os").environ["PATH"], "HOME": __import__("os").environ.get("HOME", "/")}


@pytest.fixture
def make_repo(env):
    def _make(name="app", files=None):
        path = env / "repos" / name
        path.mkdir(parents=True)
        files = files or {"README.md": "# demo\n", "check.py": CHECK, "src/app.py": "def hello():\n    return 'hi'\n",
                          "tests/test_app.py": "def test_ok():\n    assert True\n", ".gitignore": "__pycache__/\n"}
        for rel, content in files.items():
            f = path / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(content)
        for cmd in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "init"]):
            subprocess.run(["git", *cmd], cwd=path, check=True, env=git_env())
        return path
    return _make


@pytest.fixture
def repo(db, project, make_repo):
    from app.services.tasks import register_repo
    return register_repo(db, str(make_repo()), project)
