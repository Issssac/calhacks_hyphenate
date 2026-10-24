# Promote the adopted revision

The controller adopted your revision. Make it live:

```
python -m workflows.build promote <workflow> --actor <workflow>-agent --role <role> \
    --addresses feedback:F-n --summary "<one line: what changed>"
```

Pass one `--addresses` for each feedback item it answers. This archives the old version
under `workflows/history/`, makes the proposal live, and records `workflow.adopted` with the
new version number. Report that version number in your message.

New prompts take effect at once. A changed state machine takes effect the next time this
workflow is started.
