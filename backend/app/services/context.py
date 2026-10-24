"""Compact repo context: tree, manifests, tests, git history, heuristic architecture summary."""
import json
import re
from collections import Counter
from pathlib import Path

from sqlalchemy.exc import IntegrityError

from ..models import RepoContext
from .repo_state import git, head_sha

MANIFESTS = ["package.json", "pyproject.toml", "requirements.txt", "setup.py", "go.mod", "Cargo.toml", "pom.xml",
             "build.gradle", "Makefile", "docker-compose.yml", "Dockerfile", "tsconfig.json", "pytest.ini", "tox.ini",
             "ruff.toml", ".eslintrc.json", ".eslintrc.js"]
KEY_DOCS = ["README.md", "README.rst", "ARCHITECTURE.md", "CONTRIBUTING.md", "docs/ARCHITECTURE.md"]
TEST_RE = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*$|_test\.[a-z]+$|\.(test|spec)\.[a-z]+$")
LANGS = {".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript",
         ".go": "Go", ".rs": "Rust", ".java": "Java", ".rb": "Ruby", ".c": "C", ".cpp": "C++", ".cs": "C#"}
FRAMEWORKS = ["fastapi", "flask", "django", "express", "next", "react", "vue", "nestjs", "spring", "gin", "rails",
              "pytest", "jest", "vitest", "sqlalchemy", "prisma"]
DIR_HINTS = {"src": "main source", "app": "application code", "api": "HTTP API", "backend": "server side",
             "frontend": "client side", "web": "web client", "tests": "tests", "test": "tests", "docs": "documentation",
             "scripts": "tooling scripts", "migrations": "database migrations", "lib": "shared libraries",
             "cmd": "entrypoints", "pkg": "packages", "infra": "infrastructure", "deploy": "deployment"}


def _read(path: Path, limit: int) -> str:
    try:
        return path.read_text(errors="replace")[:limit]
    except OSError:
        return ""


def build_context(repo_path) -> dict:
    root = Path(repo_path)
    files = [f for f in git(root, "ls-files").splitlines() if f]
    langs = Counter(LANGS[Path(f).suffix] for f in files if Path(f).suffix in LANGS)
    tops: dict[str, list[str]] = {}
    for f in files:
        tops.setdefault(f.split("/")[0] if "/" in f else ".", []).append(f)
    modules = []
    for name, fs in sorted(tops.items()):
        if name == ".":
            continue
        exts = Counter(LANGS[Path(f).suffix] for f in fs if Path(f).suffix in LANGS)
        modules.append({"dir": name, "files": len(fs), "purpose": DIR_HINTS.get(name.lower(), ""),
                        "languages": [k for k, _ in exts.most_common(2)]})
    manifests = {m: _read(root / m, 1500) for m in MANIFESTS if (root / m).is_file()}
    blob = " ".join(manifests.values()).lower()
    docs = {d: _read(root / d, 1500) for d in KEY_DOCS if (root / d).is_file()}
    scripts = {}
    if "package.json" in manifests:
        try:
            scripts = json.loads(_read(root / "package.json", 50_000)).get("scripts", {})
        except json.JSONDecodeError:
            pass
    return {
        "head_sha": head_sha(root),
        "branch": git(root, "rev-parse", "--abbrev-ref", "HEAD").strip(),
        "file_count": len(files),
        "languages": dict(langs.most_common()),
        "frameworks": [f for f in FRAMEWORKS if re.search(rf"\b{f}\b", blob)],
        "modules": modules,
        "root_files": sorted(f for f in tops.get(".", []))[:40],
        "tree": files[:5000],  # only used to pick relevant files, never rendered into a prompt whole
        "manifests": manifests,
        "npm_scripts": scripts,
        "tests": [f for f in files if TEST_RE.search(f)][:60],
        "docs": docs,
        "git_log": git(root, "log", "-n", "15", "--pretty=format:%h %ad %s", "--date=short", check=False).splitlines(),
    }


def render(ctx: dict, budget: int = 6000) -> str:
    """Compact text form, most useful sections first, hard-capped at `budget` chars."""
    langs = ", ".join(f"{k} ({v})" for k, v in ctx["languages"].items()) or "unknown"
    parts = [
        f"Branch {ctx['branch']} @ {ctx['head_sha'][:8]}; {ctx['file_count']} files; languages: {langs}",
        "Frameworks: " + (", ".join(ctx["frameworks"]) or "none detected"),
        "Modules:\n" + "\n".join(f"- {m['dir']}/ ({m['files']} files{', ' + m['purpose'] if m['purpose'] else ''}"
                                 f"{', ' + '/'.join(m['languages']) if m['languages'] else ''})" for m in ctx["modules"]),
        "Root files: " + ", ".join(ctx["root_files"]),
        "Existing tests:\n" + "\n".join(f"- {t}" for t in ctx["tests"][:25]),
        "Recent history:\n" + "\n".join(ctx["git_log"][:10]),
    ]
    for name, text in ctx["docs"].items():
        parts.append(f"{name} (excerpt):\n{text[:700]}")
    for name, text in ctx["manifests"].items():
        parts.append(f"{name}:\n{text[:500]}")
    out, used = [], 0
    for p in parts:
        if used + len(p) > budget:
            out.append(p[: max(0, budget - used)] + "\n…[context truncated]")
            break
        out.append(p)
        used += len(p) + 2
    return "\n\n".join(out)


def suggest_validation(ctx: dict) -> list[dict]:
    """Best-effort default checks from manifests, used when a project configures none."""
    cmds = []
    s = ctx.get("npm_scripts", {})
    for kind, names in (("test", ["test"]), ("lint", ["lint"]), ("typecheck", ["typecheck", "tsc"]), ("build", ["build"])):
        for n in names:
            if n in s and "no test specified" not in s[n]:  # skip the `npm init` placeholder that always fails
                cmds.append({"kind": kind, "command": f"npm run {n}" if n != "test" else "npm test", "required": True})
                break
    blob = " ".join(ctx.get("manifests", {}).values()).lower()
    py_tests = any(f.endswith(".py") for f in ctx.get("tests", []))
    if "pytest" in blob:
        cmds.append({"kind": "test", "command": "python -m pytest -q", "required": True})
    elif py_tests:  # no pytest declared: the stdlib runner is the only safe assumption
        cmds.append({"kind": "test", "command": "python -m unittest discover -s tests", "required": True})
    if "ruff" in blob:
        cmds.append({"kind": "lint", "command": "ruff check .", "required": True})
    return cmds


def get_or_create_context(db, repo, force: bool = False) -> RepoContext:
    """Cached per (repo, HEAD sha): a new commit invalidates the cache."""
    sha = head_sha(repo.path)
    row = db.query(RepoContext).filter_by(repo_id=repo.id, head_sha=sha).one_or_none()
    if row and not force:
        return row
    ctx = build_context(repo.path)
    if row:
        row.summary = ctx
        db.commit()
        return row
    row = RepoContext(repo_id=repo.id, head_sha=sha, summary=ctx)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:  # another worker built the same (repo, sha) first; use theirs
        db.rollback()
        row = db.query(RepoContext).filter_by(repo_id=repo.id, head_sha=sha).one()
    return row
