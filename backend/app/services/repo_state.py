"""Git plumbing: worktrees, diff capture, file tracking, conflict detection, commit."""
import hashlib
import os
import shutil
import subprocess
import uuid
from functools import cache
from pathlib import Path

from ..config import get_settings
from ..models import BranchWorktree, Diff
from . import safety
from .audit import emit

JUNK = ["__pycache__/", "*.pyc", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/", "node_modules/", ".venv/", ".next/",
        "*.egg-info/"]


class GitError(RuntimeError):
    pass


@cache
def _excludes(data_dir: Path) -> str:
    """Written once per process, atomically: other workers may be running git against it."""
    f = data_dir / "hivemind.gitignore"
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text("\n".join(JUNK) + "\n")
    os.replace(tmp, f)
    return str(f.resolve())


def git(cwd, *args: str, check: bool = True) -> str:
    s = get_settings()
    # Hooks off: a relative core.hooksPath (husky) would run hook files the agent can edit in the worktree.
    # quotePath off: non-ASCII file names come back as real paths, not "caf\303\251.txt".
    cmd = ["git", "-c", f"core.excludesFile={_excludes(s.data_dir)}", "-c", f"user.name={s.git_author_name}",
           "-c", f"user.email={s.git_author_email}", "-c", f"core.hooksPath={os.devnull}", "-c", "core.quotePath=false",
           *args]
    # errors="replace": a Latin-1 file in a diff must not crash the session
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, errors="replace", timeout=120, env=safety.child_env())
    if check and r.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout


def head_sha(path) -> str:
    return git(path, "rev-parse", "HEAD").strip()


def resolve_ref(path, ref: str) -> str | None:
    """Commit sha for a branch/tag/sha, or None. `--end-of-options` stops a ref like '--foo' being read as a flag."""
    out = git(path, "rev-parse", "--verify", "-q", "--end-of-options", ref + "^{commit}", check=False).strip()
    return out or None


def create_worktree(db, task, repo) -> BranchWorktree:
    """Idempotently create an isolated worktree + branch for a top-level task."""
    bw = db.query(BranchWorktree).filter_by(task_id=task.id).one_or_none()
    if bw and Path(bw.path).exists():
        return bw
    base_ref = task.branch_target or repo.default_branch
    base_sha = resolve_ref(repo.path, base_ref)
    if base_sha is None:  # never fall back to whatever is checked out: the diff would be against the wrong base
        raise GitError(f"base branch '{base_ref}' not found in {repo.path}")
    branch = bw.branch if bw else f"hivemind/task-{task.id}"
    path = (get_settings().worktrees_dir / f"task-{task.id}").resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.rmtree(path)
    git(repo.path, "worktree", "prune")
    exists = bool(git(repo.path, "branch", "--list", branch).strip())
    if exists and bw is None:  # left over from an earlier database (ids restart at 1): never adopt its commits
        branch, exists = f"{branch}-{uuid.uuid4().hex[:6]}", False
    if exists:  # our own branch, resuming after the checkout was lost
        git(repo.path, "worktree", "add", str(path), branch)
        base_sha = git(path, "merge-base", branch, base_sha).strip() or base_sha
    else:
        git(repo.path, "worktree", "add", "-b", branch, str(path), base_sha)
    if bw is None:
        bw = BranchWorktree(task_id=task.id, repo_id=repo.id, branch=branch)
        db.add(bw)
    bw.base_branch, bw.base_sha, bw.path, bw.active = base_ref, base_sha, str(path), True
    emit(db, task.id, "worktree_created", f"{branch} at {base_sha[:8]}", {"path": str(path)})
    db.commit()
    return bw


def snapshot(path) -> dict[str, str]:
    """path -> content hash for every file that differs from HEAD (including untracked)."""
    out = git(path, "status", "--porcelain=v1", "-z", "-uall")
    snap = {}
    entries = out.split("\0")
    i = 0
    while i < len(entries):
        e = entries[i]
        i += 1
        if len(e) < 4:
            continue
        status, name = e[:2], e[3:]
        if status[0] in "RC":  # rename/copy: next entry is the origin path
            i += 1
        f = Path(path) / name
        snap[name] = hashlib.sha1(f.read_bytes()).hexdigest() if f.is_file() else "deleted"
    return snap


def changed_between(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))


def capture_diff(db, task, bw: BranchWorktree, session_id: int | None = None) -> Diff:
    """Store the cumulative diff of the worktree against its base, and update touched files."""
    git(bw.path, "add", "-N", ".")  # intent-to-add so untracked files appear in the diff
    # --no-renames: a rename shows as delete + add, so every path is a real file path (numstat, conflicts, UI)
    patch = git(bw.path, "diff", "--no-renames", bw.base_sha)
    numstat = git(bw.path, "diff", "--no-renames", "--numstat", bw.base_sha)
    files = []
    for line in numstat.splitlines():
        a, d, name = line.split("\t", 2)
        files.append({"path": name, "additions": int(a) if a.isdigit() else 0, "deletions": int(d) if d.isdigit() else 0})
    cap = get_settings().max_diff_chars
    d = Diff(task_id=task.id, session_id=session_id, patch=patch[:cap], files=files, truncated=len(patch) > cap,
             additions=sum(f["additions"] for f in files), deletions=sum(f["deletions"] for f in files))
    db.add(d)
    bw.touched_files = [f["path"] for f in files]
    db.commit()
    detect_conflicts(db, task, bw)
    return d


def find_conflicts(db, bw: BranchWorktree) -> list[dict]:
    """Files this worktree shares with other active worktrees of the same repo. Read-only; call it to display."""
    mine = set(bw.touched_files or [])
    if not mine:
        return []
    others = db.query(BranchWorktree).filter(BranchWorktree.repo_id == bw.repo_id, BranchWorktree.active.is_(True),
                                             BranchWorktree.task_id != bw.task_id).order_by(BranchWorktree.task_id)
    return [{"task_id": o.task_id, "files": sorted(mine & set(o.touched_files or []))}
            for o in others if mine & set(o.touched_files or [])]


def detect_conflicts(db, task, bw: BranchWorktree) -> list[dict]:
    """Record overlaps on this task's own row only. Writing the other task's row too made parallel workers
    lock each other's rows in opposite order and deadlock; readers call find_conflicts for a live view."""
    found = find_conflicts(db, bw)
    known = {c["task_id"] for c in bw.conflict_with or []}
    new = [c["task_id"] for c in found if c["task_id"] not in known]
    if new:
        emit(db, task.id, "conflict_warning", f"overlapping edits with task(s) {new}", {"conflicts": found})
    bw.conflict_with = found
    db.commit()
    return found


def commit(db, task, bw: BranchWorktree, message: str) -> str:
    git(bw.path, "add", "-A")
    if not git(bw.path, "status", "--porcelain").strip():
        emit(db, task.id, "commit_skipped", "nothing to commit")
    else:
        git(bw.path, "commit", "-m", message)
    bw.commit_sha = head_sha(bw.path)
    emit(db, task.id, "committed", f"{bw.branch} @ {bw.commit_sha[:8]}", {"sha": bw.commit_sha})
    db.commit()
    return bw.commit_sha


def release_worktree(db, bw: BranchWorktree, repo, delete_branch: bool = False) -> None:
    """Remove the checkout (the branch is kept unless delete_branch)."""
    git(repo.path, "worktree", "remove", "--force", bw.path, check=False)
    if delete_branch:
        git(repo.path, "branch", "-D", bw.branch, check=False)
    bw.active = False
    db.commit()
