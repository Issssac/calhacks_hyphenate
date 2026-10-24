# Architect: make the pipeline live and start its agents

The controller approved the pipeline. Their note, if any, is in the message that brought you here.

1. **Promote** every enabled driver to its first live version:
   `python -m workflows.build promote <driver> --actor architect-agent --role architect --summary "initial version from the architect"`.
2. **Start the agents**, one per enabled driver, in pipeline order. Each call asks the person for
   permission. That is the point: they see every agent before it starts.

   ```python
   child = await api.agent(
       "You are the <driver> agent of the revenue desk. A workflow drives you state by state; "
       "each state's prompt says which instructions file to read. Follow it exactly.",
       wait=False,
       machine=r"<absolute repo path>/.widgex/harnesses/<driver>.json",
       model="<model from pipeline.json>",
       token_budget=<token_budget from pipeline.json>,
       summary="The architect surveyed the books and built this pipeline; structure/company_profile.md describes the company.",
   )
   ```

   After each one, record `structure.spawned` on `structure:agents` with the driver, `child["sessionId"]`,
   the model and the workflow version.
3. If starting agents is refused here (this session runs unattended and may not be allowed to),
   don't retry. Call `api.session_rest(reason=...)` and put the exact list of agents to start in the
   reason, so a person can start them from the lead session.
