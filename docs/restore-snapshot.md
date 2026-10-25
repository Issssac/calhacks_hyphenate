# Restore the settled demo run on another machine

This snapshot is the finished Halyard benchmark run (46 pass, 0 fail, 0 pending; the ledger verifies), taken
2026-10-25 at about 11:28 UTC. With it, `python -m desk.demo <segment>` runs anywhere, without the original machine.

## What's in `snapshot/`

| File | What it is | Size |
|---|---|---|
| `desk-home/ledger.db` | The ledger: a consistent copy made with `VACUUM INTO`. `ledgerdb verify` reports no problems. | 1.1 MB |
| `desk-home/inbox/` | Every email and PDF the desk received (`raw/`), with the extracted PDF text | 0.2 MB |
| `desk-home/sim_state.json` | The world simulator's record of what it sent | 8 KB |
| `desk-home/bank.json` | Increase **sandbox** account IDs (no keys) | 1 KB |
| `revenue.dump` | `pg_dump -Fc` of the Odoo database `revenue` | 5.0 MB |
| `filestore-revenue.tgz` | Odoo's attachment store for `revenue` (the PDFs and posted documents) | 2.4 MB |

The snapshot contains no secrets: no `.env`, no `tokens/`, no `hooks.json`, no API keys. The orders workflow's
adopted revision is committed on this branch (`workflows/history/orders/v10/`, `structure/policy.json`), which
the "learn" segment shows.

Versions: **Odoo 18.0 (image `odoo:18.0`, build 18.0-20261015)** and **Postgres 16 (image `postgres:16`)**, from
`docker-compose.yml`.

## Restore (Windows, Docker Desktop, PowerShell)

Run these from the repo root, on branch `widgex/demo-snapshot`.

```powershell
# 1. Postgres, then an empty database, then the dump (copied in, so no shell redirection of binary data)
docker compose up -d db
Start-Sleep 10
docker compose cp snapshot/revenue.dump db:/tmp/revenue.dump
docker compose exec -T db createdb -U odoo revenue
docker compose exec -T db pg_restore -U odoo -d revenue --no-owner /tmp/revenue.dump

# 2. Odoo, then its filestore
docker compose up -d odoo
Start-Sleep 15
docker compose cp snapshot/filestore-revenue.tgz odoo:/tmp/fs.tgz
docker compose exec -T -u root odoo sh -c "mkdir -p /var/lib/odoo/filestore && tar -C /var/lib/odoo/filestore -xzf /tmp/fs.tgz && chown -R odoo:odoo /var/lib/odoo/filestore"
docker compose restart odoo

# 3. Python (3.11 or newer; 3.14 is fine)
python -m pip install -e .

# 4. Point the desk at the snapshot (this PowerShell session only)
$env:REVENUE_DESK_HOME = "$PWD\snapshot\desk-home"
$env:ODOO_URL = "http://127.0.0.1:8069"     # 127.0.0.1, not localhost: much faster on Windows
$env:PYTHONIOENCODING = "utf-8"

# 5. Check, then run
python -m desk.demo --check                 # should end: learn is ready to record
python -m desk.demo                         # all segments; Enter between them
python -m desk.demo moment                  # or one segment at a time
```

Odoo is at http://127.0.0.1:8069. Log in as `admin` / `admin` (the local sandbox), database `revenue`.
Useful records: S00005 (the S03 price conflict), P00007, INV/2026/00004, BILL/2026/10/0005.

`LEDGER_DB` isn't needed: the desk finds `ledger.db` in `REVENUE_DESK_HOME`.

## Which segments need what

| Segment | Needs | Bank API key (Increase)? |
|---|---|---|
| moment | ledger, inbox | no |
| what | nothing | no |
| gate | Odoo running (it writes to a scratch ledger) | no |
| learn | ledger, `workflows/history/orders/v10/` | no |
| proof | ledger, sim_state, Odoo | no |
| phish | Odoo (vendor bank accounts on file) | no |
| close | nothing | no |

No segment calls the bank. Only a **new** run would need `INCREASE_API_KEY` (in `.env` or
`~/.revenue-desk/.env`): sending vendor payments, simulating customer payments, and `recon.py`'s bank balance.

I checked all of this on the original machine before committing:
- The dump was restored into a scratch database: 9 sales orders, company Halyard Marine Supply.
- Every segment run against `snapshot/desk-home` gave the same text as `docs/demo-output/` on `widgex/demo-media`. The only difference is the folder path printed in "what".
