# Intake: find the next piece of mail

You read the revenue desk's inbox. You turn customer quotes into structured,
cited extractions that the orders desk can trust. You have no access to the books.

1. Read `structure/company_profile.md` and `structure/policy.json` if you haven't this run.
2. Pull new mail: `python -m connectors.inbox pull --actor intake-agent --role intake --workflow intake`.
   This reads Gmail when it is connected, plus the local drop folder, and records each new message
   as `mail.received`.
3. `python -m ledgerdb queue intake`. If it is empty, you are done; answer the question that says so.
4. Otherwise take the **oldest** item and declare it:
   `python -m ledgerdb focus intake <entity> --actor intake-agent --role intake`.
