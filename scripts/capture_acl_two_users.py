"""Record the ACL slide demo with two real test accounts: one in Tecnici, one not.

Usage:
  python scripts/capture_acl_two_users.py --base-url https://<chat-app>.azurewebsites.net \
      --included-user enrico@contoso.onmicrosoft.com --excluded-user claudia@contoso.onmicrosoft.com

A visible Chrome window opens for each account: sign in yourself (the script never reads or
stores passwords). Sign-in happens in a non-recorded context; the session cookie is copied in
memory to a recorded context. Nothing is mocked and groups or ACLs are never modified.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import imageio_ffmpeg
from playwright.sync_api import Browser, Page, sync_playwright

from capture_demo_media import ROOT, SIZE, ask, caption, close_dialog, collect_response, transcode

TECNICI_GROUP_ID = "fdc16a14-f1e3-46a3-9996-814fd35cbdbb"
QUESTION = "Qual è il codice della modalità service del Ven Systems Model 12?"
DOCUMENT = "codici-modalita-service.md"
VIDEO = "demo-acl-due-utenti.mp4"
TITLE = "Stessa domanda, due utenti"
# Shows display name and groups; account, object/tenant IDs and unknown groups stay hidden.
PRIVACY_CSS = """
.profile-summary > span, .profile-data .profile-field:nth-child(-n+4) dd {
  position:relative;color:transparent !important;text-shadow:none !important;
}
.profile-summary > span::after, .profile-data .profile-field:nth-child(-n+4) dd::after {
  content:"Valore oscurato";position:absolute;inset:0;color:#475569;background:#f1f5f9;overflow:hidden;
}
"""
GROUP_LABELS_JS = """(tecnici) => document.querySelectorAll('.profile-groups code').forEach(e => {
  e.textContent = e.textContent.trim() === tecnici ? 'WPC FoundryIQ ACL - Tecnici' : 'Altro gruppo (ID oscurato)';
})"""


def sign_in(browser: Browser, base_url: str, account: str, label: str) -> dict:
    """Wait for the user to sign in interactively, then return the session state in memory."""
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    try:
        page = context.new_page()
        page.goto(f"{base_url}/demo/acl")
        print(f"\n>>> Accedi come {label} ({account}) nella finestra di Chrome aperta.", flush=True)
        page.wait_for_url(f"{base_url}/**", timeout=600_000)
        page.wait_for_function("!document.querySelector('#send')?.disabled", timeout=600_000)
        config = page.evaluate("fetch('/api/config').then(r => r.json())")
        profile = config.get("user_profile") or {}
        signed_in = (profile.get("username") or config.get("user") or "").lower()
        if signed_in != account.lower():
            raise RuntimeError(f"{label}: è stato usato un account diverso da quello atteso. Ripeti l'accesso.")
        if not config.get("user_token_available"):
            raise RuntimeError(f"{label}: token Search non disponibile nella sessione; le ACL non sarebbero applicate.")
        return context.storage_state()
    finally:
        context.close()


def record(browser: Browser, base_url: str, state: dict, label: str, included: bool, staging: Path) -> dict:
    context = browser.new_context(
        viewport=SIZE, storage_state=state, record_video_dir=str(staging / f"raw-{label}"),
        record_video_size=SIZE, reduced_motion="reduce", device_scale_factor=1,
    )
    page = context.new_page()
    responses: list[dict] = []
    page.on("requestfinished", lambda request: collect_response(request.response(), responses))
    try:
        page.goto(f"{base_url}/demo/acl", wait_until="networkidle")
        page.wait_for_function("!document.querySelector('#send')?.disabled")
        page.add_style_tag(content=PRIVACY_CSS)
        expectation = "è nel gruppo Tecnici" if included else "non è nel gruppo Tecnici"
        caption(page, f"Utente reale: {label}, che {expectation}. Stessa app, stessa knowledge base ACL.", 5)
        page.locator(".user").click()
        page.locator(".profile-dialog[open]").wait_for()
        groups = page.evaluate("[...document.querySelectorAll('.profile-groups code')].map(e => e.textContent.trim())")
        page.evaluate(GROUP_LABELS_JS, TECNICI_GROUP_ID)
        caption(page, "Gruppi letti dai claim della sessione. Account e ID oscurati; nomi gruppo associati dallo script.", 6)
        close_dialog(page, ".profile-dialog")
        page.locator('label:has(input[name="mode"][value="acl"])').click()
        page.wait_for_function("document.querySelector('input[name=\"mode\"][value=\"acl\"]').checked")
        caption(page, f"Modalità Con ACL: il token di {label} viaggia verso AI Search. Documento: {DOCUMENT}, groupIds Tecnici.", 5)
        result = ask(page, QUESTION, responses)
        references = page.locator(".references-details summary")
        if references.count():
            references.first.click()
            references.first.scroll_into_view_if_needed()
        found = sum(r["references"] for r in result if r["acl_requested"])
        outcome = (f"{label}: documento incluso, {found} riferimento/i restituito/i dal servizio." if found
                   else f"{label}: nessun documento restituito con il suo token. Il servizio non espone il motivo.")
        caption(page, outcome, 7)
        page.get_by_role("button", name="Visualizza trace corrente", exact=True).click()
        page.locator(".trace-dialog[open]").wait_for()
        caption(page, "Trace della chat: query e conteggi osservati sullo stream. Nessun token mostrato.", 5)
        close_dialog(page, ".trace-dialog")
        video = page.video
    finally:
        context.close()
    if video is None:
        raise RuntimeError("Playwright non ha prodotto la registrazione.")
    if not all(r["acl_requested"] and r["user_token_forwarded"] for r in result):
        raise RuntimeError(f"{label}: la richiesta non ha inoltrato il token utente. Nessun asset pubblicato.")
    if included != (found > 0):
        raise RuntimeError(
            f"{label}: atteso {'almeno un' if included else 'nessun'} riferimento, ottenuti {found}. "
            "Verifica appartenenze e ingestion: nessun asset pubblicato."
        )
    return {
        "label": label, "in_tecnici_expected": included,
        "tecnici_group_in_claims": TECNICI_GROUP_ID in groups, "groups_in_claims": len(groups),
        "raw": Path(video.path()), "requests": responses,
    }


def concat(parts: list[Path], destination: Path) -> float:
    joined = destination.with_suffix(".webm")
    listing = destination.with_suffix(".txt")
    listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "concat", "-safe", "0", "-i", str(listing), "-c:v", "libvpx", "-b:v", "4M",
                    "-an", str(joined)], check=True)
    return transcode(joined, destination, TITLE)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default=os.getenv("CHAT_APP_URL", ""))
    parser.add_argument("--included-user", required=True, help="UPN di un membro del gruppo Tecnici")
    parser.add_argument("--excluded-user", required=True, help="UPN di un utente non membro di Tecnici")
    parser.add_argument("--included-label", default="Enrico")
    parser.add_argument("--excluded-label", default="Claudia")
    parser.add_argument("--browser-channel", default=os.getenv("DEMO_CAPTURE_BROWSER_CHANNEL", "chrome"))
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")
    if urlsplit(base_url).scheme != "https":
        parser.error("--base-url deve essere l'URL HTTPS dell'app pubblicata con Easy Auth.")
    users = [(args.included_user, args.included_label, True), (args.excluded_user, args.excluded_label, False)]
    with tempfile.TemporaryDirectory(prefix="wpc-acl-users-") as temporary, sync_playwright() as playwright:
        staging = Path(temporary)
        headed = playwright.chromium.launch(channel=args.browser_channel, headless=False)
        try:
            states = [sign_in(headed, base_url, account, label) for account, label, _ in users]
        finally:
            headed.close()
        browser = playwright.chromium.launch(channel=args.browser_channel, headless=True)
        try:
            runs = [record(browser, base_url, state, label, included, staging)
                    for state, (_, label, included) in zip(states, users)]
        finally:
            browser.close()
        destination = staging / VIDEO
        duration = concat([run.pop("raw") for run in runs], destination)
        videos = ROOT / "docs" / "videos"
        shutil.copy2(destination, videos / VIDEO)
        manifest_path = videos / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["demos"]["acl-due-utenti"] = {
            "title": TITLE, "video": VIDEO, "duration_seconds": duration,
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "identity": "Two real test accounts signed in interactively via Easy Auth; account and IDs redacted",
            "question": QUESTION, "document": DOCUMENT, "users": runs,
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Video verificato salvato in docs/videos/{VIDEO} ({duration}s).")


if __name__ == "__main__":
    main()
