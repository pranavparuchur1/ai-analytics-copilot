# How the Analytics Copilot works: a beginner's guide

This guide walks through the project in the order it was built. Every section has: what
problem it solves, how it works (with an everyday analogy), what we measured, and a few
self-test questions. Answers are at the end. The glossary explains every term in **bold**.

---

## 0. The big picture

A business person asks: *"Why did revenue decrease in California last month?"* The copilot:

1. figures out what kind of question it is,
2. writes a **SQL** query (the language databases understand),
3. checks that the query is safe,
4. runs it against the **data warehouse**,
5. does statistics on the result,
6. picks a chart,
7. writes a short explanation, and checks the explanation's numbers against the data.

**Analogy.** Think of a junior analyst (the language model) who is fast and fluent but
sometimes careless. You don't let them touch the production database directly. They write a
query on paper; a strict reviewer (the validator) checks it; it runs under an account that
physically cannot change anything (the read-only role); a calculator, not the junior,
does the arithmetic (the statistics code); and before the answer goes out, someone checks
that every number in the write-up appears in the data (the grounding check).

That analogy is the whole design: **the model proposes, code decides.**

---

## 1. The warehouse and the data (Phase 0)

### What a data warehouse is
A **database** stores data in **tables** (like spreadsheets with strict column types). A
**data warehouse** is a database organised for analysis rather than for running a shop
day-to-day. Ours is **PostgreSQL** running in **Docker** (a container: a small, isolated
computer-inside-your-computer that runs the same everywhere).

Our tables describe a fictional outdoor-gear retailer, Switchback Outfitters:

| Table | One row is... |
|---|---|
| `orders` | one customer order (when, where, online or in-store, completed or cancelled) |
| `order_items` | one product line inside an order (quantity, price, discount) |
| `products` | one product (category, brand, price, cost) |
| `customers` | one customer account (state, segment, loyalty tier; names/emails hidden) |
| `stores` | one store (25 physical + "Online") |
| `returns` | one returned order line, with a reason and refund |
| `marketing_spend` | one day x channel x state of advertising spend |
| `stockouts` | a period when a product was out of stock at a store |

Tables connect through **keys**: `order_items.order_id` points to `orders.order_id`. Putting
two tables together on a key is a **join**.

### Why synthetic data, and what "planted effects" are
Real company data is private, and public sample databases have no known "story", so you
could never check whether an answer to "why did X happen?" is right. So a program
(`data/generate.py`) invents 330,000 orders with realistic patterns (camping gear sells in
summer, ski gear in winter, the business grows ~17% a year, weekends are busier) **and**
five hidden events with known answers:

| Planted effect | What really happened |
|---|---|
| A | Camping products ran out in every California store in September 2026 |
| B | A defective batch of the Summit Down Jacket (March-May 2026) caused returns |
| C | A FALL25 promotion (15% off) in Oct-Nov 2025 |
| D | Texas social-media ads were paused from June 2026 |
| E | Data-quality bugs: future-dated orders, zero-quantity lines, duplicate returns, ... |

The generator uses a **random seed** (42). Same seed, same data, every time, so results are
reproducible.

Checked after loading: California revenue fell **23.4%** from August to September while the
other states fell **13.6%** (seasonal). California camping fell **56%**. So the planted
effect is visible, and the "why" feature has a right answer to find.

### The read-only database role (least privilege)
A database **role** is a user account with **privileges** (what it may do). The copilot
connects as `copilot_ro`, which:

- can only `SELECT` (read), never `INSERT`/`UPDATE`/`DELETE` (write) or `DROP` (destroy);
- has **column-level grants**: it can read `customers.state` but not `customers.email`;
- cannot see the `hr` schema (salaries) at all;
- has a **statement timeout** of 5 seconds, so a runaway query is killed.

This is the principle of **least privilege**: give an account only what it needs.

**Analogy:** a museum visitor badge. You can look at everything in the public galleries,
you cannot enter the staff office, and you cannot take anything home, no matter what you say
at the door.

### Self-test 0
1. Why can't we just use a real company's database to test the "why" feature?
2. What does a random seed guarantee?
3. Name two things the `copilot_ro` role cannot do.

---

## 2. The SQL safety layer (Phase 1)

### The problem
The model writes SQL. Sometimes it will be wrong; occasionally it might be harmful (a user
types "delete all orders", or a confused model writes `DROP TABLE`).

### Why not just search for bad words?
The first idea everyone has: reject any query containing `DROP`, `DELETE`, `UPDATE`... We
measured that idea (`scripts/safety_report.py`):

| Approach | Harmful statements blocked | Harmless queries wrongly blocked |
|---|---|---|
| Keyword search | 18 / 47 | 2 / 14 |
| Parse-tree validator | 47 / 47 | 0 / 14 |

Keyword search fails both ways:
- It **blocks** `SELECT 'DROP TABLE' AS note`, which is harmless (the words are just text).
- It **misses** `SELECT pg_sleep(60)` (freezes a connection), `SELECT email FROM customers`
  (reads private data), `COPY orders TO '/tmp/x'` (exports data), and many more.

### Parsing: reading SQL like a compiler does
A **parser** turns text into a tree that shows its structure, called an **abstract syntax tree
(AST)**. We use the library **sqlglot**. For
`WITH d AS (DELETE FROM orders RETURNING *) SELECT count(*) FROM d` the text starts with
`WITH` and ends with `SELECT`, but the tree contains a `Delete` node, so the validator sees it.
(Yes, PostgreSQL allows deleting inside a `WITH`: a sneaky case a regex would never catch.)

**Analogy:** keyword search is like scanning a letter for the word "bomb". Parsing is like
actually reading and understanding the sentences.

The validator's rules, in order (`safety/validator.py`):
1. exactly one statement (blocks `SELECT 1; DROP TABLE orders`);
2. it must be a query (SELECT or UNION etc.);
3. no write/DDL/lock/`SELECT INTO` nodes anywhere in the tree;
4. only allowed tables, depending on the user's **role** (a "marketing" user cannot query
   returns);
5. no restricted columns (names, email, phone, salary, SSN); no `SELECT *` on customers;
6. no dangerous functions (`pg_sleep`, `pg_read_file`, `dblink`, `set_config`...);
7. at least one real table (added later, see section 3);
8. add `LIMIT 1000` (or lower an existing bigger limit) so nobody downloads millions of rows.

Then the **executor** asks PostgreSQL for the query's **plan** with `EXPLAIN` (the database's
estimate of how much work a query is) and refuses plans above a cost ceiling, so an
accidental "join every order line to every order line" never runs.

### Defence in depth
Two independent layers: the validator *and* the database role. If either has a bug, the
other still stops the attack. The tests (`tests/test_db_security.py`) deliberately skip the
validator and send `DELETE`, `DROP`, `SELECT email` straight to the database to prove the
role alone refuses them.

### Self-test 1
4. Give one harmless query a keyword filter would block, and one harmful query it would miss.
5. What is an AST, and why does it help here?
6. Why keep the database role if the validator already blocks everything in the suite?

---

## 3. Turning questions into SQL (Phase 2)

### The model
**Qwen2.5-Coder-7B-Instruct**, an open-weights **large language model (LLM)** trained on lots
of code, compressed with **AWQ 4-bit quantisation** so it fits in the laptop's 8 GB GPU. It runs
on **vLLM** inside WSL and is reached through **your inference-gateway from Project 5**
(API key, rate limits, usage tracking). The copilot is just another client of the gateway.

### What goes in the prompt
A **prompt** is the text sent to the model. Ours contains:
- **today's date and exact date ranges** ("last quarter = 2026-07-01 to 2026-10-01");
- the **schema** of the relevant tables, with a description and example values per column;
- **join conditions** and **metric definitions** ("revenue = quantity x price - discount, completed
  orders only");
- rules ("use half-open date ranges", "write PostgreSQL");
- one worked example (a question and its SQL), called a **few-shot example**.

This structured description of the business is a **semantic layer**
(`configs/semantic_layer.yaml`).

### Schema retrieval
Real warehouses have hundreds of tables, too many for a prompt. **Schema retrieval** picks the
relevant ones: it scores each table by how many question words match its name, synonyms,
columns and known values, adds tables required by any metric mentioned, and then adds tables
needed to *connect* them (to get from `returns` to `stores` you must pass through
`order_items` and `orders`; that is a shortest path in the **join graph**).

Measured: it found every needed table for all 56 questions, using 2.4 of 8 tables on average.
Sending the whole schema instead was **equally accurate here** (49/56 both ways) but used
**1.8x as many prompt tokens**. With 8 tables retrieval mainly saves cost; with 800 tables it
would be essential.

### The date resolver: don't make the model do calendar maths
Early runs got "last month" wrong (the model said August when today is October 1st). Small
models are bad at calendar arithmetic. Fix: compute the ranges in Python (`dates.py`) and put
them in the prompt. The model now only has to copy the right line. Dev accuracy went from
79% to 89%. Lesson: **move anything that can be computed out of the model.**

### Self-correction
If the validator rejects the SQL, or PostgreSQL returns an error, the error message is sent
back to the model ("PostgreSQL returned an error: column o.revenue does not exist. Fix the
query.") and it gets up to 2 more tries. An empty result gets one retry too (it usually means
a misspelt filter like 'California' instead of 'CA').

Measured: with self-correction, 98% of questions produced a query that ran; without it, 89%.
But accuracy only went from 85.7% to 87.5% (one question). The loop mostly repairs *syntax*
errors; it rarely repairs *wrong logic*, because the model doesn't know its logic is wrong.

### How accuracy is measured
**Execution accuracy**: run the model's query and the hand-written correct ("gold") query,
and compare the *results*, not the SQL text (there are many correct ways to write a query).
The comparison ignores column names and allows extra columns, tolerates rounding, accepts
25% vs 0.25, and treats `2024` and `2024-01-01` as the same year. Each tolerance rule has a
unit test.

**Dev/test split.** 19 "dev" questions were used while improving the prompt; 37 "test"
questions were run once at the end, so the test score isn't inflated by tuning. That
**held-out** score was **30/37 = 81%**.

**Confidence intervals.** With 37 questions, one question is worth ~3 points. The 95%
**Wilson interval** for 30/37 is 66-91%: the true accuracy on questions like these is very
likely somewhere in there. So "81% vs 84%" is *not* a real difference; "57% vs 87.5%" (3B vs
7B model) clearly is.

| Configuration | Execution accuracy (56 questions) |
|---|---|
| Full pipeline, 7B | 87.5% |
| Same, run again | 87.5% (identical SQL for 55/56: the model is deterministic at temperature 0) |
| No schema retrieval | 87.5% (1.8x tokens) |
| No self-correction | 85.7% |
| 3B model | 57.1% |

### What still fails (honest list)
- **Business vocabulary**: "physical stores" was read as `channel = 'retail'`, silently
  dropping outlet stores, even after a definition was added to the table description.
- **Invented filters**: adding `channel = 'online'` that the question never mentioned.
- **Wrong grain**: computing per state *per month* when asked per state.
- **A fabricated answer** (fixed): asked about the weather, the model wrote
  `SELECT 'Sunny' AS weather`, a query that reads no table at all. The validator now rejects
  queries that read no table.

### Self-test 2
7. Why compare query *results* rather than query *text*?
8. Why were dates moved out of the model and into Python?
9. Self-correction raised "queries that ran" from 89% to 98% but accuracy by only ~2 points. Why?
10. What does the interval 66-91% around 81% tell you?

---

## 4. Analysis, charts and data quality (Phase 3)

### Profiling the result
Before doing statistics, code works out what each column *is*:
- **time** (dates, or a `year`/`month` number),
- **measure** (a number you add up: revenue, units),
- **dimension** (a category you group by: state, product),
- **id** (numbers that are labels, like `product_id`, never summed).

### Statistics, computed by code
Depending on the shape: for a time series, first-to-last change, the latest period-over-period
change, highest and lowest periods, the **trend** (slope of a straight line fitted by **least
squares**) and **anomalies** (periods far from that line, more than 2 standard deviations).
For a breakdown, the largest item and its share, the runner-up, and how concentrated the total
is in the top 3.

Why not ask the model to do this? Small models misread tables and mis-add numbers. A
calculator doesn't.

One subtle bug found while testing: for "top five products", the share "Tent = 34% of the
total" was the share of the *five rows shown*, not of all revenue, and the model repeated it as
"34% of total revenue". Now, when a query asked for the top N, shares are not computed and the
facts say the overall total is unknown.

### Choosing a chart
| Result shape | Chart | Why |
|---|---|---|
| one number | no chart, a big number | a chart of one number says nothing |
| time + number | line | shows change over time |
| category + number | bar, sorted | compares sizes; horizontal if labels are long |
| two numbers | scatter | shows a relationship |

Charts are **Vega-Lite** specifications (JSON describing a chart), drawn in the browser and
rendered to PNG for reports. Colours come from a palette checked for **colour-vision
deficiency** (colour blindness).

### Data-quality checks
On every answer: did the result hit the row limit? Are there future dates, empty values,
negative money, duplicate rows? These become warnings next to the answer.

On the whole warehouse (`acp dq`): the audit found all five planted problem types (30 future
orders, 150 zero-quantity lines, 12 duplicate returns, 170 customers without a state, 8
products costing more than their price) and **one nobody planted**: 5 open stockouts on
discontinued products, a side effect of how the generator works. Real audits find surprises too.

### Self-test 3
11. Why is `product_id` treated differently from `revenue` even though both are numbers?
12. What is an anomaly in a time series, as defined here?
13. What went wrong with "34% of total revenue", and how was it fixed?

---

## 5. "Why did X change?" (Phase 4)

### Why this is hard
"Why" has no single SQL query. You need to compare two periods, break the change down, check
whether it's normal, and look for evidence elsewhere.

### The design: model for understanding, code for analysis
The model does one small job: turn the question into a **spec**:

```json
{"metric": "revenue", "filters": {"ship_state": "CA"},
 "current": ["2026-09-01", "2026-10-01"], "baseline": ["2026-08-01", "2026-09-01"]}
```

Then code runs pre-written query **templates** (still through the validator):
1. **Totals**: CA revenue 691,372 -> 529,378 (-23.4%).
2. **Benchmark**: the same change in all other states: -13.6%. So part of the drop is
   seasonal, but California fell much more.
3. **Decomposition**: for each dimension (category, product, channel, store...), how much of
   the change each segment explains.
4. **Signals**: stockouts that started in this period, changes in marketing spend, promotions
   that ended.

### Excess contribution
If you just ask "which category lost the most money?", the biggest category usually wins
simply because it's biggest. Instead we compute:

    excess = (share of the change) - (share of the baseline)

Camping was 33% of California's August revenue but explains 80% of the drop: excess +47 points.
That's the "out of proportion" signal. **Analogy:** if a class's average score drops, the
student who was 10% of the points but caused 60% of the drop is the story, not the student
who was 40% of the points and caused 40% of the drop.

### Correlation is not causation
The evidence says camping fell *and* camping was out of stock at the same time. That is
strongly suggestive, but the explanation is told to say "coincides with", not "caused".

### Results
- California September drop: **found** (Camping, #1 finding; stockout signal too).
- September overall drop: **found** (Camping).
- Texas online orders down year-over-year: **found** via the marketing signal (social spend
  -100%). Orders were -4% while other states grew +30%.
- "Why did returns go up in spring 2026?": **missed**. The model first invented a filter
  (`reason = 'arrived late'`), which is now automatically removed, and then read "spring" as
  March vs February. Jackets are always returned a lot in winter, so March vs February hides
  the defect. A year-over-year comparison would have shown it. "Defective" still ranked #2.

### Self-test 4
14. Why isn't "the biggest segment of the change" a good definition of "the driver"?
15. What does the benchmark tell you that the total change doesn't?
16. Why does the explanation say "coincides with" rather than "caused"?

---

## 6. Explanations and the grounding check (Phase 5)

### Query explanation (no model)
"What does this SQL do?" is answered by walking the parse tree: *Reads orders, order_items;
Joins 2 times; Keeps rows where status = 'completed' AND...; Groups by category; Calculates
SUM; Sorts by revenue DESC; Returns at most 5 rows.* It can never describe a query that wasn't run.

### Answer explanation (model, then checked)
The model gets the question, the first rows of the result, the computed facts and the
warnings, and writes 2-4 sentences. Then the **grounding check** pulls every number out of the
text ("$2.38 million", "23.4%") and looks for it in the result table and the facts, allowing for
rounding and dropped minus signs. Any number it can't find becomes a warning: "the explanation
mentions numbers not found in the results".

This found real bugs in the *checker itself* along the way: it didn't understand the word
"million", and it treated "fell by 23.4%" as different from "-23.4%". Both were fixed with tests.

### Self-test 5
17. Why is the query explanation generated from the parse tree instead of by the model?
18. The model writes "revenue was $3.1M" but the result says 2,378,398. What happens?

---

## 7. Caching and conversation memory (Phase 6)

### Caching
A **cache** remembers answers so repeated work is skipped. Two levels:
- **Answer cache**: same question (ignoring case, spaces and trailing "?"), same user role,
  same model, same "today" -> return the stored answer. Several seconds becomes about 1 ms.
- **Result cache**: different wordings that produce the *same SQL* share one database query.

The key includes a fingerprint of the semantic layer and permissions, so if you change the
definition of revenue, old answers are automatically ignored. Entries also expire after an
hour (**TTL**, time to live), because real warehouses change.

### Conversation memory
"What about Texas?" means nothing alone. If the session has history and the new question
looks like a follow-up (short, or starts with "and", "what about", or uses "that/those"), the
model rewrites it into a full question using the earlier turns: *"What was revenue by category
in Texas last quarter?"* Everything after that works on the full question.

A security detail: a rewrite must never turn a refused question into an allowed one, so the
intent rules check **both** the original and the rewritten question.

Measured: 6/6 follow-up conversations answered correctly.

### Self-test 6
19. Why does the cache key include a fingerprint of the semantic layer?
20. "And their emails?" after a customer question: what stops this from leaking data?

---

## 8. Serving it (Phase 7)

- **FastAPI** web server: `POST /api/ask` returns the answer, table, chart spec, SQL, warnings
  and a per-stage timing trace; `GET /api/health`, `GET /api/schema`.
- A one-page web UI with the trace drawn as bars (orange = model call, blue = code/database),
  so you can see where time goes: almost all of it is model calls.
- **Dockerfile** and `docker compose --profile app up` run the API in a container that talks to
  the database container and to the model in WSL via `host.docker.internal`.
- **CI** (GitHub Actions): lint, create the schema and role in a fresh PostgreSQL, seed the data,
  check every gold query runs, run all tests (a scripted fake model replaces the real one), and
  check the safety report is up to date.

---

## Glossary

| Term | Meaning |
|---|---|
| AST (abstract syntax tree) | A tree representation of code's structure, produced by a parser |
| AWQ 4-bit | A way to compress model weights to 4 bits each so a bigger model fits in GPU memory |
| Benchmark (in diagnostics) | A comparison group used to tell "unusual" from "normal" change |
| Column-level grant | Permission to read only specific columns of a table |
| Data warehouse | A database organised for analysis |
| Defence in depth | Several independent safety layers, so one failure isn't fatal |
| Dimension / measure | A thing you group by / a number you add up |
| Docker / container | A packaged, isolated environment that runs the same everywhere |
| EXPLAIN | PostgreSQL's estimate of how a query will run and how costly it is |
| Execution accuracy | Share of questions where the generated query returns the right result |
| Excess contribution | Share of a change minus share of the baseline; finds disproportionate segments |
| Few-shot example | A worked example inside the prompt that shows the model the expected format |
| Grounding check | Verifying every number in generated text exists in the source data |
| Held-out set | Questions not looked at during tuning, used once for an honest score |
| Join | Combining rows of two tables that share a key |
| Least privilege | Give an account only the permissions it needs |
| LLM | Large language model |
| Prompt | The text sent to a language model |
| Read-only transaction | A database transaction in which any write is refused |
| Schema | The list of tables, columns and their types |
| Schema retrieval | Choosing which tables to describe to the model for a question |
| Seed | A starting number for a random generator, making randomness repeatable |
| Semantic layer | Business definitions (metrics, synonyms, joins) layered on top of raw tables |
| sqlglot | A Python library that parses and rewrites SQL |
| Statement timeout | The database kills any statement running longer than this |
| Temperature 0 | Model setting that always picks the most likely next word (repeatable output) |
| TTL | Time to live: how long a cache entry stays valid |
| Vega-Lite | A JSON format for describing charts |
| Wilson interval | A confidence interval for a proportion that behaves well with small samples |

---

## Answers to self-test questions

1. There is no known "true reason" in real data to check the answer against, and real data is private.
2. The same seed produces exactly the same data, so results can be reproduced.
3. Any of: write (INSERT/UPDATE/DELETE), drop tables, read email/names/phone, read the hr schema, run longer than 5 s.
4. Blocked but harmless: `SELECT 'DROP TABLE' AS note`. Missed but harmful: `SELECT pg_sleep(60)` or `SELECT email FROM customers`.
5. A tree showing the query's structure. It lets the validator check what the query *does* (e.g. a Delete node inside a WITH) instead of which words appear.
6. Defence in depth: the suite only covers attacks we thought of; the role still blocks anything the validator misses.
7. Many different queries are correct; only the results tell you whether the answer is right.
8. The model got calendar arithmetic wrong; code doesn't. Dev accuracy rose from 79% to 89%.
9. The loop fixes errors the database reports (bad column names, syntax). A logically wrong query runs fine, so nothing tells the model it's wrong.
10. With only 37 questions the true accuracy is uncertain; anything from about 66% to 91% is consistent with what we saw. Small differences between configurations aren't meaningful.
11. `product_id` is a label; adding IDs together is meaningless. Revenue is a quantity you sum.
12. A period whose value is more than 2 standard deviations from the fitted straight-line trend.
13. The share was of the five rows shown, not of total revenue. For top-N queries the shares are no longer computed, and the facts say the total is unknown.
14. The biggest segment usually contributes the most just because it's big. Excess contribution finds the segment that changed out of proportion to its size.
15. Whether the change is unusual. CA -23.4% vs other states -13.6% means about 10 points are specific to California.
16. The data shows things happening together; it can't prove one caused the other.
17. It can't be wrong about what the query does, and it costs no model call.
18. The grounding check can't find 3.1M (allowing for rounding) in the data, so the answer gets a warning to rely on the table.
19. So that changing a definition (e.g. what counts as revenue) or permissions invalidates stale answers automatically.
20. Intent rules check the original wording ("emails" is restricted) as well as the rewrite, and even if they didn't, the validator rejects the `email` column and the database role has no grant on it.

---

## Interview talking points

- "I measured a keyword blocklist against a parse-tree validator on 61 statements: 18/47 vs
  47/47 harmful blocked, 2 vs 0 false positives, and the database role is an independent
  second layer proven by tests that bypass the validator."
- "Held-out execution accuracy was 81% (30/37, 95% CI 66-91%) with a local 7B model; the 3B
  model was 57%, so model size mattered far more than any prompt trick."
- "Moving date arithmetic out of the model was the single biggest prompt-side gain."
- "Schema retrieval cost nothing in accuracy and saved 45% of prompt tokens. On 8 tables
  that's a cost win, not an accuracy win, and I'd say so."
- "For 'why' questions the model only extracts a spec; the analysis is deterministic and uses
  excess contribution plus a benchmark. It found 3 of 4 planted causes; the miss was an
  ambiguous time period."
- "Every number in a generated explanation is checked against the data."
