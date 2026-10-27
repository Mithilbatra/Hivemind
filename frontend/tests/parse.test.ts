import assert from "node:assert/strict";
import { test } from "node:test";
import { checksFromText, checksToText, splitDiff } from "../lib/parse";

test("splitDiff keys files by path, including non-ASCII and spaces", () => {
  const patch = [
    "diff --git a/café.txt b/café.txt", "new file mode 100644", "+a",
    "diff --git a/my notes.md b/my notes.md", "-old", "+new", "",
  ].join("\n");
  const files = splitDiff(patch);
  assert.deepEqual(files.map((f) => f.path), ["café.txt", "my notes.md"]);
  assert.equal(files[1].lines.filter((l) => l.startsWith("+")).length, 1);
  assert.deepEqual(splitDiff(""), []);
});

test("checks round-trip through the settings text box", () => {
  const text = "test: python -m pytest -q\nlint: ruff check .\npytest -k \"a: b\"";
  const parsed = checksFromText(text);
  assert.deepEqual(parsed.map((c) => [c.kind, c.command]), [
    ["test", "python -m pytest -q"], ["lint", "ruff check ."], ["test", "pytest -k \"a: b\""],
  ]);
  assert.equal(checksToText(parsed.slice(0, 2)), "test: python -m pytest -q\nlint: ruff check .");
  assert.deepEqual(checksFromText("\n  \n"), []);
});
