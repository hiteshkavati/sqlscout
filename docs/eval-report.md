# Evaluation report

Guard results are deterministic and need no model.

## SQL guard

- Attack queries blocked: 81/81
- Legitimate queries allowed: 21/21
- Attacks the database layer would also have stopped without the guard: 77/81

| attack category | blocked |
|---|---|
| engine command | 12/12 |
| function | 14/14 |
| malformed | 6/6 |
| resource | 3/3 |
| restricted column | 14/14 |
| select-shaped write | 4/4 |
| stacked | 5/5 |
| system table | 10/10 |
| unknown table | 3/3 |
| write/ddl | 10/10 |
