# Hivemind architecture

```
 Browser ──► web (Next.js) ──/api proxy──► api (FastAPI) ──► Postgres
                                              │                ▲
                                   enqueue    ▼                │ sessions, logs, diffs, audit
                                            Redis ──► worker (rq worker-pool, N processes)
                                                         │
                                  git worktree per task ─┴─► agent CLI (subprocess, stdin prompt)
                                                         └─► allowlisted validation commands
```

## Components (`backend/app`)

| Module | Responsibility |
|---|---|
| `states.py` | Status table and `transition()`: the only place status changes; writes an audit event |
| `services/orchestrator.py` | `advance(task_id)`: idempotent driver (plan → run → validate → review). Safe to re-run after a crash, approval or retry |
| `services/planner.py` | Goal → DAG; cycle/unknown-dependency checks; template fallback |
| `services/context.py` | Repo tree, manifests, tests, git log, module summary read from a commit's git objects; cached per commit; validation auto-detect |
| `services/prompts.py` | Sectioned prompt builder with a character budget; trims low-value sections first |
| `services/runner.py` | `CLIRunner` (subprocess, stream-json, timeout, cancel via process group) and `MockRunner` |
| `services/sessions.py` | One session: prompt/log/tool-call capture, role policy check, diff capture |
| `services/validation.py` | Runs checks through the allowlist, stores results |
| `services/repo_state.py` | Worktrees, snapshots, diff capture, conflict detection, commit |
| `services/risk.py` | Score 0–100 with reasons; pre-run gate |
| `services/review.py` | Approve / reject / request changes / force commit |
| `services/safety.py` | Command allowlist, repo-root restriction, child env without Hivemind's own variables |
| `worker/` | RQ queue, per-task Redis lock (60 s TTL + heartbeat, shared with API actions), dead-letter after the last retry |
| `api/hub.py` | Hive snapshot: running sessions per role, project boards, event feed |
| `api/` | REST endpoints; `auth.py` scrypt + HMAC-signed tokens |

## Data model

`users`, `projects` (goal, validation commands, allowed roots, retries), `repositories`, `repo_contexts`, `agents`, `tasks` (self-referential: top-level task → subtasks), `task_dependencies`, `sessions`, `prompts`, `output_logs` (first 200k chars per session; the whole log is a file on disk, served by `/sessions/{id}/log`), `validation_results`, `diffs`, `audit_events`, `approvals` (`pre_run` / `pre_commit`), `branch_worktrees` (branch, base sha, touched files, conflicts, commit sha). Migrations: `backend/migrations/versions/`.

## Status machine

```
queued → planning → assigned → running → validation → waiting_for_review → completed
   ▲        │           │         ▲  │         │  └──────────────► running (request changes)
   │        ▼           ▼         └──┼─────────┘ (validation retry)
   └──── blocked ◄──────┴────────────┘          failed ──► queued (retry) / completed (force commit)
  (pre-run approval:                            cancelled ──► queued (retry)
   back to planning)
```
Subtasks use `queued → assigned → running → completed | failed | cancelled`.

## Safety model

* Repo access fails closed: `ALLOWED_REPO_ROOTS` (empty = nothing), optional per-project roots, symlinks resolved. Prompt context comes from git objects, and files read from a checkout must resolve inside it, so a symlink can't pull outside files into a prompt.
* Commands: `shlex` → argv, `shell=False`, executable allowlist, denied tokens (`rm`, `push`, `--force`, `curl`, inline `-c`/`-e`), read-only git only. Blocked commands are recorded as failed checks.
* Agents run in a git worktree (their working directory), with Hivemind's own variables removed from their environment, and with per-role tool policies plus a post-session file-policy check. This is not a sandbox: they run as the worker's user, so they can read the worker's environment via `/proc`. The worker therefore gets no login secrets, but it does hold `DATABASE_URL`. Git hooks never run (`core.hooksPath=/dev/null`).
* Nothing is committed without a `pre_commit` approval; high-risk tasks also need a `pre_run` approval. Validation failure fails the task closed.
* Every command, decision and policy event is in the audit log.
* Browser side: API and web both refuse non-localhost `Host` headers (DNS rebinding), pages can't be framed, logins lock after 10 failures, and `make setup` generates the admin password.

## Operations

Retries use exponential backoff (`RETRY_BACKOFF_S`), sessions and validation commands time out (an agent that hangs after an error result, or leaves a child holding its pipes, is still stopped), jobs have RQ retries and an `on_failure` hook that dead-letters the task. Output is bounded everywhere: check output keeps an 8000-character tail, agent output is read through a bounded queue, and log files stop at 50M characters. `/health/ready` checks DB and Redis; `/health/workers` reports queue depth, failed-job count and live workers. Logs are JSON on stdout.
