from pathlib import Path

import pytest

from scripts import build_docs


@pytest.mark.parametrize(
    "link",
    [
        "![Screenshot](docs/images/missing.png)",
        "[Guide](missing.md#section)",
        "[![Screenshot](docs/images/missing.png)](https://example.test)",
    ],
)
def test_missing_local_link_fails_before_rewriting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, link: str):
    monkeypatch.setattr(build_docs, "ROOT", tmp_path)

    with pytest.raises(FileNotFoundError, match="README.md: local link target not found"):
        build_docs.rewrite_links(link, "README.md", "https://example.test/repo", "main", {"missing.md"})


def test_existing_links_keep_their_expected_destinations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(build_docs, "ROOT", tmp_path)
    for path in ("guide/README.md", "docs/images/chat.png", "docs/videos/demo.mp4", "src/app.py"):
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.touch()

    text = "\n".join(
        [
            "[Guide](guide/#section)",
            "![Chat](docs/images/chat.png)",
            "[![Chat](docs/images/chat.png)](docs/images/chat.png)",
            "[Code](src/app.py)",
            "[Video](docs/videos/demo.mp4)",
            "[Video folder](docs/videos/)",
            "[Folder](src/)",
            "[External](https://example.test)",
            "[Section](#section)",
        ]
    )
    expected = "\n".join(
        [
            "[Guide](guide/README.md#section)",
            "![Chat](docs/images/chat.png)",
            "[![Chat](docs/images/chat.png)](docs/images/chat.png)",
            "[Code](https://example.test/repo/blob/main/src/app.py)",
            "[Video](docs/videos/demo.mp4)",
            "[Video folder](https://example.test/repo/tree/main/docs/videos)",
            "[Folder](https://example.test/repo/tree/main/src)",
            "[External](https://example.test)",
            "[Section](#section)",
        ]
    )

    assert build_docs.rewrite_links(text, "README.md", "https://example.test/repo", "main", {"guide/README.md"}) == expected


@pytest.mark.parametrize("page, target, expected", [
    ("docs/demo-guide.md", "videos/demo.mp4", "../videos/demo.mp4"),
    ("webapp-chat/README.md", "../docs/videos/demo.mp4", "../docs/videos/demo.mp4"),
    ("README.md", "docs/videos/demo.mp4", "docs/videos/demo.mp4"),
])
def test_video_html_uses_served_page_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                         page: str, target: str, expected: str):
    monkeypatch.setattr(build_docs, "ROOT", tmp_path)
    video = tmp_path / "docs" / "videos" / "demo.mp4"
    video.parent.mkdir(parents=True)
    video.touch()
    text = f'<video controls><source src="{target}" type="video/mp4"></video>'
    result = build_docs.rewrite_links(text, page, "https://example.test/repo", "main", {page})
    assert result == f'<video controls><source src="{expected}" type="video/mp4"></video>'


def test_missing_video_or_poster_fails_before_site_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(build_docs, "ROOT", tmp_path)
    with pytest.raises(FileNotFoundError, match="local media target not found"):
        build_docs.rewrite_links('<video poster="images/missing.png">', "docs/demo-guide.md",
                                 "https://example.test/repo", "main", set())
