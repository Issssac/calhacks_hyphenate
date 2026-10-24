# Ask to adopt the revision

Put the proposal in front of the controller. In this turn, write a short message that
covers:

- **What went wrong**: the feedback, quoted.
- **Why the workflow let it happen**: your diagnosis, in one or two sentences.
- **What changes**, in plain words. For example: "orders now looks up the customer's
  contract price before drafting, and flags any line priced differently."
- **The new flow**: the readback from `harness_check`, trimmed to the lines that changed.
- **The diff**: from `python -m workflows.build diff <workflow>`, trimmed to what matters.

Then stop and wait. The controller will adopt it or send it back with a note.
