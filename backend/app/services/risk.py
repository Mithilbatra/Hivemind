"""Risk score 0-100 with human-readable reasons."""
import re

LEVEL_BASE = {"low": 10, "medium": 30, "high": 60, "critical": 85}
SENSITIVE_PATHS = re.compile(r"(auth|secret|credential|password|\.env|migration|dockerfile|docker-compose|\.github/workflows|"
                             r"terraform|infra|deploy|payment|billing|\.pem|\.key|package-lock|poetry\.lock)", re.IGNORECASE)
RISKY_WORDS = re.compile(r"\b(delete|drop|truncate|migrate|production|prod|secret|payment|credentials?|force)\b", re.IGNORECASE)
PRE_RUN_THRESHOLD = 60


def assess(task, files: list[dict] | None = None, validation_failed: bool = False, no_validation: bool = False,
           conflicts: int = 0) -> tuple[float, list[str]]:
    files = files or []
    score = float(LEVEL_BASE.get(task.risk_level, 10))
    reasons = [f"declared risk level: {task.risk_level}"]
    if RISKY_WORDS.search(f"{task.title} {task.description}"):
        score += 15
        reasons.append("description mentions a risky operation (delete/migrate/production/secret...)")
    sens = [f["path"] for f in files if SENSITIVE_PATHS.search(f["path"])]
    if sens:
        score += min(30, 10 * len(sens))
        reasons.append("touches sensitive paths: " + ", ".join(sens[:5]))
    churn = sum(f["additions"] + f["deletions"] for f in files)
    if churn:
        score += min(20, churn / 50)
        reasons.append(f"{len(files)} files, {churn} changed lines")
    if conflicts:
        score += 15
        reasons.append(f"overlapping edits with {conflicts} other task(s)")
    if no_validation:
        score += 10
        reasons.append("no validation commands configured")
    if validation_failed:
        score += 25
        reasons.append("validation failed")
    return min(100.0, round(score, 1)), reasons


def needs_pre_run_approval(task, score: float) -> bool:
    return task.risk_level in ("high", "critical") or score >= PRE_RUN_THRESHOLD
