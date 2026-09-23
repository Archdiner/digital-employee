Use when maintaining, updating, extending, or checking a financial model or a spreadsheet that others rely on.

# Financial model hygiene

## Before changing anything
1. Read the model's structure first: tab list, inputs vs calculations vs outputs, the version and date on the cover or header. Do not edit until you can say where inputs live.
2. Identify hard-coded numbers inside formula areas. They are the first thing to report, not to fix silently.
3. Record the current outputs (the key totals) so the change can be checked against them afterwards.

## Making the change
- Change inputs in the input area only. New assumptions go next to existing ones with a label, a unit, a source, and a date.
- Extend formulas by copying the existing pattern across; do not write a new formula style mid-row.
- When writing values into a shared workbook, write only the cells asked for. Do not reformat, insert rows, or rename tabs; other people's links depend on them.
- Every number you put in comes from a fact or a computation with a recorded formula.

## Checks after the change
- Balance sheet balances; cash flow ties to the change in cash; sub-totals sum to totals. Report each check as pass or fail.
- Compare key outputs to the pre-change record. Explain every movement.
- Look for #REF, #DIV/0, circular references, and cells that now show text where numbers should be.

## Hand-over
State in one paragraph: what changed, which cells, why, and what the outputs moved by. Keep the prior version.

## Never
- Never overwrite a formula with a value to "make it match".
- Never hide rows or tabs to tidy up.
- Never guess a missing input. Leave the cell empty, flag it, ask.
