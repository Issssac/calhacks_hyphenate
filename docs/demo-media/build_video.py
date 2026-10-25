"""Build the silent demo video from the real captured output in docs/demo-output/*.txt.

    python docs/demo-media/build_video.py

Writes docs/demo-media/frames/*.png, docs/demo-media/clips/<segment>.mp4,
docs/demo-media/revenue-desk-demo.mp4 and docs/demo-cues.md. The program output is drawn as captured;
only the caption bar under it is added. Needs Pillow and imageio-ffmpeg.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "demo-output"
MEDIA = ROOT / "docs" / "demo-media"
FRAMES, CLIPS = MEDIA / "frames", MEDIA / "clips"
W, H = 1280, 720
CAPTION_H = 64
BG, FG, DIM, RED, AMBER, GREEN = "#0f1419", "#d6deeb", "#5c6773", "#ff6b6b", "#ffb454", "#9be38b"
MONO = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 21)
MONO_B = ImageFont.truetype("C:/Windows/Fonts/consolab.ttf", 21)
SANS = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 25)
SANS_SMALL = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 17)
SANS_TITLE = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 46)
SANS_MID = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 26)
LINE_H = 24
STAND_IN = "In this benchmark the controller is a stand-in agent answering from an answer key."


def lines(seg: str) -> list[str]:
    return (OUT / f"{seg}.txt").read_text(encoding="utf-8").splitlines()


def part(seg: str, a: int, b: int, header: bool = False) -> list[str]:
    """Lines a..b (1-based, inclusive) of a captured segment; header=True prepends its title block."""
    ls = lines(seg)
    body = ls[a - 1:b]
    return (ls[1:4] + [""] + body) if header else body


def caption_bar(d: ImageDraw.ImageDraw, text: str) -> None:
    d.rectangle([0, H - CAPTION_H, W, H], fill="#1c2430")
    d.text((32, H - CAPTION_H + 15), text, font=SANS, fill="#ffffff")
    tag = "real captured output, replayed"
    tw = d.textlength(tag, font=SANS_SMALL)
    d.text((W - tw - 20, H - 26), tag, font=SANS_SMALL, fill="#7f8b99")


def text_frame(path: Path, text_lines: list[str], caption: str) -> None:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    y = 10
    for ln in text_lines:
        colour, font = FG, MONO
        if set(ln.strip()) == {"="}:
            colour = DIM
        elif ln.strip().isupper() or re.match(r"^\s{2}[A-Z][A-Z -]+ {2}-", ln):
            colour, font = "#ffffff", MONO_B
        if "REFUSED" in ln:
            colour = RED
        elif "CONFLICT" in ln:
            colour = AMBER
        elif ln.strip().startswith("+"):
            colour = GREEN
        d.text((40, y), ln, font=font, fill=colour)
        y += LINE_H
    if y > H - CAPTION_H:
        raise SystemExit(f"{path.name}: {len(text_lines)} lines do not fit")
    caption_bar(d, caption)
    img.save(path)


def image_frame(path: Path, src: Path, crop: tuple[int, int, int, int], caption: str) -> None:
    img = Image.new("RGB", (W, H), "#f4f5f7")
    pic = Image.open(src).convert("RGB").crop(crop)
    scale = min((W - 40) / pic.width, (H - CAPTION_H - 16) / pic.height)
    pic = pic.resize((int(pic.width * scale), int(pic.height * scale)), Image.LANCZOS)
    img.paste(pic, ((W - pic.width) // 2, 8))
    d = ImageDraw.Draw(img)
    caption_bar(d, caption)
    img.save(path)


def title_frame(path: Path) -> None:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    rows = [
        (SANS_TITLE, "#ffffff", "Revenue Desk"),
        (SANS_MID, FG, "An autonomous quote-to-cash desk, built on WidgeX workflows"),
        (SANS_MID, FG, ""),
        (SANS_MID, AMBER, "A replay of the program's real captured output, not a live screen recording."),
        (SANS_MID, FG, "Real: Odoo 18 books, the Increase sandbox bank, agents reading emails and PDFs."),
        (SANS_MID, FG, "Simulated: the company, customers, suppliers, emails and payments."),
        (SANS_MID, FG, STAND_IN),
    ]
    y = 170
    for font, colour, text in rows:
        tw = d.textlength(text, font=font)
        d.text(((W - tw) / 2, y), text, font=font, fill=colour)
        y += 74 if font is SANS_TITLE else 44
    img.save(path)


def proof_caption() -> str:
    for ln in lines("proof"):
        m = re.search(r"Checks: (\d+) pass, (\d+) fail, (\d+) pending", ln)
        if m:
            p, f, pend = m.groups()
            tail = f", {pend} pending" if pend != "0" else ""
            return f"Scored against a planted answer key: {p} pass, {f} fail{tail}"
    return "Scored against a planted answer key"


def segments() -> list[dict]:
    return [
        {"name": "title", "frames": [("title", None, None, 7)],
         "cues": ["Title card: say what this is: an autonomous quote-to-cash desk on WidgeX.",
                  "Say it is a replay of real captured output from the running desk.",
                  "Say out loud: " + STAND_IN]},
        {"name": "moment", "frames": [
            ("text", part("moment", 1, 18), "The customer's email says $4.00 a shackle; the attached PDF says $4.40", 9),
            ("text", part("moment", 19, 30, header=True),
             "Intake records a conflict citing both sources; the controller picks the PDF", 10)],
         "cues": ["Frame 1: the same email has two prices: $4.00 in the body, $4.40 in the PDF.",
                  "Nobody told the desk to compare them.",
                  "Frame 2: intake recorded a CONFLICT, citing the email body and the PDF page, and recommended the PDF instead of guessing.",
                  "Say the controller here is a stand-in agent answering from an answer key."]},
        {"name": "what", "frames": [
            ("text", part("what", 1, 11), "Five agents, each driven by a WidgeX workflow, writing to one tamper-evident ledger", 7),
            ("image", MEDIA / "arch_raw.png",
             "Python checks, not the agent's word, decide when work moves on and what reaches the books", 11,
             (0, 0, 1160, 895))],
         "cues": ["Intake, orders, billing and cash take a quote to cash; reconcile checks all three.",
                  "Real Odoo books and a real sandbox bank API; the company and its customers are simulated.",
                  "Diagram: each agent is a WidgeX state machine; hard checks read the ledger before a step can finish.",
                  "Everything is appended to one hash-chained ledger; a posting gate sits in front of Odoo and the bank."]},
        {"name": "gate", "frames": [
            ("text", part("gate", 1, 16), "Posting without approval, or approving your own work: refused in code", 11),
            ("text", part("gate", 17, 38, header=True),
             "Every number traces back to the email, the PDF page and the Odoo record", 11)],
         "cues": ["Agents draft, but they can't commit.",
                  "Frame 1: confirming in Odoo without approval is refused before Odoo is even called; so is approving your own work.",
                  "Frame 2: the trace of one live order, from the email to the invoice to the payment, each row with its source."]},
        {"name": "learn", "frames": [
            ("text", part("learn", 1, 16),
             "Overruled on contract pricing, the orders agent diagnoses its own workflow", 11),
            ("text", part("learn", 17, 29, header=True),
             "Revision adopted (orders v10 -> v11); the next Bay Rigging quote is priced right first time", 11)],
         "cues": ["The controller rejected a sales order: Bay Rigging has contract prices.",
                  "The orders agent recorded the feedback, found the gap in its own workflow, and proposed a revision.",
                  "Frame 2: the revision was reviewed and adopted; the old version is archived. The green lines are the change.",
                  "S08: the same customer at list price again; this time the contract price was applied on the first draft."]},
        {"name": "proof", "frames": [("text", part("proof", 1, 21), proof_caption(), 12)],
         "cues": ["Ten scenarios, each with a planted problem and an answer key; this table scores the run.",
                  "Point at the tally line and that the ledger hash chain verifies.",
                  "Read the last two lines: what is real and what is simulated, including the stand-in controller."]},
        {"name": "phish", "frames": [("text", part("phish", 1, 15),
                                      "An email tried to redirect a vendor payment: flagged, and the bank connector refuses", 8)],
         "cues": ["A customer email told 'the AI assistant' to skip review and pay a vendor at a new account.",
                  "Intake flagged it and didn't follow it.",
                  "Even if an agent tried, the bank connector pays only to the account on file in Odoo."]},
        {"name": "close", "frames": [("text", part("close", 1, 7),
                                      "It fills the revenue desk's seats; the controller keeps only the decisions", 8)],
         "cues": ["Order entry, billing, vendor bills with 3-way match, cash application, vendor payments, reconciliation.",
                  "The controller keeps only the decisions."]},
    ]


def ffmpeg(*args: str) -> None:
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def build_clip(name: str, frames: list[tuple[Path, float]]) -> Path:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        for p, secs in frames:
            f.write(f"file '{p.as_posix()}'\nduration {secs}\n")
        f.write(f"file '{frames[-1][0].as_posix()}'\n")
        listing = f.name
    out = CLIPS / f"{name}.mp4"
    ffmpeg("-f", "concat", "-safe", "0", "-i", listing, "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
           "-shortest", "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-tune", "stillimage",
           "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", str(out))
    Path(listing).unlink()
    return out


def main() -> None:
    FRAMES.mkdir(parents=True, exist_ok=True)
    CLIPS.mkdir(parents=True, exist_ok=True)
    segs = segments()
    clips, cues, t = [], [], 0.0
    for s in segs:
        made = []
        for i, fr in enumerate(s["frames"], 1):
            kind, src, caption, secs = fr[:4]
            p = FRAMES / f"{len(clips) + 1:02d}-{s['name']}-{i}.png"
            if kind == "title":
                title_frame(p)
            elif kind == "image":
                image_frame(p, src, fr[4], caption)
            else:
                text_frame(p, src, caption)
            made.append((p, float(secs)))
        clips.append(build_clip(s["name"], made))
        dur = sum(x for _, x in made)
        cues.append((s, t, dur))
        t += dur
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        for c in clips:
            f.write(f"file '{c.as_posix()}'\n")
        listing = f.name
    final = MEDIA / "revenue-desk-demo.mp4"
    ffmpeg("-f", "concat", "-safe", "0", "-i", listing, "-c", "copy", "-movflags", "+faststart", str(final))
    Path(listing).unlink()
    write_cues(cues, t)
    print(f"{final}  {t:.0f}s, {len(clips)} clips")


def write_cues(cues: list, total: float) -> None:
    def ts(x: float) -> str:
        return f"{int(x // 60)}:{int(x % 60):02d}"

    out = ["# Demo video cue sheet", "",
           f"Video: `docs/demo-media/revenue-desk-demo.mp4`: silent, 1280x720, {ts(total)} long. Each segment is also a "
           "separate clip in `docs/demo-media/clips/`. Every frame shows the real captured output of "
           "`python -m desk.demo <segment>` (the text in `docs/demo-output/`), with one caption line added.", "",
           f"**Say this out loud at some point:** {STAND_IN}", "",
           "Don't say \"secure\": identities in the ledger are asserted, not authenticated.", "",
           "| Segment | Starts | Length | Clip |", "|---|---|---|---|"]
    for s, start, dur in cues:
        out.append(f"| {s['name']} | {ts(start)} | {dur:.0f}s | `clips/{s['name']}.mp4` |")
    for s, start, dur in cues:
        out += ["", f"## {s['name']} ({ts(start)}, {dur:.0f}s)", ""]
        out += [f"- {c}" for c in s["cues"]]
    (ROOT / "docs" / "demo-cues.md").write_text("\n".join(out) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
