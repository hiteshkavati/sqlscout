# Design decisions

**Parse, rewrite, regenerate.** String and regex filters for SQL fail on comments, quoting,
case and encoding tricks. Validating a parsed tree and executing the SQL regenerated from it
removes the difference between what was checked and what runs.

**Hide restricted columns from the schema instead of blacklisting them.** The qualifier is
given a schema without those columns, so a reference to one is an error and `SELECT *` expands
without it. There is no list of "bad" expressions to keep complete.

**Defence in depth.** The guard can have bugs. The read-only connection and authorizer mean a
bug in the guard still cannot write or read hidden columns. The report shows how many attacks
each layer stops on its own.

**Guard messages are written for the model.** A rejection that says what was wrong and what is
available ("Table 'x' is not available. Available tables: ...") lets a good model repair the
query in one more step. Block limits stop a model that keeps pushing.

**Business definitions in the prompt.** "Revenue" is ambiguous, and an answer that is
consistent but uses another definition is still wrong for the user. The definitions are stated
once in the system prompt and the evaluation's gold SQL uses the same ones.

**Execution accuracy, not SQL text match.** Two different queries can be correct. The
evaluation compares result sets (rounded, order-insensitive unless the question asks for an
order) and reports separately when only the column order differed.

**Evaluation questions are not tuned on.** The question set is split into dev and test.
Prompts and guard rules were developed against dev only.

**Refusal and unanswerable cases are scored too.** A data agent that returns email addresses
when asked, or invents a number for a question the data cannot answer, is failing in ways an
accuracy number over answerable questions would hide.

**Oracle model for the harness.** An "oracle" that answers with the gold SQL proves the scoring
code works without a real model. The report labels it clearly so it cannot be read as model
accuracy.

**One connection with a lock.** SQLite is a single-writer, small-reader database and the demo
is a single process. A lock around the connection keeps the progress-handler time limit
correct under concurrent requests.

**Synthetic data.** No real customer data is used. The generator is seeded so the gold answers
stay valid, and the customer emails use the reserved `example.com` domain.
