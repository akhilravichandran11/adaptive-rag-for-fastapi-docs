"""Verify referenced-code discovery through the collector's public seams."""

import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from data_collection import collect_referenced_code as collector  # noqa: E402


@pytest.mark.parametrize(
    "include",
    [
        "{! ../../docs_src/example.py !}",
        "{!../../docs_src/example.py!}",
        "{!> ../../docs_src/example.py !}",
        "{!>../../docs_src/example.py!}",
    ],
)
def test_legacy_include_forms_resolve_without_arrow_in_path(include: str) -> None:
    text = f"Before\n{include}\nAfter"

    assert list(collector.iter_references(text)) == [("docs_src/example.py", "")]


@pytest.mark.parametrize("fail_javascript", [False, True])
def test_collection_writes_pinned_examples_and_records_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail_javascript: bool,
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    code = tmp_path / "code"
    docs_manifest = tmp_path / "manifest.json"
    code_manifest = tmp_path / "code_manifest.json"
    source = {
        "repo": "example/docs",
        "repo_url": "https://example.invalid/docs",
        "ref": "fixture-commit",
    }
    docs_manifest.write_text(
        json.dumps(
            {
                "source": source,
                "files": [
                    {
                        "output_filename": "example.md",
                        "source_path": "tutorial/example.md",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (docs / "example.md").write_text(
        "Before\n"
        "{!> ../../docs_src/one.py !}\n"
        "{!>../../docs_src/two.js!}\n"
        "{* ../../docs_src/one.py ln[1] *}\n"
        "After\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(collector, "DOCS_DIR", docs)
    monkeypatch.setattr(collector, "DOCS_MANIFEST", docs_manifest)
    monkeypatch.setattr(collector, "CODE_DIR", code)
    monkeypatch.setattr(collector, "CODE_MANIFEST", code_manifest)

    references = collector.collect_references()
    assert set(references) == {"docs_src/one.py", "docs_src/two.js"}
    assert sorted(references["docs_src/one.py"], key=lambda record: record["options"]) == [
        {"doc": "tutorial/example.md", "options": ""},
        {"doc": "tutorial/example.md", "options": "ln[1]"},
    ]
    assert references["docs_src/two.js"] == [{"doc": "tutorial/example.md", "options": ""}]

    base_url = "https://raw.githubusercontent.com/example/docs/fixture-commit/"
    responses = {
        base_url + "docs_src/one.py": b"x = 1\n",
        base_url + "docs_src/two.js": b"let x = 2;\n",
    }
    requested: list[str] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> io.BytesIO:
        url = request.full_url
        requested.append(url)
        if fail_javascript and url == base_url + "docs_src/two.js":
            raise urllib.error.URLError("fixture failure")
        return io.BytesIO(responses[url])

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    collector.main()

    assert set(requested) == set(responses)
    assert (code / "docs_src/one.py").read_bytes() == b"x = 1\n"
    manifest = json.loads(code_manifest.read_text(encoding="utf-8"))
    assert manifest["source"] == {
        "id": "github-docs",
        "version": "v1",
        **source,
    }
    records = {record["repo_path"]: record for record in manifest["files"]}
    expected_paths = {"docs_src/one.py"}
    if not fail_javascript:
        expected_paths.add("docs_src/two.js")
        assert (code / "docs_src/two.js").read_bytes() == b"let x = 2;\n"
    else:
        assert not (code / "docs_src/two.js").exists()
    assert set(records) == expected_paths

    for path, record in records.items():
        assert record["url"] == base_url + path
        assert record["output_path"] == "code/" + path
        assert record["bytes"] == len(responses[base_url + path])
        assert record["referenced_by"] == references[path]

    assert manifest["stats"] == {
        "total_files": 1 if fail_javascript else 2,
        "total_bytes": 6 if fail_javascript else 17,
        "failures": 1 if fail_javascript else 0,
        "by_ext": {".py": 1} if fail_javascript else {".py": 1, ".js": 1},
    }
    if fail_javascript:
        assert manifest["failures"] == [
            {
                "repo_path": "docs_src/two.js",
                "url": base_url + "docs_src/two.js",
                "error": "<urlopen error fixture failure>",
            }
        ]
    else:
        assert manifest["failures"] == []
