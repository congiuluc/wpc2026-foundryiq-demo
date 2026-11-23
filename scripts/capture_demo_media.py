"""Capture the current UI with live service responses, screenshots and offline MP4 videos.

Usage: python scripts/capture_demo_media.py --base-url http://127.0.0.1:8000
Prerequisites: install scripts/media-requirements.txt, Chrome, and Playwright FFmpeg
(python -m playwright install ffmpeg). Start the built webapp-chat with an authenticated
Azure CLI session first. Queries can incur Azure costs; no API responses are mocked.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import imageio_ffmpeg
from playwright.sync_api import Browser, Page, Response, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SIZE = {"width": 1920, "height": 1080}
CAPTION_STYLE = (
    "position:fixed;bottom:18px;left:5%;right:5%;z-index:2147483647;"
    "padding:17px 24px;border-radius:14px;background:#172554;color:white;"
    "font:600 23px/1.45 'Segoe UI',sans-serif;text-align:center;"
    "box-shadow:0 5px 20px #0003;pointer-events:none;"
)
PRIVACY_CSS = """
.profile-summary > strong, .profile-summary > span, .avatar-lg,
.profile-data .profile-field:nth-child(-n+4) dd, .profile-groups code {
  position:relative;color:transparent !important;text-shadow:none !important;
}
.profile-summary > strong::after, .profile-summary > span::after,
.profile-data .profile-field:nth-child(-n+4) dd::after, .profile-groups code::after {
  content:"Valore oscurato";position:absolute;inset:0;color:#475569;
  background:#f1f5f9;overflow:hidden;
}
"""
DEMOS = {
    "multi-fonte": {
        "title": "Una domanda, più fonti",
        "question": (
            "Devo cambiare le membrane filtranti della Graphene Vapor Reclaim Tank: "
            "ogni quanto va fatto, quante GRT-002 abbiamo a magazzino e come vanno smaltite quelle esauste?"
        ),
        "image": "chat-fonti.png", "architecture": "overview.png",
    },
    "acl": {
        "title": "Permessi per documento",
        "question": "Qual è il codice della procedura di richiesta ricambi e lo SLA per la priorità P1?",
        "image": "chat-acl.png", "architecture": "acl-demo.png",
    },
    "agenti": {
        "title": "RAG classico vs Foundry IQ",
        "question": (
            "Per la Graphene Vapor Reclaim Tank, ogni quanto si sostituiscono le membrane "
            "filtranti e quante GRT-002 sono disponibili a magazzino? Cita manuale e giacenze."
        ),
        "image": "chat-agenti.png", "architecture": "rag-vs-foundryiq.png",
    },
}


def caption(page: Page, text: str, seconds: float = 4) -> None:
    page.evaluate(
        """({text, style}) => {
          document.querySelectorAll('[data-demo-caption]').forEach(e => e.remove());
          const e = document.createElement('div');
          e.dataset.demoCaption = 'true'; e.textContent = text; e.style.cssText = style;
          (document.querySelector('dialog[open]') || document.body).append(e);
        }""",
        {"text": text, "style": CAPTION_STYLE},
    )
    page.wait_for_timeout(seconds * 1000)


def screenshot(page: Page, destination: Path, *, selector: str | None = None) -> None:
    page.evaluate("document.querySelectorAll('[data-demo-caption]').forEach(e => e.remove())")
    page.mouse.move(5, 80)
    page.wait_for_timeout(250)
    if selector:
        page.locator(selector).screenshot(path=str(destination))
    else:
        page.screenshot(path=str(destination))


def close_dialog(page: Page, name: str) -> None:
    page.locator(f"{name} .dialog-actions button").get_by_text("Chiudi", exact=True).click()
    page.locator(f"{name}[open]").wait_for(state="hidden")


def ask(page: Page, question: str, responses: list[dict]) -> list[dict]:
    previous = len(responses)
    agents = page.locator("#agents-picker input:checked").count()
    compare = page.locator('input[name="mode"][value="compare"]:checked').count()
    expected = agents or (2 if compare else 1)
    page.locator("#question").fill(question)
    caption(page, "Invio della domanda ai servizi Azure. Risposta ed eventi arrivano in streaming in tempo reale.", 2)
    page.locator("#send").click()
    page.evaluate("document.querySelectorAll('[data-demo-caption]').forEach(e => e.remove())")
    page.wait_for_function(
        "!document.querySelector('#send').disabled && document.querySelectorAll('.answer').length > 0",
        timeout=240_000,
    )
    # Browser rendering can finish before Playwright has dispatched all request-finished callbacks.
    deadline = time.monotonic() + 10
    while len(responses) - previous < expected and time.monotonic() < deadline:
        page.wait_for_timeout(50)
    result = responses[previous:]
    if len(result) != expected or any(not item["ok"] for item in result):
        statuses = ", ".join(str(item["http_status"]) for item in result if not item["ok"]) or "nessuna risposta"
        raise RuntimeError(
            f"Una richiesta live non è riuscita (HTTP {statuses}): nessun asset viene pubblicato. "
            "Controlla la chat e i log locali."
        )
    page.locator("#log > *").first.scroll_into_view_if_needed()
    return result


def collect_response(response: Response | None, results: list[dict]) -> None:
    if response is None:
        return
    route = urlsplit(response.url).path
    if route not in ("/api/chat", "/api/agent-chat"):
        return
    if not response.ok:
        results.append({"ok": False, "http_status": response.status})
        return
    if "text/event-stream" in response.headers.get("content-type", ""):
        data = None
        event = ""
        for line in response.text().splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:") and event == "error":
                results.append({"ok": False, "http_status": response.status})
                return
            elif line.startswith("data:") and event == "completed":
                data = json.loads(line[5:])
        if data is None:
            raise RuntimeError("Registrazione incompleta: stream senza evento finale.")
    else:
        data = response.json()
    results.append({
        "ok": True, "http_status": response.status,
        "references": len(data.get("references") or []),
        "citations": len(data.get("citations") or []),
        "elapsed_ms": data.get("elapsed_ms"),
        "sources": [
            {"name": s["name"], "type": s.get("type"), "results": s.get("results"), "error": bool(s.get("error"))}
            for s in data.get("sources") or []
        ],
        "tool_calls": len(data.get("tool_calls") or []),
        "usage": data.get("usage") or {},
        "agent": data.get("agent"),
        "acl_requested": bool((data.get("execution") or {}).get("acl_requested")),
        "user_token_forwarded": bool((data.get("execution") or {}).get("user_token_forwarded")),
    })


def transcode(source: Path, destination: Path, title: str) -> float:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
        "-vf", "fps=24,format=yuv420p", "-c:v", "libx264", "-preset", "fast",
        "-crf", "24", "-movflags", "+faststart", "-an",
        "-metadata", f"title={title}",
        "-metadata", "comment=Live Azure queries; synthetic demo corpus; personal profile redacted",
        str(destination),
    ], check=True)
    validation = subprocess.run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(destination),
        "-progress", "pipe:1", "-f", "null", "-",
    ], capture_output=True, text=True, check=True)
    durations = re.findall(r"out_time_us=(\d+)", validation.stdout)
    duration = max(map(int, durations), default=0) / 1_000_000
    if duration < 5 or destination.stat().st_size < 10_000:
        raise RuntimeError("Video vuoto o troppo breve: nessun asset viene pubblicato.")
    return round(duration, 2)


def capture(browser: Browser, base_url: str, name: str, staging: Path) -> dict:
    demo = DEMOS[name]
    images, videos = staging / "images", staging / "videos"
    context = browser.new_context(
        viewport=SIZE, record_video_dir=str(staging / f"raw-{name}"), record_video_size=SIZE,
        reduced_motion="reduce", device_scale_factor=1,
    )
    page = context.new_page()
    responses: list[dict] = []
    page.on("requestfinished", lambda request: collect_response(request.response(), responses))
    try:
        page.goto(f"{base_url}/demo/{name}", wait_until="networkidle")
        page.wait_for_function("!document.querySelector('#send')?.disabled")
        page.add_style_tag(content=PRIVACY_CSS)
        page.locator(".user").evaluate(
            "e => { e.title = 'Profilo oscurato nella registrazione'; e.setAttribute('aria-label', e.title); }"
        )
        caption(page, f"WPC 2026 | {demo['title']} | Servizi Azure reali, dati dimostrativi sintetici.", 5)
        if name == "multi-fonte":
            screenshot(page, images / "chat-home.png")
            page.locator(".user").click()
            page.locator(".profile-dialog[open]").wait_for()
            caption(page, "La sessione usa Azure CLI. Token e appartenenze sono osservati; dati personali e ID sono oscurati.", 5)
            screenshot(page, images / "chat-profilo.png")
            close_dialog(page, ".profile-dialog")
        page.get_by_role("button", name="Architettura e ACL", exact=True).click()
        page.locator(".infographic-dialog[open]").wait_for()
        caption(page, "Architettura prevista: distingue identità, sorgenti e autorizzazioni. Non è una trace di esecuzione.", 5)
        screenshot(page, images / demo["architecture"], selector=".infographic-frame svg")
        close_dialog(page, ".infographic-dialog")
        page.get_by_role("button", name="Apri query di test", exact=True).click()
        caption(page, "Le query guidate mostrano fonti e permessi attesi; i risultati effettivi vengono dal servizio.", 4)
        page.get_by_role("button", name="Comprimi sidebar query di test", exact=True).click()
        query_results = ask(page, demo["question"], responses)
        if name == "multi-fonte" and not any(r["references"] or r["citations"] for r in query_results):
            raise RuntimeError("La query live non ha restituito riferimenti: correggi l'ambiente prima di registrare.")
        if name == "acl" and not any(r["acl_requested"] and r["references"] > 0 for r in query_results):
            raise RuntimeError("La query ACL non ha restituito documenti autorizzati: controlla gruppi, token e ingestion.")
        caption(page, "Risposte live: leggi citazioni, fonti e metriche. Una fonte vuota non prova un diniego di accesso.", 7)
        screenshot(page, images / demo["image"])
        if name == "agenti":
            page.locator(".agent-metrics").first.scroll_into_view_if_needed()
            caption(page, "Tre agenti, stessa domanda. Il confronto mostra tool invocati, tempi e token, non un benchmark ripetibile.", 7)
            screenshot(page, images / "chat-agenti-metriche.png")
        timeline = page.locator("details.timeline summary")
        if timeline.count():
            timeline.first.click()
            timeline.first.scroll_into_view_if_needed()
            caption(page, "Eventi in tempo reale dallo streaming: pianificazione, query, tool, riferimenti e primo token.", 7)
            if name in ("multi-fonte", "agenti"):
                screenshot(page, images / f"chat-timeline{'-agenti' if name == 'agenti' else ''}.png")
        references = page.locator(".references-details summary")
        if references.count():
            references.first.click()
            references.first.scroll_into_view_if_needed()
            caption(page, "I riferimenti consentono di risalire ai contenuti recuperati. I riferimenti tool non sono tutti citazioni.", 5)
        if name == "multi-fonte":
            screenshot(page, images / "chat-fonti-dettaglio.png")
            page.locator(".infographic-open").first.click()
            page.locator(".infographic-dialog[open]").wait_for()
            caption(page, "Flusso osservato: source e conteggi restituiti dalla query, senza inventare attività mancanti.", 6)
            screenshot(page, images / "chat-infografica.png")
            close_dialog(page, ".infographic-dialog")
        if name == "acl":
            page.locator("#reset").click()
            caption(page, "Seconda domanda: contenuto riservato alla Direzione. L'assenza da sola non dimostra il motivo del filtro.", 4)
            ask(page, "Qual è lo sconto concordato con Delta Systems negli accordi quadro 2027?", responses)
            caption(page, "Confronta i documenti effettivamente restituiti. Non modifichiamo utenti, gruppi o ACL durante la registrazione.", 6)
            screenshot(page, images / "chat-acl-riservato.png")
        page.get_by_role("button", name="Visualizza trace corrente", exact=True).click()
        page.locator(".trace-dialog[open]").wait_for()
        caption(page, "La Trace corrente riporta gli eventi della chat. Non contiene token né la trace interna completa di Foundry.", 6)
        if name == "multi-fonte":
            screenshot(page, images / "trace-esempio.png")
        metadata = page.locator(".trace-dialog .trace-metadata").first
        if metadata.count():
            metadata.scroll_into_view_if_needed()
            caption(page, "Tappe, fasi, query Foundry IQ e argomenti dei tool con durate misurate sullo stream. Credenziali oscurate.", 7)
            if name in ("multi-fonte", "agenti"):
                screenshot(page, images / f"trace-metadati{'-agenti' if name == 'agenti' else ''}.png")
        tool = page.locator(".trace-dialog .tool-call").first
        if tool.count():
            tool.scroll_into_view_if_needed()
            caption(page, "Ogni chiamata tool mostra argomenti e durata osservata.", 5)
        close_dialog(page, ".trace-dialog")
        caption(page, "Registrazione offline di una sessione reale. Verifica fonti e permessi prima di ripetere la demo live.", 5)
        video = page.video
    finally:
        context.close()
    if video is None:
        raise RuntimeError("Playwright non ha prodotto la registrazione.")
    destination = videos / f"demo-{name}.mp4"
    duration = transcode(Path(video.path()), destination, str(demo["title"]))
    print(f"{name}: video {duration}s, {len(responses)} risposte live verificate.", flush=True)
    return {"title": demo["title"], "video": destination.name, "duration_seconds": duration, "requests": responses}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("DEMO_CAPTURE_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--browser-channel", default=os.getenv("DEMO_CAPTURE_BROWSER_CHANNEL", "chrome"))
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")
    if urlsplit(base_url).scheme not in ("http", "https"):
        parser.error("--base-url deve essere un URL HTTP(S) dell'app già avviata.")
    with tempfile.TemporaryDirectory(prefix="wpc-demo-capture-") as temporary, sync_playwright() as playwright:
        staging = Path(temporary)
        for folder in ("images", "videos"):
            (staging / folder).mkdir()
        browser = playwright.chromium.launch(channel=args.browser_channel, headless=True)
        try:
            results = {name: capture(browser, base_url, name, staging) for name in DEMOS}
        finally:
            browser.close()
        manifest = {
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "resolution": SIZE, "format": "MP4 / H.264 / 24 fps", "audio": False,
            "provenance": "Current application, live Azure service responses, synthetic repository demo corpus",
            "identity": "Azure CLI for local capture; personal profile values redacted",
            "editing": "Italian on-screen captions; no fabricated responses or request-time adjustments",
            "demos": results,
        }
        previous = ROOT / "docs" / "videos" / "manifest.json"
        if previous.exists():
            # Keep entries recorded by other scripts, such as capture_acl_two_users.py.
            extra = json.loads(previous.read_text(encoding="utf-8")).get("demos", {})
            manifest["demos"] = {**{k: v for k, v in extra.items() if k not in DEMOS}, **results}
        (staging / "videos" / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        for folder in ("images", "videos"):
            destination = ROOT / "docs" / folder
            destination.mkdir(parents=True, exist_ok=True)
            for asset in (staging / folder).iterdir():
                shutil.copy2(asset, destination / asset.name)
    print("Screenshot e video verificati salvati in docs/images e docs/videos.")


if __name__ == "__main__":
    main()
