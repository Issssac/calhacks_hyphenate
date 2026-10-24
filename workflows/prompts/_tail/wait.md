# Wait for work

Nothing is actionable for you right now. Other desks may be about to hand you something.

Wait two minutes in the terminal (`await asyncio.sleep(120)`), then answer the questions.
You stop for good only when the code check says the whole desk has been idle for the policy's
`idle_minutes`. Otherwise you go back to `begin` and look again.
