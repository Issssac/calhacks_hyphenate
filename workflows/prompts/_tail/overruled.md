# Overruled: fix the item, then record why

The controller rejected your work. Their note is in the message that brought you to this
state. Read it carefully.

1. **Make the item right first.** Apply what the controller said: re-draft, re-stage or
   correct, with new events. Corrections are new events; never try to remove old ones.
   If their note leaves the right answer unclear, record what you did understand in a
   `note.question` event and do not guess beyond it.
2. **Record the feedback.** Append `feedback.recorded` on a new `feedback:F-n`
   (`python -m ledgerdb next-id feedback F`). The payload:

   ```json
   {"driver": "<this workflow>", "state": "<the state that was rejected>", "type": "overrule",
    "target": "<entity>", "note": "<the controller's words, verbatim>",
    "what_i_did": "<how you corrected the item>",
    "workflow_version": <from python -m workflows.build current>}
   ```

   Link it with `--link about=<entity>`.

The next state asks you to work out why your workflow let this happen.
