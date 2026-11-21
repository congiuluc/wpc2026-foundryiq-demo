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
    for path in ("guide/README.md", "docs/images/chat.png", "src/app.py"):
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.touch()

    text = "\n".join(
        [
            "[Guide](guide/#section)",
            "![Chat](docs/images/chat.png)",
            "[![Chat](docs/images/chat.png)](docs/images/chat.png)",
            "[Code](src/app.py)",
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
            "[Folder](https://example.test/repo/tree/main/src)",
            "[External](https://example.test)",
            "[Section](#section)",
        ]
    )

    assert build_docs.rewrite_links(text, "README.md", "https://example.test/repo", "main", {"guide/README.md"}) == expected
