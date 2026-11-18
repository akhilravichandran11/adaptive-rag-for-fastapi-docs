"""Verify GitHub-docs normalization through its agreed public seams."""

import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
NORMALIZER = PROJECT_ROOT / "data_collection" / "data_normalization" / "normalize_github_docs.py"

sys.path.insert(0, str(PROJECT_ROOT))

from data_collection.data_normalization import normalize_github_docs as normalizer  # noqa: E402


def test_paths_resolve_from_project_root_outside_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    module = runpy.run_path(str(NORMALIZER))
    expected_root = PROJECT_ROOT / "data" / "fastapi"

    assert module["DATA_ROOT"] == expected_root
    assert module["RAW_DOCS"] == expected_root / "raw/github-docs/v1/docs"
    assert module["RAW_CODE"] == expected_root / "raw/github-docs/v1/code"
    assert module["NORM_DOCS"] == expected_root / "normalized/github-docs/v1/docs"
    assert module["DOCS_MANIFEST"] == expected_root / "raw/github-docs/v1/manifest.json"
    assert module["NORM_MANIFEST"] == (expected_root / "normalized/github-docs/v1/manifest.json")


def test_author_fenced_include_preserves_language_and_surrounding_headings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code_dir = tmp_path / "docs_src"
    code_dir.mkdir()
    (code_dir / "example.html").write_text("<p>{{ name }}</p>\n", encoding="utf-8")
    monkeypatch.setattr(normalizer, "RAW_CODE", tmp_path)
    includes: list[dict] = []

    result = normalizer.normalize_text(
        '# Before\n\n```jinja hl_lines="1"\n{!../../docs_src/example.html!}\n```\n\n## After\n',
        includes,
    )

    assert result == (
        "# Before\n\n```jinja\n<!-- docs_src/example.html -->\n<p>{{ name }}</p>\n```\n\n## After\n"
    )
    assert includes == [{"repo_path": "docs_src/example.html", "options": "", "resolved": True}]


@pytest.mark.parametrize(
    ("include", "options", "expected_code"),
    [
        ("{!../../sample.py!}", "", "first\nsecond"),
        ("{!> ../../sample.py !}", "", "first\nsecond"),
        ("{!>../../sample.py!}", "", "first\nsecond"),
        ("{* ../../sample.py ln[2] hl[2] *}", "ln[2] hl[2]", "second"),
    ],
)
@pytest.mark.parametrize(
    ("fence", "indent"),
    [("", ""), ("```", ""), ("~~~", ""), ("````", ""), ("```", "  ")],
)
def test_supported_includes_preserve_code_selection_and_fence_language(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    include: str,
    options: str,
    expected_code: str,
    fence: str,
    indent: str,
) -> None:
    (tmp_path / "sample.py").write_text("first\nsecond\n", encoding="utf-8")
    monkeypatch.setattr(normalizer, "RAW_CODE", tmp_path)
    includes: list[dict] = []

    if fence:
        text = f"{indent}{fence}Python\n{indent}{include}\n{indent}{fence}\n"
        expected = (
            "\n".join(
                indent + line
                for line in [
                    f"{fence}Python",
                    "# sample.py",
                    *expected_code.splitlines(),
                    fence,
                ]
            )
            + "\n"
        )
    else:
        text = include + "\n"
        expected = f"```python\n# sample.py\n{expected_code}\n```\n"

    assert normalizer.normalize_text(text, includes) == expected
    assert includes == [{"repo_path": "sample.py", "options": options, "resolved": True}]


def test_multiple_includes_leave_unrelated_fence_and_prose_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a.html").write_text("<p>A</p>\n", encoding="utf-8")
    (tmp_path / "b.css").write_text("p { color: red; }\n", encoding="utf-8")
    monkeypatch.setattr(normalizer, "RAW_CODE", tmp_path)
    includes: list[dict] = []

    text = (
        "# Start\n\n"
        "```jinja\n{!a.html!}\n```\n\n"
        "```text\n# literal heading\nunchanged\n```\n\n"
        "Between blocks.\n\n"
        "~~~CSS\n{* b.css *}\n~~~\n\n"
        "## End\n"
    )
    expected = (
        "# Start\n\n"
        "```jinja\n<!-- a.html -->\n<p>A</p>\n```\n\n"
        "```text\n# literal heading\nunchanged\n```\n\n"
        "Between blocks.\n\n"
        "~~~CSS\n/* b.css */\np { color: red; }\n~~~\n\n"
        "## End\n"
    )

    assert normalizer.normalize_text(text, includes) == expected
    assert includes == [
        {"repo_path": "a.html", "options": "", "resolved": True},
        {"repo_path": "b.css", "options": "", "resolved": True},
    ]


def test_main_writes_documents_and_consistent_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_docs = tmp_path / "raw" / "docs"
    raw_code = tmp_path / "raw" / "code"
    output_docs = tmp_path / "normalized" / "docs"
    raw_docs.mkdir(parents=True)
    raw_code.mkdir(parents=True)
    docs_manifest = tmp_path / "raw" / "manifest.json"
    output_manifest = tmp_path / "normalized" / "manifest.json"

    source = {
        "id": "github-docs",
        "type": "git",
        "version": "v1",
        "repo": "example/docs",
        "repo_url": "https://example.invalid/docs",
        "ref": "fixture-commit",
    }
    files = [
        {
            "output_filename": "example.md",
            "source_path": "docs/example.md",
            "bucket": "tutorial",
            "url": "https://example.invalid/docs/example.md",
        }
    ]
    raw_text = "# Café\n\n```jinja\n{!example.html!}\n```\n\n{!missing.py!}\n"
    expected = (
        "# Café\n\n```jinja\n<!-- example.html -->\n"
        "<p>Example</p>\n```\n\n"
        "<!-- unresolved include: missing.py -->\n"
    )
    (raw_docs / "example.md").write_text(raw_text, encoding="utf-8")
    (raw_code / "example.html").write_text("<p>Example</p>\n", encoding="utf-8")
    manifest_text = json.dumps({"source": source, "files": files})
    docs_manifest.write_text(manifest_text, encoding="utf-8")

    monkeypatch.setattr(normalizer, "RAW_DOCS", raw_docs)
    monkeypatch.setattr(normalizer, "RAW_CODE", raw_code)
    monkeypatch.setattr(normalizer, "DOCS_MANIFEST", docs_manifest)
    monkeypatch.setattr(normalizer, "CODE_MANIFEST", tmp_path / "raw" / "code_manifest.json")
    monkeypatch.setattr(normalizer, "NORM_DOCS", output_docs)
    monkeypatch.setattr(normalizer, "NORM_MANIFEST", output_manifest)

    normalizer.main()

    assert sorted(path.name for path in output_docs.iterdir()) == ["example.md"]
    assert (output_docs / "example.md").read_text(encoding="utf-8") == expected
    manifest = json.loads(output_manifest.read_text(encoding="utf-8"))
    expected_bytes = len(expected.encode("utf-8"))
    assert manifest["source"] == source
    assert manifest["stats"] == {
        "total_files": 1,
        "total_bytes": expected_bytes,
        "by_bucket": {"tutorial": {"files": 1, "bytes": expected_bytes}},
    }
    assert manifest["files"] == [
        {
            **files[0],
            "bytes": expected_bytes,
            "includes": [
                {"repo_path": "example.html", "options": "", "resolved": True},
                {"repo_path": "missing.py", "options": "", "resolved": False},
            ],
        }
    ]
    assert manifest["normalization"]["inlined_includes"] == 1
    assert manifest["normalization"]["unresolved_includes"] == 1
    assert (raw_docs / "example.md").read_text(encoding="utf-8") == raw_text
    assert docs_manifest.read_text(encoding="utf-8") == manifest_text
    assert (raw_code / "example.html").read_text(encoding="utf-8") == "<p>Example</p>\n"


@pytest.mark.parametrize(
    ("author_fence", "safe_fence"),
    [("~~~", "~~~~"), ("```", "````")],
)
def test_included_fence_line_does_not_close_author_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    author_fence: str,
    safe_fence: str,
) -> None:
    source = f'value = """\n{author_fence}\nexample\n"""\n'
    (tmp_path / "sample.py").write_text(source, encoding="utf-8")
    monkeypatch.setattr(normalizer, "RAW_CODE", tmp_path)
    includes: list[dict] = []

    text = f"{author_fence}python\n{{!sample.py!}}\n{author_fence}\n\n## After\n"
    expected = f"{safe_fence}python\n# sample.py\n{source}{safe_fence}\n\n## After\n"

    assert normalizer.normalize_text(text, includes) == expected
    assert includes == [{"repo_path": "sample.py", "options": "", "resolved": True}]


@pytest.mark.parametrize("include", ["{!> example.js !}", "{!>example.js!}"])
@pytest.mark.parametrize("author_fenced", [False, True])
def test_javascript_arrow_include_uses_valid_source_comment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    include: str,
    author_fenced: bool,
) -> None:
    (tmp_path / "example.js").write_text("const value = 1;\n", encoding="utf-8")
    monkeypatch.setattr(normalizer, "RAW_CODE", tmp_path)
    includes: list[dict] = []

    if author_fenced:
        text = f"```Javascript\n{include}\n```\n"
        expected = "```Javascript\n// example.js\nconst value = 1;\n```\n"
    else:
        text = include + "\n"
        expected = "```js\n// example.js\nconst value = 1;\n```\n"

    assert normalizer.normalize_text(text, includes) == expected
    assert includes == [{"repo_path": "example.js", "options": "", "resolved": True}]


def test_cli_routes_candidate_without_active_inputs(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    (raw / "docs").mkdir(parents=True)
    (raw / "code").mkdir()
    (raw / "docs/example.md").write_text("# Candidate\n{!example.py!}\n")
    (raw / "code/example.py").write_text("candidate = True\n")
    source = {
        "id": "github-docs",
        "type": "git",
        "version": "v1",
        "repo": "fixture/docs",
        "repo_url": "https://example.invalid",
        "ref": "candidate-pin",
    }
    record = {
        "output_filename": "example.md",
        "source_path": "docs/example.md",
        "bucket": "tutorial",
        "url": "https://example.invalid/example.md",
    }
    (raw / "manifest.json").write_text(json.dumps({"source": source, "files": [record]}))
    (raw / "code_manifest.json").write_text("{}")
    output = tmp_path / "normalized"
    result = subprocess.run(
        [sys.executable, str(NORMALIZER), "--raw-dir", str(raw), "--output-dir", str(output)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (output / "docs/example.md").read_text() == (
        "# Candidate\n```python\n# example.py\ncandidate = True\n```\n"
    )
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["source"] == source
    assert manifest["derived_from"] == {
        "docs_manifest": "manifest.json",
        "code_manifest": "code_manifest.json",
    }
    assert manifest["files"][0]["includes"] == [
        {"repo_path": "example.py", "options": "", "resolved": True}
    ]
    assert manifest["normalization"]["unresolved_includes"] == 0
    assert sorted(p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()) == [
        "docs/example.md",
        "manifest.json",
    ]


@pytest.fixture
def raw_candidate(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    (raw / "docs").mkdir(parents=True)
    (raw / "code").mkdir()
    (raw / "docs/example.md").write_text("# Candidate\n", encoding="utf-8")
    source = {
        "id": "github-docs",
        "type": "git",
        "version": "v1",
        "repo": "fixture/docs",
        "repo_url": "https://example.invalid",
        "ref": "candidate-pin",
    }
    record = {
        "output_filename": "example.md",
        "source_path": "docs/example.md",
        "bucket": "tutorial",
        "url": "https://example.invalid/example.md",
    }
    (raw / "manifest.json").write_text(json.dumps({"source": source, "files": [record]}))
    (raw / "code_manifest.json").write_text("{}")
    return raw


@pytest.mark.parametrize(
    ("case", "diagnostic"),
    [
        ("raw-only", "--raw-dir and --output-dir must be supplied together"),
        ("output-only", "--raw-dir and --output-dir must be supplied together"),
        ("existing-directory", "--output-dir must not exist"),
        ("existing-file", "--output-dir must not exist"),
        ("same", "--output-dir must not exist"),
        ("inside-input", "--output-dir must not overlap --raw-dir"),
        ("input-inside-output", "--output-dir must not exist"),
        ("active-raw", "--output-dir must be outside the active raw and normalized trees"),
        ("active-normalized", "--output-dir must be outside the active raw and normalized trees"),
        ("symlink-active", "--output-dir must be outside the active raw and normalized trees"),
        ("symlink-input", "--output-dir must not overlap --raw-dir"),
        ("dangling-symlink", "--output-dir must not exist"),
    ],
)
def test_cli_rejects_paths_without_writes(
    tmp_path: Path,
    raw_candidate: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    case: str,
    diagnostic: str,
) -> None:
    raw = raw_candidate
    output = tmp_path / "output"
    active_root = tmp_path / "active"
    if case == "existing-directory":
        output.mkdir()
    elif case == "existing-file":
        output.write_text("keep")
    elif case == "same":
        output = raw
    elif case == "inside-input":
        output = raw / "nested" / "output"
    elif case == "input-inside-output":
        output = tmp_path
    elif case in {"active-raw", "active-normalized"}:
        tree = "raw" if case == "active-raw" else "normalized"
        output = active_root / tree / "candidate"
    elif case == "symlink-active":
        (active_root / "raw").mkdir(parents=True)
        (tmp_path / "alias").symlink_to(active_root / "raw", target_is_directory=True)
        output = tmp_path / "alias/candidate"
    elif case == "symlink-input":
        (tmp_path / "alias").symlink_to(raw, target_is_directory=True)
        output = tmp_path / "alias/nested/output"
    elif case == "dangling-symlink":
        output.symlink_to(tmp_path / "absent")
    args = ["--raw-dir", str(raw), "--output-dir", str(output)]
    if case == "raw-only":
        args = args[:2]
    elif case == "output-only":
        args = args[2:]
    before_paths = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
    before = {
        str(p.relative_to(tmp_path)): p.read_bytes()
        for p in tmp_path.rglob("*")
        if p.is_file() and not p.is_symlink()
    }
    if case in {"active-raw", "active-normalized", "symlink-active"}:
        monkeypatch.setattr(normalizer, "DATA_ROOT", active_root)
        with pytest.raises(SystemExit) as error:
            normalizer.main(args)
        assert error.value.code == 2
        stderr = capsys.readouterr().err
    else:
        result = subprocess.run(
            [sys.executable, str(NORMALIZER), *args], capture_output=True, text=True
        )
        assert result.returncode == 2
        stderr = result.stderr
    assert f"error: {diagnostic}" in stderr
    after = {
        str(p.relative_to(tmp_path)): p.read_bytes()
        for p in tmp_path.rglob("*")
        if p.is_file() and not p.is_symlink()
    }
    assert after == before
    assert sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")) == before_paths
    if case not in {
        "existing-directory",
        "existing-file",
        "same",
        "dangling-symlink",
        "input-inside-output",
    }:
        assert not output.exists()
    assert not (raw / "nested").exists()


@pytest.mark.parametrize("missing", ["manifest.json", "code_manifest.json"])
def test_cli_rejects_missing_manifest_without_writes(
    tmp_path: Path,
    raw_candidate: Path,
    missing: str,
) -> None:
    (raw_candidate / missing).unlink()
    output = tmp_path / "output"
    before = {
        str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
    }
    result = subprocess.run(
        [
            sys.executable,
            str(NORMALIZER),
            "--raw-dir",
            str(raw_candidate),
            "--output-dir",
            str(output),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert f"error: --raw-dir is missing {missing}" in result.stderr
    assert not output.exists()
    assert {
        str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
    } == before


def test_cli_help_has_no_writes(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(NORMALIZER), "--help"], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "--raw-dir" in result.stdout
    assert "--output-dir" in result.stdout
    assert list(tmp_path.iterdir()) == []
