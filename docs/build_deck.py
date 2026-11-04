"""Build the SafeCell submission deck: one full-bleed rendered slide per page.

Source of truth is docs/deck.html, rendered to docs/deck/slide-NN.png. The deck
is image-based so it looks identical everywhere (no font substitution), with
speaker notes attached for the presenting human.
"""

from pathlib import Path

from pptx import Presentation
from pptx.util import Inches

ROOT = Path(__file__).resolve().parents[1]
SLIDES = sorted((ROOT / "docs" / "deck").glob("slide-*.png"))
OUT = ROOT / "docs" / "safecell-deck.pptx"

NOTES = [
    "SafeCell — real work, zero blast radius. Live at 192-248-147-190.sslip.io, 84s demo video, 25 containment tests.",
    "The problem: an agent with a shell is a liability. One agent scanned 286 community skills and found a credential stealer disguised as a weather skill. Most products answer this with 'trust us'.",
    "Three steps: the model writes code (never runs it), the code runs in a disposable cell, and the run returns a receipt you can re-verify. One executor, no bypass, no unsafe mode.",
    "Control layer on Vultr, planner via Serverless Inference, cells via bubblewrap, verifier hashes the event log. The app binds a high port; Caddy owns 80/443 for TLS.",
    "Every containment claim has a test that executes real code inside a real cell. Note the last line: kernel exploits are explicitly NOT denied, and the UI says so.",
    "Real output from the live box: /etc/shadow permission denied, /home does not exist, egress and DNS blocked, host write refused. Receipt chain verified.",
    "The permission manifest is generated from the same policy object the sandbox enforces, so the declaration cannot drift from enforcement. Its hash rides in the receipt.",
    "Demo URL and repo. Everything is reproducible: clone, pip install, run the test suite.",
    "Honest limits: shared kernel (microVM is the upgrade path), single-node run store, planner quality belongs to the model. Containment you can check beats containment you trust.",
]


def main() -> None:
    if not SLIDES:
        raise SystemExit("no slide PNGs found in docs/deck/")

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]

    for i, png in enumerate(SLIDES):
        slide = prs.slides.add_slide(blank)
        slide.shapes.add_picture(str(png), 0, 0, width=prs.slide_width, height=prs.slide_height)
        if i < len(NOTES):
            slide.notes_slide.notes_text_frame.text = NOTES[i]

    prs.save(OUT)
    print(f"wrote {OUT} ({OUT.stat().st_size/1e6:.2f} MB, {len(SLIDES)} slides)")


if __name__ == "__main__":
    main()
