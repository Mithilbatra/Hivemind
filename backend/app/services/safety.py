"""Command allowlist and repo-path restrictions. Everything fails closed."""
import os
import re
import shlex
from pathlib import Path

from ..config import get_settings

ALLOWED_EXECUTABLES = {
    "python", "python3", "pytest", "ruff", "mypy", "flake8", "black",
    "npm", "npx", "pnpm", "yarn", "node", "tsc", "eslint", "vitest", "jest",
    "go", "cargo", "make", "mvn", "gradle", "git",
}
GIT_READONLY = {"status", "diff", "log", "show", "rev-parse", "ls-files", "branch"}
DENIED_TOKENS = {
    "rm", "rmdir", "sudo", "curl", "wget", "ssh", "scp", "dd", "mkfs", "chmod", "chown",
    "publish", "unpublish", "push", "deploy", "uninstall", "--force", "-rf", "--global", "-g",
}
DENIED_INTERPRETER_FLAGS = {"python": {"-c"}, "python3": {"-c"}, "node": {"-e", "--eval", "-p"}}
SHELL_META = re.compile(r"[;&|<>`$(){}\n\\]")


class UnsafeCommand(ValueError):
    pass


class UnsafeRepo(ValueError):
    pass


def validate_command(command: str) -> list[str]:
    """Return argv for an allowlisted command, or raise UnsafeCommand. Never uses a shell."""
    if SHELL_META.search(command):
        raise UnsafeCommand("shell metacharacters are not allowed")
    try:
        argv = shlex.split(command)
    except ValueError as e:
        raise UnsafeCommand(f"unparseable command: {e}")
    if not argv:
        raise UnsafeCommand("empty command")
    exe = os.path.basename(argv[0])
    if exe not in ALLOWED_EXECUTABLES:
        raise UnsafeCommand(f"executable '{exe}' is not on the allowlist")
    bad = DENIED_TOKENS.intersection(argv)
    if bad:
        raise UnsafeCommand(f"denied argument(s): {', '.join(sorted(bad))}")
    if DENIED_INTERPRETER_FLAGS.get(exe, set()).intersection(argv[1:]):
        raise UnsafeCommand(f"inline code via {exe} is not allowed")
    if exe == "git" and (len(argv) < 2 or argv[1] not in GIT_READONLY):
        raise UnsafeCommand("only read-only git subcommands are allowed")
    return argv


def resolve_repo_path(path: str, project_roots: list[str] | None = None) -> Path:
    """Resolve symlinks and require the path to be a git repo under the allowed roots."""
    p = Path(path).expanduser().resolve()
    roots = get_settings().repo_roots
    if not roots:
        raise UnsafeRepo("ALLOWED_REPO_ROOTS is empty: no repository access is configured")
    if not any(p.is_relative_to(r) for r in roots):
        raise UnsafeRepo(f"{p} is outside ALLOWED_REPO_ROOTS")
    if project_roots:
        if not any(p.is_relative_to(Path(r).resolve()) for r in project_roots):
            raise UnsafeRepo(f"{p} is outside this project's allowed roots")
    if not (p / ".git").exists():
        raise UnsafeRepo(f"{p} is not a git repository")
    return p


SECRET_ENV = {"SECRET_KEY", "DATABASE_URL", "REDIS_URL", "ADMIN_PASSWORD", "ADMIN_USERNAME", "POSTGRES_PASSWORD"}


def child_env(extra: dict | None = None) -> dict:
    """Environment for subprocesses: ours minus Hivemind's own secrets."""
    env = {k: v for k, v in os.environ.items() if k not in SECRET_ENV}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.update(extra or {})
    return env
