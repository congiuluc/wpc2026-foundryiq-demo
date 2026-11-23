"""Prepare the MkDocs source folder for the GitHub Pages site.

Copies the repository README files, the demo/SharePoint guides and media assets into
.site-src/ keeping their relative paths, so links between pages keep working.
Links to code, notebooks or folders without a README are rewritten to GitHub URLs.

Usage: python scripts/build_docs.py [--repo-url URL] [--branch BRANCH]
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / ".site-src"

PAGES = [
    "README.md",
    "infra/README.md",
    "mcp-ricambi/README.md",
    "agents/README.md",
    "webapp-chat/README.md",
    "notebooks/README.md",
    "http/README.md",
    "data/sharepoint/foundry-iq-sharepoint-remote-knowledge-source.md",
    "docs/demo-guide.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
]
ASSET_DIRS = ["docs/images", "docs/videos"]
SITE_EXTRAS = ["docs/site"]  # stylesheets and overrides used only by the site

LINK = re.compile(r"(!?\[(?:[^\[\]]|!\[[^\]]*\]\([^)]*\))*\])\(([^)\s]+)(\s+\"[^\"]*\")?\)")
MEDIA_TAG = re.compile(r"<(?:video|source|track)\b[^>]*>", re.I)
MEDIA_ATTRIBUTE = re.compile(r"""(\b(?:src|poster)\s*=\s*)(["'])(.*?)\2""", re.I)


def rewrite_links(text: str, page: str, repo_url: str, branch: str, published: set[str]) -> str:
    page_dir = Path(page).parent

    def repl(match: re.Match[str]) -> str:
        label = LINK.sub(repl, match.group(1))
        target, title = match.group(2), match.group(3) or ""
        unchanged = f"{label}({target}{title})"
        if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith("#"):
            return unchanged
        path, _, anchor = target.partition("#")
        resolved = os.path.normpath(page_dir / path).replace("\\", "/")
        if resolved.startswith(".."):
            return unchanged
        source_path = ROOT / resolved
        if not source_path.exists():
            raise FileNotFoundError(f"{page}: local link target not found: {target}")
        anchor = f"#{anchor}" if anchor else ""
        if resolved in published or (
            source_path.is_file() and any(resolved.startswith(f"{folder}/") for folder in ASSET_DIRS)
        ):
            return unchanged
        readme = f"{resolved}/README.md" if resolved != "." else "README.md"
        if readme in published:
            rel = os.path.relpath(readme, page_dir).replace("\\", "/")
            return f"{label}({rel}{anchor}{title})"
        kind = "tree" if source_path.is_dir() else "blob"
        return f"{label}({repo_url}/{kind}/{branch}/{resolved}{anchor}{title})"

    text = LINK.sub(repl, text)
    # Raw HTML video URLs are not rewritten by Markdown like ordinary image/link URLs.
    served_dir = page_dir if Path(page).stem in ("README", "index") else Path(page).with_suffix("")

    def media_tag(match: re.Match[str]) -> str:
        def attribute(attr: re.Match[str]) -> str:
            target = attr.group(3)
            if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith("/"):
                return attr.group(0)
            resolved = os.path.normpath(page_dir / target).replace("\\", "/")
            if not (ROOT / resolved).is_file():
                raise FileNotFoundError(f"{page}: local media target not found: {target}")
            if not any(resolved.startswith(f"{folder}/") for folder in ASSET_DIRS):
                raise ValueError(f"{page}: media target must be in a published asset directory: {target}")
            relative = os.path.relpath(resolved, served_dir).replace("\\", "/")
            return f"{attr.group(1)}{attr.group(2)}{relative}{attr.group(2)}"

        return MEDIA_ATTRIBUTE.sub(attribute, match.group(0))

    return MEDIA_TAG.sub(media_tag, text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-url", default=os.getenv("DOCS_REPO_URL", "https://github.com/congiuluc/wpc2026-foundryiq-demo"))
    parser.add_argument("--branch", default=os.getenv("DOCS_BRANCH", "main"))
    args = parser.parse_args()

    if OUT.exists():
        shutil.rmtree(OUT)
    published = set(PAGES)
    for page in PAGES:
        dest = OUT / page
        dest.parent.mkdir(parents=True, exist_ok=True)
        text = (ROOT / page).read_text(encoding="utf-8")
        dest.write_text(rewrite_links(text, page, args.repo_url.rstrip("/"), args.branch, published), encoding="utf-8")
    for folder in ASSET_DIRS:
        shutil.copytree(ROOT / folder, OUT / folder)
    for folder in SITE_EXTRAS:
        if (ROOT / folder).exists():
            shutil.copytree(ROOT / folder, OUT / folder, dirs_exist_ok=True)
    print(f"Docs source ready in {OUT.relative_to(ROOT)} ({len(PAGES)} pages)")


if __name__ == "__main__":
    main()
