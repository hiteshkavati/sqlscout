# sqlscout

A data-analyst agent. You ask a business question in plain English, it writes SQL, runs it on a
read-only database, repairs its own mistakes, and answers with the numbers, the query behind
them and a step trace.

The interesting part is not the text-to-SQL. It is everything around the model: a guard that
parses every query and refuses anything that is not a safe read, a database connection that
enforces the same limits independently, and an evaluation harness that scores results rather
than SQL text.

Runs on a synthetic Indian e-commerce database (customers, products, orders, line items,
returns; 12,000 orders over 2025) that the project generates itself.

## What it does

- **Agent loop** with tool calling: `list_tables`, `describe_table`, `run_sql`. Step, rejected-query
  and token budgets. Works with Anthropic, any OpenAI-compatible API, or Ollama.
- **SQL guard** built on a parsed AST, not regexes. Single `SELECT` only, table and function
  allowlists, hidden columns, forced `LIMIT`, no recursive CTEs, no system tables. The SQL that
  runs is regenerated from the validated tree. See [docs/guard.md](docs/guard.md).
- **Defence in depth.** The database connection is read-only, has an authorizer that denies
  everything except permitted reads, and enforces a time limit and row cap on its own.
- **Evaluation harness.** 43 questions with gold SQL, scored by execution accuracy, including
  questions it must refuse and questions the data cannot answer. 102 attack and legitimate
  queries for the guard.
- **Interfaces.** Web UI (ask, SQL console, schema browser, bar charts), HTTP API, CLI and an
  MCP server exposing the same guarded tools.

## Quickstart

```bash
pip install -e ".[dev,mcp]"
sqlscout init-db                 # generates data/shop.db (deterministic)
sqlscout serve                   # http://127.0.0.1:8000
```

Without a model the SQL console, schema browser and guard all work. To enable questions:

```bash
export ANTHROPIC_API_KEY=...     # or OPENAI_API_KEY, or use a local Ollama
export SQ_LLM_PROVIDER=anthropic # anthropic | openai | ollama
sqlscout ask "Which city has the most delivered revenue?"
```

Other commands: `sqlscout sql "SELECT ..."` (guarded query), `sqlscout eval` (guard report),
`sqlscout mcp` (MCP server over stdio). Configuration is in `.env.example`.
Docker: `docker compose up --build`.

## Example

```
$ sqlscout sql "SELECT email FROM customers"
Rejected: Column customers.email is restricted and cannot be queried.

$ sqlscout sql "SELECT * FROM customers LIMIT 2"
SELECT customers.customer_id AS customer_id, customers.city AS city, ... LIMIT 2
customer_id  city     state       signup_date  segment
1            Chennai  Tamil Nadu  2024-05-16   new
```

`SELECT *` expands without the hidden `email` column, and the SQL shown is the rewritten
query that actually ran.

## Results

**SQL guard** (`sqlscout eval`, no model needed; full table in
[docs/eval-report.md](docs/eval-report.md)):

| | |
|---|---|
| Attack queries blocked | 81 / 81 |
| Legitimate queries allowed | 21 / 21 |
| Attacks the database layer alone also stops | 77 / 81 |

Categories: DDL and writes, stacked statements, writes hidden inside `WITH`, engine commands
(`PRAGMA`, `ATTACH`, `VACUUM`), system tables, dangerous functions, restricted columns reached
through aliases, subqueries, CTEs and joins, resource abuse.

**Agent accuracy: not measured yet.** The scoring harness is tested with an oracle model that
answers with the gold SQL (it scores 43/43, which only shows the harness is correct). To
measure a real model:

```bash
sqlscout eval --provider anthropic --write docs/eval-report.md
```

Caveats on what is measured:

- I wrote the attack set while building the guard, and it found one gap that I fixed. It is a
  regression suite, not an independent security audit.
- The 43 questions are mostly aggregates and joins over a clean synthetic schema. Real
  warehouses are messier, so expect lower numbers there.
- Unanswerable questions are scored as passed only if the agent never produced a result set.
  That is strict, and a good answer that first inspects the data can be marked down.

## Project layout

```
src/sqlscout/   datagen, schema, guard, db, tools, llm, agent, evaluate, api, cli, mcp_server, static UI
eval/           questions.jsonl (gold SQL, dev/test), attacks.jsonl
docs/           architecture, guard design, design decisions, eval report
tests/          204 tests
```

## Security notes

- Everything is read-only and limited to the tables and columns shown in the schema.
- The demo has no user accounts. Set `SQ_API_KEY` to require a key on the query endpoints;
  requests are also rate limited per client address. Do not expose it publicly with real data
  without adding authentication, per-user permissions and audit logging.
- With a model enabled, questions and returned rows are sent to that provider.
- Traces (`SQ_TRACE_DIR`) record questions, queries and step output, not result rows.

## License

MIT
