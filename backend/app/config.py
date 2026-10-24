import logging
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://orchestrator:orchestrator@localhost:5432/orchestrator"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = "dev-secret-change-me"
    admin_username: str = "admin"
    admin_password: str = "admin"
    token_ttl_s: int = 12 * 3600

    # Agent execution. `cli` runs AGENT_COMMAND as a subprocess, `mock` uses a scripted fake.
    agent_mode: str = "cli"
    agent_command: str = ""  # e.g. see .env.example; the prompt is written to its stdin
    agent_tool_flags: bool = True  # append --allowedTools/--disallowedTools from the role's policy
    mock_fail_first: bool = False  # mock writer produces a failing change until it gets a debug prompt
    mock_step_s: float = 0  # pause between mock agent output lines

    # Comma-separated absolute paths; a repo must live under one of them. Empty = nothing allowed.
    allowed_repo_roots: str = ""
    worktrees_dir: Path = Path("./data/worktrees")
    data_dir: Path = Path("./data")

    session_timeout_s: int = 900
    validation_timeout_s: int = 600
    max_retries: int = 2
    retry_backoff_s: float = 2.0
    worker_concurrency: int = 2
    queue_inline: bool = False  # run jobs synchronously in the caller (tests)

    max_prompt_chars: int = 24000
    max_log_chars: int = 200_000  # per session kept in DB; full log stays on disk
    max_diff_chars: int = 500_000

    git_author_name: str = "Orchestrator"
    git_author_email: str = "orchestrator@localhost"

    @property
    def repo_roots(self) -> list[Path]:
        return [Path(p).resolve() for p in self.allowed_repo_roots.split(",") if p.strip()]


PLACEHOLDER_SECRETS = {"", "dev-secret-change-me", "change-me-to-a-long-random-string"}


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.secret_key in PLACEHOLDER_SECRETS:
        # A published key lets anyone forge a login token. A random one only costs a re-login after restarts.
        # shortcut: per-process key, so it breaks if the API ever runs as several processes; set SECRET_KEY then.
        logging.getLogger("orchestrator").warning("SECRET_KEY is unset or a placeholder; using a random key")
        s.secret_key = secrets.token_hex(32)
    return s
