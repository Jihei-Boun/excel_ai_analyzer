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

### Changing a prompt

A prompt is code too. A sentence added after each observed failure turns the
prompt into the word list we removed from the code.

- **What a prompt may say:**
  - what the model is given (tables, tools, actions) and what each action does
  - the output format
  - general ways of working (one step at a time, check each result, compute
    values from the data instead of typing them)
- **What it must not say:**
  - how to handle a kind of question ("for a discount rate, compute it per channel")
  - domain words, column names or layouts taken from test questions or datasets
  - a sentence whose only reason is one observed failure ("do not forget the
    total row")
- **Prefer facts over instructions.** When the model gets something wrong, first
  fix what it is shown: a wrong table read, a misleading preview, an error
  message without the failing line. Tell it what is true ("this is the same code
  as your previous step"), not what to do.
- **One prompt change per commit.** The message says why the change was made and
  what it did on a held-out set. A change with no measurable effect is removed.
- **Prune.** From time to time, remove sentences one at a time; if the score does
  not drop, the sentence goes.
- **Tests guard the obvious cases:** prompts may not contain words from benchmark
  questions or datasets, and each prompt has a length limit. Raising a limit is
  a visible change and needs a reason in the commit.

## Measure, don't guess

Judge changes with the benchmark in `tests/benchmark` (`runner.py --live`).

- Keep a held-out set that is not looked at while developing a fix, and run it at
  the end to check the fix generalises.
- Report outcomes as `correct` / `honest_failure` / `flagged_wrong` /
  `silent_wrong` / `crash`. A silent wrong answer is the worst outcome.

## Data hygiene

The repository is public. Never commit real spreadsheets, screenshots, or real
figures. Tests and benchmark datasets use synthetic numbers only.
