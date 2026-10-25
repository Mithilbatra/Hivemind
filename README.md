# Hivemind

<p><img src="frontend/app/icon.svg" width="64" alt="Hivemind logo: a honeycomb cell holding a small network"></p>

**Many agents, one mind.** Hivemind is a self-hosted hive for coding agents, built around a coding-agent CLI. Submit a task in plain language; the system plans it as a DAG of subtasks, gathers repo context, runs isolated agent sessions in git worktrees, validates with your tests/lint/typecheck/build, retries on failure, gates risky work behind human approval, and keeps a full audit trail.

* Backend: Python 3.12, FastAPI, SQLAlchemy 2 + Alembic, Postgres 16, Redis + RQ
* Frontend: Next.js 14 (App Router), React, TypeScript
* Runtime: Docker Compose (`db`, `redis`, `api`, `worker`, `web`)

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design and [samples/workflows.md](samples/workflows.md) for walkthroughs.

## Run it

Requires Docker with Compose v2.

```bash
make up          # first run writes .env (random secrets, your user id), creates samples/repos/demo-app, starts everything
```

Open http://localhost:47213 and sign in as `admin` with the `ADMIN_PASSWORD` from `.env` (`change-me` by default). API docs: http://localhost:8000/docs. Both ports listen on 127.0.0.1 only.

The home page is **the Hive**: a honeycomb showing which agents are working, plus one card per agent role (planner, researcher, writer, tester, reviewer) showing what each is working on right now and what its last output line says, a board per project (queued, agents working, needs you, done, stopped) and a live activity feed. Up to `WORKER_CONCURRENCY` tasks (default 4) run at the same time, so several agents work in parallel.

**Projects** group tasks under one big goal. Create one from the hub ("+ New project"), give it a goal, and add tasks to it. Every agent prompt includes the project goal and a list of the other in-flight tasks in the project with the files they are editing, so agents working in parallel stay out of each other's way.

The default `.env` uses `AGENT_MODE=mock`, a scripted fake agent, so the whole pipeline (plan, session, validation, retry, review, commit) runs without any credentials. `MOCK_FAIL_FIRST=true` makes the first implementation attempt fail validation so you can watch the retry loop.

Try it from the CLI (needs `jq`):

```bash
samples/submit.sh 0     # "Add a farewell function" against samples/repos/demo-app
```

### Using a real agent

Set in `.env`:

```
AGENT_MODE=cli
AGENT_COMMAND=<your agent CLI and flags>   # reads the prompt on stdin, runs headless in the worktree
```

The worker container needs that CLI installed and logged in, which the stock image does not do. Extend `backend/Dockerfile` (or build a derived image) to install it and pass its credentials to the `worker` service. The runner understands stream-json output (tool calls and a final result event); anything else is captured as plain text. With `AGENT_TOOL_FLAGS=true` each role's tool policy is appended as `--allowedTools` / `--disallowedTools`; set it to `false` if your CLI uses other flags (read-only is then enforced only by the post-session check below).

### Your own repositories

Put repos under `REPOS_DIR` (mounted at `/repos` in the containers), or point `REPOS_DIR` at the directory that holds them. Register one in **Settings → Repositories** or by typing its path in the task form. Anything outside `ALLOWED_REPO_ROOTS` is refused.

### Development without rebuilding images

```bash
cd backend && python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest tests                           # 97 tests, no Docker needed (SQLite + fakeredis)
.venv/bin/ruff check .
cd ../frontend && npm install && npm run typecheck && npm run build
```

## Key API endpoints (all under `/api`, bearer token from `POST /auth/login`)

| Method | Path | Purpose |
|---|---|---|
| POST | `/tasks` | Create task(s) (one per repo when several are given) |
| GET | `/tasks`, `/tasks/{id}` | List (filters: `status`, `repo_id`, `q`) / detail with subtask DAG, sessions, approvals, branch |
| PATCH | `/tasks/{id}/status` | Manual change: `cancelled`, `queued` (retry), `blocked` (hold) |
| POST | `/tasks/{id}/run`, `/retry`, `/cancel` | Lifecycle controls |
| POST | `/tasks/{id}/approval` | `approve`, `reject`, `request_changes`, `force_commit` (needs `reason` and `confirm`) |
| GET | `/tasks/{id}/logs` `/diff` `/validation` `/events` | Output (paged), latest diff, validation report, audit timeline |
| GET | `/sessions` | Agent sessions |
| POST/GET | `/repos`, `/repos/{id}/context` | Register repos; build / read cached repo context |
| GET | `/hub` | Hive snapshot: active sessions per role with a readable "doing now" line, per-project boards, event feed, workers |
| GET/POST/PUT | `/projects`, GET `/agents`, `/settings`, `/dead-letter` | Projects (name, goal, checks), configuration, dead-letter list |
| GET | `/health`, `/health/ready`, `/health/workers` | Liveness, readiness (DB + Redis), queue depth and workers |

## How Hivemind works

1. **Create.** The API checks the repo is a git repo with commits under the allowed roots, that the target branch exists, and that any validation commands pass the allowlist, stores the task as `queued` and enqueues one job. One worker at a time owns a task through a Redis lock with a 60 s TTL that a heartbeat keeps alive; API actions (approve, retry, cancel) take the same lock, so they never race a worker mid-step.
2. **Plan** (`planning`). A worktree and branch `hivemind/task-<id>` are created from the target branch. Repo context (tree, manifests, tests, git log, module summary) is built, cached per HEAD sha. The read-only planner agent is asked for a JSON DAG; if it is missing or invalid (cycle, unknown dependency) a deterministic template for feature/bugfix is used and the fallback is logged. If the planner edits files, the task fails. Every prompt also carries the project goal and the other agents' in-flight tasks. Subtasks are routed to roles: researcher, planner, writer, tester, reviewer.
3. **Risk gate** (`blocked`). Declared risk `high`/`critical`, or a score of 60+ from the description, requires a human *pre-run* approval before any code agent runs.
4. **Run** (`assigned` → `running`). Subtasks execute in dependency order inside the one worktree. Each gets a session with a structured prompt (task, acceptance criteria, constraints, validation commands, repo summary, relevant files, earlier steps), within a character budget. stdout/stderr, tool calls and a cumulative diff are captured. After each session the changed files are compared with the role's policy: a read-only role that touches files, or the tester editing non-test files, fails the session. Crashed or failed sessions retry with exponential backoff.
5. **Validate** (`validation`). The project's checks run (or are auto-detected from manifests). Commands are parsed to argv and must pass the allowlist; no shell. On a failure the output tail is sent back to the writer as a focused debug prompt, up to `max_retries`. Exhausted retries fail the task closed and put it in the dead-letter list.
6. **Review** (`waiting_for_review`). Diff, summary, validation status, risk score and conflict warnings are shown. Approve commits on the task branch and removes the worktree; reject deletes the branch; request changes sends feedback to the writer and loops back through validation; force commit bypasses a failed validation (reason + confirmation, audit-logged).
7. **Audit.** Every status change, command, session, retry, decision and warning is an `audit_events` row, shown as the task timeline.

## Known tradeoffs and caveats

* **Nothing is merged or pushed.** Approval commits to `hivemind/task-<id>` in your repo; merging or opening a PR is yours. GitHub/GitLab integration is not built.
* **Subtasks run serially inside one worktree.** Parallelism is across tasks and repos, not within a task's DAG. Concurrent tasks on one repo are isolated by worktree, and overlapping edited files raise a warning, but nothing is auto-merged.
* **Tool restriction depends on the agent CLI.** The allowed/denied tool lists are only enforced if the CLI honours the flags. The post-session file check is the backstop, and it detects a violation after it happened (the task fails; the file is not reverted).
* **Validation commands are allowlisted executables, not a sandbox.** `npm test` or `pytest` still execute repo code with the worker's privileges. Run workers in containers (as provided), not on a machine with credentials you care about.
* **Single tenant.** One admin user from the environment, signed bearer token, no per-user permissions. `make setup` writes a random `SECRET_KEY`; a placeholder key is replaced by a random one at startup (logins then reset on restart). Change `ADMIN_PASSWORD`.
* **A worker that dies mid-task** (container restart, OOM) frees the task within 60 s; RQ retries the job twice and the task resumes from its last finished step, otherwise it lands in the dead-letter list. Sessions it left running are marked `abandoned`.
* **The worker image ships Python and git only.** Validation runs inside it, so `npm test` or `python -m pytest` fail until you add your toolchain (Node, pytest, …) to `backend/Dockerfile`.
* **Worktrees live in the `orch-data` volume, not next to your repo.** Your repo's `.git/worktrees` entries point at container paths, so don't run `git worktree prune` on the host while tasks are in flight.
* **No live streaming.** The UI polls every 2–3 s; agent output reaches the database about once a second.
* **Not verified against a real agent CLI** in this build (no credentials in the build environment). The CLI runner is tested against a stand-in script covering stdin prompt, stream-json parsing, timeout, cancellation and tool flags.

Not built: PR/branch management beyond local branches, cost/SLA tracking, OpenTelemetry, pgvector memory, metrics dashboard, task templates beyond the planner defaults.

## Assumptions

Git repos are local directories; the default branch is whatever is checked out when the repo is registered; commits are authored as `GIT_AUTHOR_NAME`/`GIT_AUTHOR_EMAIL`; an empty `validation_commands` means auto-detect, and if nothing is detected the task still requires review and its risk score goes up.
