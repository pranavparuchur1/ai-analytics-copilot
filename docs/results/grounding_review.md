# Grounding check: manual review of flagged answers

The grounding check (`explain.ungrounded_numbers`) flagged 5 of the 55 explanations in the
final `scripts/eval_pipeline.py` run (`evaluation/runs/pipeline.json`). Each was checked by
hand against the query result:

| Id | Flagged number | Verdict |
|---|---|---|
| q39 | "$23,400" drop from August to September | **Real error caught.** The actual drop is $161,993 (691,372 -> 529,378); the model mixed the 23.4% figure into a dollar amount |
| q20 | 40.4% online share | Correct, derived by the model (100% - 59.6%); false alarm |
| q24 | 25.0%, 10.0% channel shares | Correct to rounding (24.9%, 9.99%), derived; false alarm |
| q46 | $33,195 decrease | Correct (94,185 - 60,990), derived; false alarm |
| q42 | "10" | Not traceable to a claim in the text; unclear |

So in this run: 1 real error caught, 3 false alarms on correct arithmetic, 1 unclear.

Two earlier runs of the same evaluation exposed bugs in the checker itself, not the model:
it did not understand "$2.38 million", and it read years followed by a comma ("In 2025,")
as numbers. Both are fixed and unit-tested (`tests/test_explain.py`).

## Limits
- The check verifies a number exists somewhere in the result or facts, not that the text
  attaches it to the right thing. "Texas had 1,893,904" would pass if California had that value.
- Numbers the model computes itself (differences, shares) are flagged even when right.
  Adding totals and two-row differences to the facts reduced these; a next step is to have
  the checker recompute simple sums, differences and ratios of source numbers.
- A clean check on the other 50 answers means no *untraceable* numbers, not that every
  sentence is correct.
