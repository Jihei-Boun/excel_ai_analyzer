# excel_ai_analyzer — working principles

## Generality comes from LLM judgment, not from more rules

This app answers natural-language questions about arbitrary Excel files. Do not
make it more general by adding rules for the cases you happened to see.

- **The LLM decides.** The LLM makes the judgment calls about a question or a table:
  - the analysis plan
  - which rows are headers, where the data starts and ends
  - which rows are subtotals or totals, and which are titles or notes
- **Code only verifies** facts that must hold for any correct answer:
  - no data rows were lost
  - a row called a subtotal really equals the sum of the rows above it
  - the plan passes validation
- **Failed verification** falls back with a visible warning. It does not get a
  new special case.
- **Do not add** word lists, layout-specific heuristics, or one patch per observed
  error. If a stronger model fixes a failure, prefer the model.
- **Before proposing a fix, ask yourself:** "is this a rule predicting a case, or
  LLM judgment plus an invariant check?" If a rule really seems unavoidable, call
  it a rule and ask first.

### Adding a verification check

A check is code too. It can block correct LLM answers, and checks added one per
observed failure turn into patches. Add one only if all three hold:

1. **It is a fact.** Every correct answer satisfies it, whatever the table looks
   like. If it holds only for some layouts, it is a rule.
2. **It only rejects.** It never picks the answer itself. Its message states what
   is inconsistent (rows, range, values) and does not hand the LLM a ready-made
   alternative.
3. **It generalises.** Even when a failure prompted it, its effect is judged on a
   held-out set nobody has looked at.

## Measure, don't guess

Judge changes with the benchmark in `tests/benchmark` (`runner.py --live`).

- Keep a held-out set that is not looked at while developing a fix, and run it at
  the end to check the fix generalises.
- Report outcomes as `correct` / `honest_failure` / `flagged_wrong` /
  `silent_wrong` / `crash`. A silent wrong answer is the worst outcome.

## Data hygiene

The repository is public. Never commit real spreadsheets, screenshots, or real
figures. Tests and benchmark datasets use synthetic numbers only.
