"""Gmail connector, over Gmail's REST API with plain `requests`.

Two separate tokens, for separation of duties:
    gmail-read   gmail.readonly                 used by the intake agent; cannot send or change mail
    gmail-send   gmail.send + gmail.modify      used only by seed scripts to deliver test mail

    python -m connectors.gmail connect --scope read      one-time consent, prints a URL
    python -m connectors.gmail connect --scope send
    python -m connectors.gmail pull --actor intake-1 --role intake
    python -m connectors.gmail send --to me --subject "Quote" --body @body.txt --attach quote.pdf

`pull` downloads every message under GMAIL_LABEL that the ledger hasn't seen into
inbox/raw/<gmail id>/ (one folder per message: the content is untrusted), extracts
PDF text page by page, and appends mail.received. The intake agent classifies it.
"""

from __future__ import annotations

import argparse
import base64
import email
import email.policy
import hashlib
import html
import http.server
import json
import mimetypes
import os
import re
import secrets
import sys
import threading
import time
import urllib.parse
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import requests

from ledgerdb import db
from . import pdftext
from .env import DATA_DIR, need, read_token, write_token

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
SCOPES = {
    "read": ["https://www.googleapis.com/auth/gmail.readonly"],
    "send": ["https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/gmail.modify"],
}
RAW_DIR = DATA_DIR / "inbox" / "raw"  # untrusted content, one folder per message


class GmailError(RuntimeError):
    pass


class Gmail:
    def __init__(self, scope: str = "read") -> None:
        cfg = need("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET")
        self.client_id, self.client_secret = cfg["GMAIL_CLIENT_ID"], cfg["GMAIL_CLIENT_SECRET"]
        self.scope = scope
        self.token = read_token(f"gmail-{scope}")
        self.session = requests.Session()

    def _refresh(self) -> None:
        if not self.token.get("refresh_token"):
            raise GmailError(f"no gmail-{self.scope} token: run `python -m connectors.gmail connect --scope {self.scope}`")
        r = requests.post(TOKEN_URL, timeout=30, data={
            "client_id": self.client_id, "client_secret": self.client_secret,
            "refresh_token": self.token["refresh_token"], "grant_type": "refresh_token"})
        if r.status_code != 200:
            raise GmailError(f"token refresh failed: {r.status_code} {r.text[:300]}")
        tok = r.json()
        self.token.update(tok)
        self.token["expires_at"] = time.time() + int(tok.get("expires_in", 3600)) - 60
        write_token(f"gmail-{self.scope}", self.token)

    def request(self, method: str, path: str, **kw: Any) -> dict:
        if self.token.get("expires_at", 0) < time.time():
            self._refresh()
        for attempt in (1, 2):
            r = self.session.request(method, API + path, timeout=60,
                                     headers={"Authorization": f"Bearer {self.token['access_token']}"}, **kw)
            if r.status_code == 401 and attempt == 1:
                self._refresh()
                continue
            break
        if r.status_code >= 400:
            raise GmailError(f"{method} {path}: {r.status_code} {r.text[:300]}")
        return r.json() if r.text else {}

    def label_id(self, name: str) -> str | None:
        for lab in self.request("GET", "/labels").get("labels", []):
            if lab["name"].lower() == name.lower():
                return lab["id"]
        return None

    def list_ids(self, query: str) -> list[str]:
        ids, page = [], None
        while True:
            params = {"q": query, "maxResults": 100}
            if page:
                params["pageToken"] = page
            data = self.request("GET", "/messages", params=params)
            ids += [m["id"] for m in data.get("messages", [])]
            page = data.get("nextPageToken")
            if not page:
                return ids

    def raw(self, msg_id: str) -> tuple[bytes, dict]:
        data = self.request("GET", f"/messages/{msg_id}", params={"format": "raw"})
        return base64.urlsafe_b64decode(data["raw"]), data


# ---------------------------------------------------------------- oauth

def connect(scope: str, port: int = 0) -> dict:
    g = Gmail(scope)
    srv = http.server.HTTPServer(("127.0.0.1", port), http.server.BaseHTTPRequestHandler)
    redirect = f"http://127.0.0.1:{srv.server_address[1]}/"
    state = secrets.token_urlsafe(16)
    got: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            got.update(dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query)))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Gmail connected. You can close this tab.")

        def log_message(self, *a):
            pass

    srv.RequestHandlerClass = Handler
    threading.Thread(target=srv.handle_request, daemon=True).start()
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": g.client_id, "redirect_uri": redirect, "response_type": "code",
        "scope": " ".join(SCOPES[scope]), "access_type": "offline", "prompt": "consent", "state": state})
    print(json.dumps({"open_this_url": url}), flush=True)
    deadline = time.time() + 600
    while not got and time.time() < deadline:
        time.sleep(0.5)
    srv.server_close()
    if got.get("state") != state or "code" not in got:
        raise GmailError(f"consent did not complete: {got.get('error') or 'timed out'}")
    r = requests.post(TOKEN_URL, timeout=30, data={
        "client_id": g.client_id, "client_secret": g.client_secret, "code": got["code"],
        "redirect_uri": redirect, "grant_type": "authorization_code"})
    if r.status_code != 200:
        raise GmailError(f"code exchange failed: {r.status_code} {r.text[:300]}")
    tok = r.json()
    tok["expires_at"] = time.time() + int(tok.get("expires_in", 3600)) - 60
    write_token(f"gmail-{scope}", tok)
    return {"ok": True, "scope": scope}


# ---------------------------------------------------------------- pull

def _safe(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .") or "attachment"
    return name[:120]


def _body_text(msg: email.message.EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    text = part.get_content()
    if part.get_content_type() == "text/html":
        text = re.sub(r"(?is)<(script|style).*?</\1>", "", text)
        text = re.sub(r"(?i)<br\s*/?>|</p>|</tr>|</div>", "\n", text)
        text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"[ \t]+", " ", text).strip()


def save_message(msg_id: str, raw: bytes, meta: dict) -> dict:
    folder = RAW_DIR / msg_id
    (folder / "attachments").mkdir(parents=True, exist_ok=True)
    (folder / "message.eml").write_bytes(raw)
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    body = _body_text(msg)
    (folder / "body.txt").write_text(body, encoding="utf-8")
    atts = []
    for i, part in enumerate(msg.iter_attachments(), 1):
        name = _safe(part.get_filename() or f"attachment-{i}")
        data = part.get_payload(decode=True) or b""
        path = folder / "attachments" / name
        path.write_bytes(data)
        info: dict[str, Any] = {"n": i, "name": name, "path": str(path),
                                "content_type": part.get_content_type(), "bytes": len(data),
                                "sha256": hashlib.sha256(data).hexdigest()}
        if part.get_content_type() == "application/pdf" or name.lower().endswith(".pdf"):
            try:
                info.update(pdftext.extract_to(path, path.with_suffix(".pages.json")))
            except Exception as e:
                info["pdf_error"] = str(e)
        atts.append(info)
    return {
        "gmail_id": msg_id, "thread_id": meta.get("threadId"), "from": str(msg.get("From", "")),
        "to": str(msg.get("To", "")), "subject": str(msg.get("Subject", "")), "date": str(msg.get("Date", "")),
        "message_id_header": str(msg.get("Message-ID", "")), "in_reply_to": str(msg.get("In-Reply-To", "")),
        "body_path": str(folder / "body.txt"), "body_excerpt": body[:1500],
        "attachments": atts, "label_ids": meta.get("labelIds", []),
    }


def pull(conn, g: Gmail, ctx: dict, label: str | None = None, newer_than: str = "30d") -> dict:
    label = label or os.environ.get("GMAIL_LABEL", "quotes")
    new = []
    for msg_id in g.list_ids(f'label:"{label}" newer_than:{newer_than}'):
        entity = f"mail:M-{msg_id}"
        if conn.execute("SELECT 1 FROM events WHERE entity = ? LIMIT 1", (entity,)).fetchone():
            continue
        raw, meta = g.raw(msg_id)
        payload = save_message(msg_id, raw, meta)
        db.append(conn, kind="mail.received", entity=entity, payload=payload,
                  sources=[f"gmail:{msg_id}"] + [f"gmail:{msg_id}#att{a['n']}" for a in payload["attachments"]],
                  **ctx)
        new.append({"entity": entity, "subject": payload["subject"], "from": payload["from"],
                    "attachments": [a["name"] for a in payload["attachments"]]})
    return {"label": label, "new": new}


def peek(g: Gmail, label: str | None = None, n: int = 10) -> list[dict]:
    """Read-only sample of recent mail for the architect. Writes nothing to the ledger."""
    label = label or os.environ.get("GMAIL_LABEL", "quotes")
    out = []
    for msg_id in g.list_ids(f'label:"{label}"')[:n]:
        data = g.request("GET", f"/messages/{msg_id}", params={"format": "metadata",
                                                               "metadataHeaders": ["From", "Subject", "Date"]})
        headers = {h["name"]: h["value"] for h in data.get("payload", {}).get("headers", [])}
        parts = data.get("payload", {}).get("parts", []) or []
        out.append({"id": msg_id, "from": headers.get("From"), "subject": headers.get("Subject"),
                    "date": headers.get("Date"), "snippet": data.get("snippet"),
                    "attachments": [p.get("filename") for p in parts if p.get("filename")]})
    return out


# ---------------------------------------------------------------- send (seed only)

def send(g: Gmail, to: str, subject: str, body: str, attachments: list[Path] | None = None,
         label: str | None = None, sender_name: str | None = None) -> dict:
    me = os.environ.get("GMAIL_ADDRESS", "")
    msg = EmailMessage()
    msg["To"] = me if to == "me" else to
    msg["From"] = f"{sender_name} <{me}>" if sender_name else me
    msg["Subject"] = subject
    msg.set_content(body)
    for path in attachments or []:
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        msg.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    sent = g.request("POST", "/messages/send", json={"raw": raw})
    label = label or os.environ.get("GMAIL_LABEL", "quotes")
    lid = g.label_id(label)
    if not lid:
        lid = g.request("POST", "/labels", json={"name": label})["id"]
    g.request("POST", f"/messages/{sent['id']}/modify", json={"addLabelIds": [lid, "INBOX"]})
    return {"id": sent["id"], "label": label}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="connectors.gmail", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("connect")
    c.add_argument("--scope", choices=["read", "send"], default="read")
    pl = sub.add_parser("pull")
    pl.add_argument("--actor", default=os.environ.get("LEDGER_ACTOR"))
    pl.add_argument("--role", default=os.environ.get("LEDGER_ROLE"))
    pl.add_argument("--run-id", default=os.environ.get("LEDGER_RUN_ID"))
    pl.add_argument("--workflow", default=os.environ.get("LEDGER_WORKFLOW"))
    pl.add_argument("--workflow-version", type=int)
    pl.add_argument("--label")
    pl.add_argument("--newer-than", default="30d")
    pk = sub.add_parser("peek")
    pk.add_argument("--label")
    pk.add_argument("-n", type=int, default=10)
    s = sub.add_parser("send")
    s.add_argument("--to", default="me")
    s.add_argument("--subject", required=True)
    s.add_argument("--body", required=True, help="text or @file")
    s.add_argument("--attach", action="append", default=[])
    s.add_argument("--sender-name")
    args = p.parse_args(argv)
    out = lambda o: sys.stdout.write(json.dumps(o, indent=2, default=str) + "\n")
    try:
        if args.cmd == "connect":
            out(connect(args.scope))
        elif args.cmd == "pull":
            if not args.actor or not args.role:
                raise db.LedgerError("--actor and --role are required")
            conn = db.connect(Path(args.db) if args.db else None)
            ctx = {"actor": args.actor, "role": args.role, "run_id": args.run_id, "workflow": args.workflow,
                   "workflow_version": args.workflow_version}
            out(pull(conn, Gmail("read"), ctx, args.label, args.newer_than))
        elif args.cmd == "peek":
            out(peek(Gmail("read"), args.label, args.n))
        elif args.cmd == "send":
            body = Path(args.body[1:]).read_text(encoding="utf-8") if args.body.startswith("@") else args.body
            out(send(Gmail("send"), args.to, args.subject, body, [Path(a) for a in args.attach],
                     sender_name=args.sender_name))
        return 0
    except (GmailError, db.LedgerError) as e:
        out({"error": str(e)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
