# Architect: compile and check the driver workflows

1. `python -m workflows.build compile`. For each enabled driver in `structure/pipeline.json`, this
   writes `workflows/proposed/<driver>.json`. It starts from the live version if one exists,
   otherwise from the template.
2. Read each proposed machine. Where this company needs something the template lacks, edit the
   proposal. For example: an extra review state before a step that is risky here, or a question
   reworded for how this company works. Company-specific *guidance* belongs in
   `company_profile.md`, not in the machines.
3. Check every proposed file:
   `result = await api.harness_check(r"<absolute path to workflows/proposed/<driver>.json>")`.
   Fix it until `result["ok"]`. Then record exactly that file:
   `api.run(["python", "-m", "workflows.build", "record-validation", "<driver>", "--ok", "true",
   "--actor", "architect-agent", "--role", "architect"], input=json.dumps(result), cwd=...)`.
4. Keep each driver's `readback`. You will show the controller these in the next state.
