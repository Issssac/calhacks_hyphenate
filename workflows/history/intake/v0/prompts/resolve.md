# Intake: record the approved resolution

The controller approved your recommendation. Their note, if any, is in the message that
brought you here.

Append `quote.clarified` on the quote. Use the recommended extraction as the payload, in the
same shape as `quote.extracted`, with every line keeping its `source`. Add
`"resolved_by": "<controller>", "resolution_note": "<their note>", "resolves": "<the conflict's seq>"`.
