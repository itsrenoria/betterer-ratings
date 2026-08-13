# Post-Merge Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove ownership-invalid PMDB ID caching, prevent false queue-stall alerts during scheduled backoff, and restore clean Ruff and mypy checks.

**Architecture:** Keep queue totals and provider result types unchanged. Enforce the ownership rule at the two submission handlers, and give `QueueAlertMonitor` explicit due counts so it does not infer runnable work from aggregate pending totals.

**Tech Stack:** Python 3.12+, asyncio, SQLite, pytest, Ruff, mypy.

## Global Constraints

- Preserve existing successful submission and dashboard behavior.
- Do not add schema migrations or dependencies.
- Write and run each behavioral regression test before changing production code.
- Final verification must pass pytest, Ruff, strict mypy, and `git diff --check`.

---

### Task 1: Prevent ownership-invalid PMDB ID caching

**Files:**
- Modify: `tests/test_pmdb_ownership_caching.py`
- Modify: `tests/test_throughput_optimization.py`
- Modify: `src/betterer_ratings/services/submit/handler_rating.py:37-50`
- Modify: `src/betterer_ratings/services/submit/handler_mapping.py:28-50`

**Interfaces:**
- Consumes: `PMDBSubmitResult.stale_cached_item_id`, `PMDBSubmitResult.duplicate_or_exists`
- Produces: submitted rows whose `pmdb_item_id` is either owned or unset

- [ ] **Step 1: Add a failing stale-duplicate rating test**

Add a handler-level test whose fake PMDB result is successful, duplicate, contains
`item_id="foreign-rating-id"`, and sets `stale_cached_item_id=True`. Assert that the DB clear method
is called and the submitted tuple stores `None` as its ID.

- [ ] **Step 2: Run the rating regression test and verify RED**

Run:

```bash
PYTHONPATH=src ../../.venv/bin/pytest tests/test_pmdb_ownership_caching.py::test_stale_duplicate_success_does_not_recache_foreign_rating_id -q
```

Expected: failure showing `foreign-rating-id` was stored instead of `None`.

- [ ] **Step 3: Implement the minimal rating fix**

Before `mark_rating_submitted`, derive the trusted ID as:

```python
submitted_item_id = (
    None if result.stale_cached_item_id and result.duplicate_or_exists else result.item_id
)
```

Pass `submitted_item_id` to the database call. This preserves a newly created owned ID when a
stale cached ID was detected but the replacement POST succeeded normally.

- [ ] **Step 4: Run the focused rating tests and verify GREEN**

```bash
PYTHONPATH=src ../../.venv/bin/pytest tests/test_pmdb_ownership_caching.py -q
```

- [ ] **Step 5: Add a failing mapping-preflight test assertion**

Update `test_mapping_group_preflight_resolves_existing_and_posts_only_missing` so both submitted
mapping tuples must have a final ID value of `None`.

- [ ] **Step 6: Run the mapping regression test and verify RED**

```bash
PYTHONPATH=src ../../.venv/bin/pytest tests/test_throughput_optimization.py::test_mapping_group_preflight_resolves_existing_and_posts_only_missing -q
```

Expected: failure showing `remote-imdb` and `remote-tvdb` were cached.

- [ ] **Step 7: Implement the minimal mapping fix and verify GREEN**

Remove the `pmdb_item_id` argument from the preflight `mark_mapping_submitted` call, then rerun the
focused test file.

- [ ] **Step 8: Commit the ownership fixes**

```bash
git add tests/test_pmdb_ownership_caching.py tests/test_throughput_optimization.py \
  src/betterer_ratings/services/submit/handler_rating.py \
  src/betterer_ratings/services/submit/handler_mapping.py
git commit -m "fix(pmdb): avoid caching unowned item ids"
```

### Task 2: Alert only on runnable queue work

**Files:**
- Modify: `tests/test_http_resilience_and_alerts.py`
- Modify: `src/betterer_ratings/services/submit/runner.py:28-215`

**Interfaces:**
- Consumes: `LocalDatabase.count_due_queue(kind: str, now_ts: int) -> int`
- Produces: `QueueAlertMonitor.observe(..., due_counts: Mapping[str, int])`

- [ ] **Step 1: Add failing future-retry and due-work tests**

Add a `_due_counts` helper with literal `ratings_due`, `mappings_due`, and
`episode_ratings_due` values. Pass it to existing monitor tests. Add a test where aggregate pending
counts stay nonzero but every due count and in-flight count is zero for more than 300 seconds;
assert that no stall event is emitted. Keep the sustained due-work test asserting a stall at 300
seconds.

- [ ] **Step 2: Run the monitor test file and verify RED**

```bash
PYTHONPATH=src ../../.venv/bin/pytest tests/test_http_resilience_and_alerts.py -q
```

Expected: `QueueAlertMonitor.observe` rejects `due_counts`, or the future-only retry emits a stall.

- [ ] **Step 3: Implement explicit due-work accounting**

Change `_queue_has_active_work` to accept aggregate `counts` and explicit `due_counts`; return true
only for a positive in-flight count or a positive due count. Require `due_counts` in
`QueueAlertMonitor.observe`. In `queue_progress_loop`, construct:

```python
due_counts = {
    "ratings_due": db.count_due_queue(kind="rating", now_ts=now_ts),
    "mappings_due": db.count_due_queue(kind="mapping", now_ts=now_ts),
    "episode_ratings_due": db.count_due_queue(kind="episode_ratings", now_ts=now_ts),
}
```

Use it for active-work logging and pass it to the monitor.

- [ ] **Step 4: Run focused queue tests and verify GREEN**

```bash
PYTHONPATH=src ../../.venv/bin/pytest tests/test_http_resilience_and_alerts.py tests/test_throughput_optimization.py -q
```

- [ ] **Step 5: Commit the queue-monitor fix**

```bash
git add tests/test_http_resilience_and_alerts.py src/betterer_ratings/services/submit/runner.py
git commit -m "fix(submitter): exclude delayed retries from stall alerts"
```

### Task 3: Restore Ruff and mypy cleanliness

**Files:**
- Modify: `src/betterer_ratings/core/scoring.py:263-271`
- Modify: `src/betterer_ratings/services/submit/runner.py:1-10`
- Modify: `src/betterer_ratings/services/submit/worker.py:1-8`

**Interfaces:**
- Consumes: `parse_value_and_scale(value) -> tuple[float | None, float | None]`
- Produces: unchanged MDBList rating output with a statically narrowed fallback value

- [ ] **Step 1: Verify the static-analysis RED state**

```bash
PYTHONPATH=src ../../.venv/bin/mypy src/betterer_ratings
../../.venv/bin/ruff check src tests
```

Expected: mypy reports `core/scoring.py:269`; Ruff reports `I001` in `runner.py` and `worker.py`.

- [ ] **Step 2: Apply minimal static fixes**

Set `tr_score` to `None` when `tr_num is None`; otherwise retain the existing denominator and
clamp branches. Remove the single surplus blank line before each module constant block flagged by
Ruff.

- [ ] **Step 3: Verify mypy, Ruff, and scoring tests GREEN**

```bash
PYTHONPATH=src ../../.venv/bin/pytest tests/characterization/test_scoring_characterization.py -q
PYTHONPATH=src ../../.venv/bin/mypy src/betterer_ratings
../../.venv/bin/ruff check src tests
```

- [ ] **Step 4: Commit the static-analysis fixes**

```bash
git add src/betterer_ratings/core/scoring.py \
  src/betterer_ratings/services/submit/runner.py \
  src/betterer_ratings/services/submit/worker.py
git commit -m "fix: restore static analysis checks"
```

### Task 4: Full verification

**Files:**
- Review: all changes since `origin/main`

- [ ] **Step 1: Run the complete verification suite**

```bash
PYTHONPATH=src ../../.venv/bin/pytest
../../.venv/bin/ruff check src tests
PYTHONPATH=src ../../.venv/bin/mypy src/betterer_ratings
git diff --check origin/main...HEAD
```

- [ ] **Step 2: Review the final diff and branch state**

```bash
git diff --stat origin/main...HEAD
git diff origin/main...HEAD -- src tests
git status --short --branch
```

Confirm there are no unrelated changes and every requested issue has a corresponding regression
test or static-analysis proof.
