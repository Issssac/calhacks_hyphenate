"""The central, append-only event log the agents share.

Every write goes through `append`, which enforces three rules in code rather
than in prompts:

* a role may only write the event kinds listed for it in roles.toml;
* nobody may approve something they created or drafted;
* nothing is posted (confirmed) in the books until its posting gate is satisfied.

Rows are hash-chained, so `verify` detects any edit made behind the triggers.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import sqlite3
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

PKG_DIR = Path(__file__).resolve().parent
REPO_ROOT = PKG_DIR.parent
GENESIS = "0" * 64
ENTITY_RE = re.compile(r"^[a-z][a-z0-9_]*:[A-Za-z0-9][A-Za-z0-9._\-/#]*$")
DEFINING_KINDS_SQL = "(kind LIKE '%.drafted' OR kind LIKE '%.amended' OR kind = 'bill.received')"

# Fields that go into each row's hash, in this order.
HASHED_FIELDS = (
    "ts", "actor", "recorded_by", "role", "run_id", "workflow", "workflow_version",
    "kind", "entity", "payload", "sources", "links",
)


class LedgerError(ValueError):
    """A write the ledger refused. The message says which rule and why."""


def desk_home() -> Path:
    """One folder per machine for shared state, outside any checkout, so every
    agent appends to the same ledger whichever worktree it runs in."""
    env = os.environ.get("REVENUE_DESK_HOME")
    return Path(env) if env else Path.home() / ".revenue-desk"


def default_path() -> Path:
    env = os.environ.get("LEDGER_DB")
    return Path(env) if env else desk_home() / "ledger.db"


def connect(path: Path | str | None = None, readonly: bool = False) -> sqlite3.Connection:
    path = Path(path) if path else default_path()
    if readonly:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=15)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=15, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.row_factory = sqlite3.Row
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript((PKG_DIR / "schema.sql").read_text(encoding="utf-8"))
    conn.executescript((PKG_DIR / "views.sql").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- roles

@dataclass(frozen=True)
class Policy:
    roles: dict[str, dict[str, Any]]
    common: tuple[str, ...]
    posting: dict[str, str]

    def allowed(self, role: str, kind: str) -> bool:
        spec = self.roles.get(role)
        if spec is None:
            return False
        patterns = list(spec.get("kinds", []))
        if not spec.get("human") and role != "simulator":
            patterns += self.common
        return any(fnmatch.fnmatchcase(kind, p) for p in patterns)


def load_policy(path: Path | None = None) -> Policy:
    data = tomllib.loads((path or PKG_DIR / "roles.toml").read_text(encoding="utf-8"))
    return Policy(
        roles=data.get("roles", {}),
        common=tuple(data.get("common", {}).get("kinds", [])),
        posting=data.get("posting", {}),
    )


# ---------------------------------------------------------------- hashing

def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def row_hash(prev_hash: str, fields: dict[str, Any]) -> str:
    body = canonical([prev_hash] + [fields.get(k) for k in HASHED_FIELDS])
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _dec(x: Any, what: str) -> Decimal:
    try:
        return Decimal(str(x))
    except (InvalidOperation, ValueError) as e:
        raise LedgerError(f"{what} is not a number: {x!r}") from e


# ---------------------------------------------------------------- rules

def _check_entity(entity: str, what: str = "entity") -> str:
    if not isinstance(entity, str) or not ENTITY_RE.match(entity):
        raise LedgerError(f"{what} must look like type:id (e.g. so:SO-1001), got {entity!r}")
    return entity.split(":", 1)[0]


def _norm_links(links: Iterable[Any] | None) -> list[dict[str, Any]]:
    out = []
    for link in links or []:
        if not isinstance(link, dict) or "rel" not in link or "to" not in link:
            raise LedgerError(f"each link needs rel and to, got {link!r}")
        _check_entity(link["to"], "link target")
        clean = {"rel": str(link["rel"]), "to": link["to"]}
        if link.get("amount") is not None:
            clean["amount"] = float(_dec(link["amount"], "link amount"))
        out.append(clean)
    return out


def _check_je(payload: dict[str, Any]) -> None:
    lines = payload.get("lines")
    if not isinstance(lines, list) or len(lines) < 2:
        raise LedgerError("je.posted needs payload.lines with at least two lines")
    dr = cr = Decimal(0)
    for i, line in enumerate(lines, 1):
        if not line.get("account"):
            raise LedgerError(f"journal line {i} has no account")
        d = _dec(line.get("debit", 0), f"line {i} debit")
        c = _dec(line.get("credit", 0), f"line {i} credit")
        if d < 0 or c < 0 or (d and c) or not (d or c):
            raise LedgerError(f"journal line {i} must have exactly one positive debit or credit")
        dr += d
        cr += c
    if dr.quantize(Decimal("0.01")) != cr.quantize(Decimal("0.01")):
        raise LedgerError(f"journal entry does not balance: debits {dr} != credits {cr}")


def _entity_actors(conn: sqlite3.Connection, entity: str) -> set[str]:
    rows = conn.execute(
        "SELECT DISTINCT actor, recorded_by FROM events WHERE entity = ? AND kind NOT LIKE 'approval.%'",
        (entity,),
    ).fetchall()
    return {r[0] for r in rows} | {r[1] for r in rows}


def _last_defining_seq(conn: sqlite3.Connection, entity: str) -> int | None:
    return conn.execute(
        f"SELECT MAX(seq) FROM events WHERE entity = ? AND {DEFINING_KINDS_SQL}", (entity,)
    ).fetchone()[0]


def is_approved(conn: sqlite3.Connection, entity: str) -> bool:
    """True when the latest approval decision after the latest draft is a grant."""
    last = _last_defining_seq(conn, entity) or 0
    row = conn.execute(
        "SELECT kind FROM events WHERE entity = ? AND kind IN ('approval.granted','approval.rejected') "
        "AND seq > ? ORDER BY seq DESC LIMIT 1",
        (entity, last),
    ).fetchone()
    return bool(row) and row[0] == "approval.granted"


def is_posted(conn: sqlite3.Connection, entity: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM events WHERE entity = ? AND kind = 'erp.posted' LIMIT 1", (entity,)
    ).fetchone() is not None


def _parents(conn: sqlite3.Connection, entity: str, rel: str, new_links: list[dict]) -> list[str]:
    found = [l["to"] for l in new_links if l["rel"] == rel]
    for (links,) in conn.execute("SELECT links FROM events WHERE entity = ?", (entity,)):
        found += [l["to"] for l in json.loads(links) if l.get("rel") == rel]
    return sorted(set(found))


def _check_posting(conn: sqlite3.Connection, policy: Policy, entity: str, etype: str,
                   links: list[dict]) -> None:
    rule = policy.posting.get(etype)
    if rule is None:
        raise LedgerError(f"no posting policy for {etype!r}; add one to roles.toml [posting]")
    if rule == "none":
        return
    if rule == "approved":
        if not is_approved(conn, entity):
            raise LedgerError(f"{entity} cannot be posted: it has no approval after its latest draft")
        return
    name, _, rel = rule.partition(":")
    parents = _parents(conn, entity, rel, links)
    if not parents:
        raise LedgerError(f"{entity} cannot be posted: it is not linked to anything via {rel!r}")
    if name == "parent_approved":
        bad = [p for p in parents if not is_approved(conn, p)]
        if bad:
            raise LedgerError(f"{entity} cannot be posted: parent {', '.join(bad)} is not approved")
        return
    if name == "matched_or_approved":
        unposted = [p for p in parents if not is_posted(conn, p)]
        if unposted:
            raise LedgerError(f"{entity} cannot be posted: parent {', '.join(unposted)} was never posted")
        last = _last_defining_seq(conn, entity) or 0
        matched = conn.execute(
            "SELECT 1 FROM events WHERE entity = ? AND kind = 'bill.matched' AND seq > ?", (entity, last)
        ).fetchone()
        if not matched and not is_approved(conn, entity):
            raise LedgerError(f"{entity} cannot be posted: no 3-way match and no approval")
        return
    raise LedgerError(f"unknown posting rule {rule!r} for {etype!r}")


def check_post(conn: sqlite3.Connection, entity: str, links: list[dict[str, Any]] | None = None,
               policy: Policy | None = None) -> None:
    """Raise LedgerError unless `entity` may be posted in the books right now."""
    etype = _check_entity(entity)
    _check_posting(conn, policy or load_policy(), entity, etype, _norm_links(links))


def latest_payload(conn: sqlite3.Connection, entity: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT payload FROM doc_latest WHERE entity = ?", (entity,)).fetchone()
    return json.loads(row[0]) if row else None


def erp_ref(conn: sqlite3.Connection, entity: str, posted_only: bool = False) -> dict[str, Any] | None:
    """The ERP record an entity was last staged or posted as: {model, id, name, ...}."""
    kinds = "('erp.posted')" if posted_only else "('erp.staged', 'erp.posted')"
    row = conn.execute(
        f"SELECT payload FROM events WHERE entity = ? AND kind IN {kinds} ORDER BY seq DESC LIMIT 1",
        (entity,),
    ).fetchone()
    return json.loads(row[0]) if row else None


def entity_for_erp(conn: sqlite3.Connection, model: str, record_id: int | str) -> str | None:
    row = conn.execute(
        "SELECT entity FROM events WHERE kind IN ('erp.staged', 'erp.posted')"
        " AND json_extract(payload, '$.model') = ? AND CAST(json_extract(payload, '$.id') AS TEXT) = ?"
        " ORDER BY seq DESC LIMIT 1",
        (model, str(record_id)),
    ).fetchone()
    return row[0] if row else None


def links_of(conn: sqlite3.Connection, entity: str, rel: str) -> list[str]:
    return _parents(conn, entity, rel, [])


# ---------------------------------------------------------------- append

def append(
    conn: sqlite3.Connection,
    *,
    actor: str,
    role: str,
    kind: str,
    entity: str,
    payload: dict[str, Any] | None = None,
    sources: list[Any] | None = None,
    links: list[dict[str, Any]] | None = None,
    run_id: str | None = None,
    workflow: str | None = None,
    workflow_version: int | None = None,
    on_behalf_of: str | None = None,
    policy: Policy | None = None,
    ts: str | None = None,
    unique_entity: bool = False,
) -> dict[str, Any]:
    policy = policy or load_policy()
    if not actor:
        raise LedgerError("actor is required")
    etype = _check_entity(entity)
    if not policy.allowed(role, kind):
        raise LedgerError(f"role {role!r} may not write {kind!r} (see ledgerdb/roles.toml)")

    spec = policy.roles[role]
    recorded_by = actor
    if spec.get("human"):
        # model:<name> is allowed because the brief lets a model play the approver.
        if not (on_behalf_of or "").startswith(("human:", "model:")):
            raise LedgerError(
                f"role {role!r} acts for an approver: pass --on-behalf-of human:<name> (or model:<name>)"
            )
        actor = on_behalf_of
    elif on_behalf_of:
        raise LedgerError(f"role {role!r} cannot act on behalf of anyone")

    payload = payload if payload is not None else {}
    if not isinstance(payload, dict):
        raise LedgerError("payload must be a JSON object")
    sources = list(sources or [])
    norm_links = _norm_links(links)

    if role == "simulator":
        payload = {**payload, "simulated": True}
    if kind == "je.posted":
        _check_je(payload)
    if kind.startswith("approval.") and kind != "approval.requested":
        if not conn.execute("SELECT 1 FROM events WHERE entity = ? LIMIT 1", (entity,)).fetchone():
            raise LedgerError(f"cannot decide on {entity}: it does not exist")
        if actor in _entity_actors(conn, entity):
            raise LedgerError(f"{actor} created or worked on {entity} and may not also approve it")
    if kind == "erp.posted":
        _check_posting(conn, policy, entity, etype, norm_links)
    if kind == "payment.applied":
        if not any(l["rel"] == "applies_to" and "amount" in l for l in norm_links):
            raise LedgerError("payment.applied needs a link rel=applies_to with an amount")

    fields = {
        "ts": ts or now(),
        "actor": actor,
        "recorded_by": recorded_by,
        "role": role,
        "run_id": run_id,
        "workflow": workflow,
        "workflow_version": workflow_version,
        "kind": kind,
        "entity": entity,
        "payload": canonical(payload),
        "sources": canonical(sources),
        "links": canonical(norm_links),
    }
    conn.execute("BEGIN IMMEDIATE")
    try:
        if unique_entity and conn.execute("SELECT 1 FROM events WHERE entity = ? LIMIT 1", (entity,)).fetchone():
            raise LedgerError(f"{entity} already exists")
        row = conn.execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        prev = row[0] if row else GENESIS
        h = row_hash(prev, fields)
        cur = conn.execute(
            "INSERT INTO events (ts, actor, recorded_by, role, run_id, workflow, workflow_version, kind,"
            " entity, entity_type, payload, sources, links, prev_hash, hash)"
            " VALUES (:ts,:actor,:recorded_by,:role,:run_id,:workflow,:workflow_version,:kind,"
            " :entity,:entity_type,:payload,:sources,:links,:prev_hash,:hash)",
            {**fields, "entity_type": etype, "prev_hash": prev, "hash": h},
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {"seq": cur.lastrowid, "hash": h, "entity": entity, "kind": kind, "actor": actor}


# ---------------------------------------------------------------- reads

def rows(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    out = []
    for r in conn.execute(sql, tuple(params)).fetchall():
        d = dict(r)
        for k in ("payload", "sources", "links"):
            if isinstance(d.get(k), str):
                try:
                    d[k] = json.loads(d[k])
                except json.JSONDecodeError:
                    pass
        out.append(d)
    return out


def verify(conn: sqlite3.Connection) -> dict[str, Any]:
    """Recompute the hash chain. Any edited, reordered or removed row shows up."""
    prev = GENESIS
    problems = []
    n = 0
    for r in conn.execute("SELECT * FROM events ORDER BY seq"):
        n += 1
        d = dict(r)
        if d["prev_hash"] != prev:
            problems.append({"seq": d["seq"], "problem": "prev_hash does not match the row before it"})
        if row_hash(d["prev_hash"], d) != d["hash"]:
            problems.append({"seq": d["seq"], "problem": "row contents do not match its hash"})
        prev = d["hash"]
    return {"ok": not problems, "events": n, "head": prev, "problems": problems}


# Links that describe governance (feedback about a document, a revision answering feedback)
# rather than the document chain. Trace shows them one hop out but does not walk through
# them, so one workflow revision doesn't join every deal it was ever about.
GOVERNANCE_RELS = ("about", "addresses")


def trace(conn: sqlite3.Connection, entity: str) -> dict[str, Any]:
    """The document chain this entity belongs to (quote, orders, invoices, bills, payments,
    mail, bank), the feedback and revisions about it, and all their events in order."""
    _check_entity(entity)
    marks = ",".join("?" * len(GOVERNANCE_RELS))
    seen = {entity}
    frontier = [entity]
    while frontier:
        nxt = []
        for e in frontier:
            for (src, dst) in conn.execute(
                f"SELECT src, dst FROM doc_links WHERE (src = ? OR dst = ?) AND rel NOT IN ({marks})",
                (e, e, *GOVERNANCE_RELS),
            ):
                for x in (src, dst):
                    if x not in seen:
                        seen.add(x)
                        nxt.append(x)
        frontier = nxt
    chain = set(seen)
    for e in chain:
        for (src,) in conn.execute(f"SELECT src FROM doc_links WHERE dst = ? AND rel IN ({marks})",
                                   (e, *GOVERNANCE_RELS)):
            seen.add(src)
    marks = ",".join("?" * len(seen))
    events = rows(
        conn,
        f"SELECT seq, ts, actor, recorded_by, role, workflow, workflow_version, kind, entity,"
        f" payload, sources, links FROM events WHERE entity IN ({marks}) ORDER BY seq",
        sorted(seen),
    )
    return {"root": entity, "entities": sorted(chain), "governance": sorted(seen - chain), "events": events}


def next_id(conn: sqlite3.Connection, entity_type: str, prefix: str, start: int = 1001,
            actor: str | None = None, role: str | None = None) -> str:
    """The next unused id for a type, e.g. next_id(conn, 'so', 'SO') -> 'SO-1001'.

    With an actor, the id is also reserved atomically (a `note.reserved` event on
    reserve:<type>-<id>), so two agents asking at once never get the same id.
    """
    def taken(i: int) -> bool:
        return conn.execute("SELECT 1 FROM events WHERE entity IN (?, ?) LIMIT 1",
                            (f"{entity_type}:{prefix}-{i}", f"reserve:{entity_type}-{prefix}-{i}")).fetchone() is not None

    n = conn.execute("SELECT COUNT(DISTINCT entity) FROM events WHERE entity_type = ?", (entity_type,)).fetchone()[0]
    i = start + n
    for _ in range(1000):
        while taken(i):
            i += 1
        if not actor:
            return f"{prefix}-{i}"
        try:
            append(conn, actor=actor, role=role or "architect", kind="note.reserved",
                   entity=f"reserve:{entity_type}-{prefix}-{i}", payload={"id": f"{entity_type}:{prefix}-{i}"},
                   unique_entity=True)
            return f"{prefix}-{i}"
        except LedgerError as e:
            if "already exists" not in str(e):
                raise
            i += 1  # someone reserved it a moment ago
    raise LedgerError(f"could not reserve an id for {entity_type}")
