#!/usr/bin/env bash
# Creates samples/repos/demo-app, a tiny git repo to point tasks at. Safe to re-run.
set -euo pipefail
dir="$(cd "$(dirname "$0")" && pwd)/repos/demo-app"
if [ -d "$dir/.git" ]; then echo "demo repo already exists: $dir"; exit 0; fi
mkdir -p "$dir/tests" && cd "$dir"
cat > README.md <<'R'
# demo-app
Tiny Python library used to try the orchestrator.
R
cat > greeter.py <<'R'
def greet(name: str) -> str:
    return f"Hello, {name}!"
R
cat > tests/test_greeter.py <<'R'
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from greeter import greet  # noqa: E402


class TestGreet(unittest.TestCase):
    def test_greet(self):
        self.assertEqual(greet("Ada"), "Hello, Ada!")
R
printf '__pycache__/\n*.pyc\n' > .gitignore
git init -q -b main
git add -A
git -c user.name="Demo" -c user.email="demo@localhost" commit -q -m "Initial commit"
echo "created $dir"
