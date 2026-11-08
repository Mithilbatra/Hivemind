import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from app import preflight

BACKEND = Path(__file__).resolve().parents[2]


def settings(admin="s3cret-admin", db="postgresql+psycopg://orchestrator:9f2c41@db:5432/orchestrator",
             redis="redis://:7ab1e0@redis:6379/0"):
    return NS(admin_password=admin, database_url=db, redis_url=redis)


def test_random_passwords_pass():
    assert preflight.problems(settings()) == []


@pytest.mark.parametrize("kw, name", [
    ({"admin": "change-me"}, "ADMIN_PASSWORD"),
    ({"admin": ""}, "ADMIN_PASSWORD"),
    ({"db": "postgresql+psycopg://orchestrator:orchestrator@db:5432/orchestrator"}, "POSTGRES_PASSWORD"),
    ({"redis": "redis://redis:6379/0"}, "REDIS_PASSWORD"),
    ({"redis": "redis://:change-me@redis:6379/0"}, "REDIS_PASSWORD"),
])
def test_missing_or_example_passwords_are_refused(kw, name):
    bad = preflight.problems(settings(**kw))
    assert len(bad) == 1 and bad[0].startswith(name)


def test_the_worker_has_no_admin_password_and_sqlite_has_none():
    assert preflight.problems(settings(admin="", db="sqlite:///x.db"), admin=False) == []


def worker(*args, **env):
    e = {**os.environ, "DATABASE_URL": "sqlite:///unused.db", **env}
    return subprocess.run([sys.executable, *args], cwd=BACKEND, env=e, capture_output=True, text=True, timeout=60)


def test_worker_entry_runs_rq_and_refuses_an_open_redis():
    ok = worker("-m", "app.worker", "--help", REDIS_URL="redis://:4f9a1c@localhost:6379/0")
    assert ok.returncode == 0 and "--num-workers" in ok.stdout
    refused = worker("-m", "app.worker", "--help", REDIS_URL="redis://localhost:6379/0")
    assert refused.returncode == 1 and "REDIS_PASSWORD" in refused.stderr


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="prctl is Linux-only")
def test_worker_processes_are_not_dumpable():
    r = worker("-c", "import ctypes; from app.worker.__main__ import harden; harden(); "
                     "print(ctypes.CDLL(None).prctl(3, 0, 0, 0, 0))")  # 3 = PR_GET_DUMPABLE
    assert r.stdout.strip() == "0", r.stderr


def test_worker_finds_a_dead_workers_job_within_about_two_minutes():
    import fakeredis

    from app.worker.queue import Worker
    w = Worker(["hivemind"], connection=fakeredis.FakeStrictRedis())
    assert (w.maintenance_interval, w.dequeue_timeout, w.job_monitoring_interval) == (30, 30, 10)
