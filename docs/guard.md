# The SQL guard

Model-written SQL is untrusted input. Prompt instructions are not a control: a model can be
talked into anything, or simply get it wrong. So the restrictions are enforced in code, in
three independent layers.

## Layer 1: validation in `guard.py`

1. Parse with sqlglot (SQLite dialect). Anything other than exactly one statement is refused.
2. The root must be a `SELECT` or a set operation. Every node in the tree is checked against
   a list of forbidden statement types (DML, DDL, `PRAGMA`, `ATTACH`, transactions, `INTO`, and
   engine-information functions).
3. Functions sqlglot does not model itself must be on an allowlist. This blocks
   `load_extension`, `readfile`, `writefile`, `randomblob`, `zeroblob`, `sqlite_*` and similar.
4. Tables must be in the allowlist. Schema-qualified names, system tables and table-valued
   functions (`pragma_*`, `json_each`, `generate_series`) are refused. Recursive CTEs are refused.
5. Columns are resolved against a schema that leaves out restricted columns. Naming one is an
   error, and `SELECT *` expands without it, so hidden columns cannot be reached by alias,
   subquery, CTE, join, `WHERE` or `GROUP BY`.
6. `LIMIT` is added if missing, lowered if above the cap, and must be a plain integer.
   Set operations are wrapped so the limit applies to the whole result.
7. The query is regenerated from the rewritten tree. **The regenerated text is what runs**, not
   the original string, so there is no gap between what was checked and what is executed.

## Layer 2: the connection in `db.py`

- Opened with `mode=ro` and `PRAGMA query_only`.
- An authorizer callback permits only `SELECT`, reads of allowed columns, and calls to
  allowed functions. Writes, `ATTACH`, `PRAGMA`, system-table reads and hidden columns are denied
  by SQLite itself.
- A progress handler stops queries that run past the time limit. Fetching stops at the row cap.

## Layer 3: the agent loop

Rejection reasons go back to the model so it can repair a legitimate query. After a set number
of rejected queries the run stops, so the model cannot probe the guard indefinitely.

## What the attack set shows

`eval/attacks.jsonl` has 81 attack queries and 21 legitimate ones (`sqlscout eval`).
All attacks are blocked and all legitimate queries pass. 77 of the 81 attacks would also have
been stopped by layer 2 alone. The four that would not are `EXPLAIN` (harmless, but not a
plain query), a schema-qualified read of an allowed table (`main.orders`), and two `LIMIT`
tricks (`LIMIT -1` and a subquery as the limit). Those are policy decisions that only the
guard makes. Recursive CTEs are stopped at layer 2 only by the time limit, so the guard's
explicit refusal matters for how fast the model gets feedback.

## Limits

- I wrote the attack set while writing the guard, and it caught one gap (`sqlite_version()`)
  that I then fixed. It is a regression suite, not an independent penetration test.
- A read-only analyst can still run an expensive query within the time limit, and can still
  learn things from allowed columns. Per-user permissions, row-level security and audit logging
  would be needed for a real multi-tenant deployment.
- The guard decides what may run. It does not decide whether the SQL answers the question
  correctly; that is what the execution-accuracy evaluation is for.
