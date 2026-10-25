# Promote the adopted revision

The controller adopted your revision. If you came here from a review card (not the ledger),
first record their decision: `approval.granted` on `workflow:<workflow>`, on the controller's
behalf, with their note. `promote` refuses without an approval recorded after the proposal was
validated. It also refuses a revision that removes any code check or review point, or that
changes an existing policy key. A revision can add controls, never remove them.

Then make it live:

```
python -m workflows.build promote <workflow> --actor <workflow>-agent --role <role> \
    --addresses feedback:F-n --summary "<one line: what changed>"
```

Pass one `--addresses` for each feedback item it answers. This archives the old version
under `workflows/history/`, makes the proposal live, and records `workflow.adopted` with the
new version number. Report that version number in your message.

New prompts take effect at once. A changed state machine takes effect the next time this
workflow is started.
