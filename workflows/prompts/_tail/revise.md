# Revise your own workflow

Open feedback (`python -m ledgerdb feedback`) says this workflow got something wrong,
either through a controller overrule or a reconciliation mistake. Change the workflow so
the same thing is caught next time, and propose that change. Paths below are relative to
the repository root, the `cwd` you run tools from.

1. Read every open feedback row for this workflow, and `trace` the items it is about.
2. Diagnose. Which state should have caught it? Was a rule missing from a prompt? Was a check
   missing? Should there be a new state, or a new review point before something irreversible?
   Write the diagnosis down in one paragraph before changing anything.
3. Write the revision:
   - `python -m workflows.build compile <workflow>` copies the live machine to
     `workflows/proposed/<workflow>.json`. Edit that file. You may add or change states,
     transitions and questions. Keep `begin`, `done` and the adaptation states (`overruled`,
     `revise`, `adopt`, `promote`). A new state's prompt follows the same pattern as the others:
     a pointer to `workflows/prompts/<workflow>/<state>.md`.
   - To change what a state *says*, write the whole new prompt to
     `workflows/proposed/prompts/<workflow>/<state>.md`. Start from the live
     `workflows/prompts/<workflow>/<state>.md` and change only what the feedback calls for.
   - For a rule with a number in it (a tolerance, a contract price), put the number in policy and
     the rule in the prompt, so the controller can see and adjust it. **Never edit
     `structure/policy.json` directly.** Write only the keys you add or change to
     `workflows/proposed/policy.<workflow>.json` (a JSON object that is merged into the policy). It is
     part of your proposal, shows in the diff, and takes effect only when the revision is promoted.
     A proposal may only **add** keys, such as `contract_prices`. Changing an existing threshold
     (margin floor, tolerances, the controller) is refused. If you think one is wrong, say so in
     your proposal for the controller to change themselves.
   - A revision may add states, checks and review points, but never remove one. `promote` refuses
     a machine that drops any code check or review point the live version has.
4. Check it: `await api.harness_check(path)` with the absolute path of the proposed JSON. Fix and
   repeat until `ok` is true. Then record exactly that file as checked, passing the result on stdin:
   `api.run(["python", "-m", "workflows.build", "record-validation", "<workflow>", "--ok", "true",
   "--actor", "<workflow>-agent", "--role", "<role>"], input=json.dumps(result), cwd=...)`.
   Any later edit to the file needs checking and recording again.
5. Record `workflow.proposed` on `workflow:<workflow>`. The payload holds `summary` (one
   paragraph, in plain words), `diagnosis`, `addresses` (the feedback ids), `diff` (the output of
   `python -m workflows.build diff <workflow>`) and `readback` (from the check). Add
   `--link addresses=feedback:F-n` for each feedback item.

If after diagnosing you are sure no change is needed (a genuine one-off), close the feedback
without a new version:
`python -m workflows.build reject <workflow> --reason "..." --addresses feedback:F-n --actor .. --role ..`.

Do not edit any other workflow, the checks, the connectors or the ledger code. Those belong to
people, not to you.
