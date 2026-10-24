# Sample workflows

Run `make up`, open http://localhost:47213 (admin / the password in `.env`), then try these. `samples/submit.sh <n>` submits entry *n* of `tasks.json` from the command line (needs `jq`).

## 1. Feature, happy path with a retry (task 0)
1. Submit "Add a farewell function". The planner produces inspect → design → implement → test → review.
2. With `AGENT_MODE=mock` and `MOCK_FAIL_FIRST=true` the writer's first attempt fails validation. Open the **Validation** tab: attempt 1 failed, a debug prompt was sent, attempt 2 passed. The **Timeline** shows the `retry` event with the reason.
3. The task stops at *waiting for review*. Inspect the **Diff**, then **Approve & commit**. The change lands as a commit on branch `orch/task-<id>` inside `samples/repos/demo-app`; the main checkout is never touched.

## 2. Risk gate (task 2)
`risk_level: high` blocks the task before any code agent runs. Approve it in the approval panel to let it proceed; Reject cancels it and deletes its branch.

## 3. Request changes
On a task in review, write feedback and press **Request changes**. The writer agent gets the feedback as a new prompt, validation re-runs, and the task returns to review.

## 4. Conflict warning
Submit two tasks against the same repo before approving either. If both touch the same file the second one shows a "merge conflict risk" banner naming the other task and the files.

## 5. Failure, dead-letter, force commit
Set `MAX_RETRIES=0`, keep `MOCK_FAIL_FIRST=true` and submit task 0: validation fails closed and the task lands in the dead-letter list (Settings page). Retry it, or use **Force commit** (requires a reason and an explicit confirmation; recorded in the audit log).
