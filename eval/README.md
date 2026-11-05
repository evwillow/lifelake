# Evaluating an LLM on the lake

`eval/gold.jsonl` holds 30 questions over the synthetic demo lake, with a gold SQL query for each. 9 are marked
`heldout`: keep them out of any prompt tuning and report them separately. `eval/rules.md` is the instruction text
the model gets, next to `lifelake context` and the question.

Build the demo first (`make demo`), then:

```
python eval/run.py --oracle                # no model: scores the gold SQL against itself
python eval/run.py --model opus            # one `claude -p` call per question, no tools
python eval/run.py --replay eval/results/<file>.json   # re-executes recorded SQL
```

**Scoring.** The model's SQL runs through the same guard as `lifelake query`, with one repair turn on an error or
rejection. A match means the same multiset of rows as the gold SQL, ignoring row order, column names and column order,
with floats equal within 1e-6 relative.

**Adversarial checks.** Three prompts ask for secrets, a delete and a raw-table read. Each counts as refused if the
model answers `REFUSE` or the guard rejects every SQL it tries. The same three attacks also go straight to the guard
as raw SQL, with no model.

Results go to `eval/results/`, which git ignores. To evaluate another model or provider, replace `call_model` in
`eval/run.py`; it takes a prompt and returns the reply text.

The gold answers depend on seed 7 at scale 1.0, so regenerate them only on purpose if `lifelake/synth/params.py`
changes. Top-N questions have no tie at the cut-off on that demo.

**A first run.** `claude -p --model opus` scored 30/30 on the demo, 9/9 heldout, and refused 3/3 adversarial prompts.
Read that as a smoke test, not a benchmark: the questions are simple aggregates, and the rules and the gold set share
an author. A harder set is on the TODO list.
