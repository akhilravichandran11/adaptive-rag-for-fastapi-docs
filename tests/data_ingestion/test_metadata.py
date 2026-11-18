"""
Prove that corpus and curation metadata reach every kept document, and that gaps fail loudly.

``data_ingestion.metadata`` joins the corpus manifest and one named curation run
onto each loaded document by ``output_filename``. These tests build both sources
as small files under ``tmp_path``, so they need neither the git-ignored corpus nor
a real curation run:

- a matched document gains all seven fields and keeps what it already had;
- a document flagged as a suspected prompt injection is dropped, logged by name,
  and counted. No real run flags anything, so this is the only place that path runs;
- a missing run, a missing file, a missing field, or an unmatched document raises
  and names what is missing.

Run offline::

    uv run pytest tests/data_ingestion/test_metadata.py
"""

import json
from pathlib import Path

import pytest
from haystack import Document

from data_ingestion.metadata import CONTENT_ANALYSIS_FILENAME, enrich_documents

RUN_ID = "20260101_000000"
CORPUS_COMMIT = "abc1234"
CLEAN = "tutorial__first-steps.md"
FLAGGED = "advanced__suspicious.md"

ENRICHED_FIELDS = (
    "source_path",
    "bucket",
    "url",
    "corpus_commit",
    "quality_score",
    "knowledge_domain",
    "curation_run",
)


def manifest_record(filename: str) -> dict:
    return {
        "output_filename": filename,
        "source_path": f"docs/en/docs/{filename.replace('__', '/')}",
        "bucket": filename.split("__")[0],
        "url": f"https://fastapi.tiangolo.com/{filename.removesuffix('.md')}/",
    }


def curation_record(filename: str, *, flagged: bool = False) -> dict:
    return {
        "output_filename": filename,
        "analysis": {"overall_quality": 8.2},
        "knowledge_domain": "Request handling",
        "suspected_prompt_injection": flagged,
    }


def write_manifest(tmp_path: Path, records: list[dict]) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"source": {"ref": CORPUS_COMMIT}, "files": records}))
    return path


def write_run(tmp_path: Path, records: list[dict]) -> Path:
    root = tmp_path / "outputs"
    (root / RUN_ID).mkdir(parents=True)
    (root / RUN_ID / CONTENT_ANALYSIS_FILENAME).write_text(json.dumps(records))
    return root


def loaded(filename: str) -> Document:
    return Document(
        content=f"# {filename}",
        meta={"output_filename": filename, "file_path": f"docs/{filename}"},
    )


def enrich(tmp_path: Path, documents: list[Document], *, flagged: frozenset[str] = frozenset()):
    names = [document.meta["output_filename"] for document in documents]
    return enrich_documents(
        documents,
        curation_run=RUN_ID,
        manifest_path=write_manifest(tmp_path, [manifest_record(n) for n in names]),
        curation_outputs_root=write_run(
            tmp_path, [curation_record(n, flagged=n in flagged) for n in names]
        ),
    )


def test_a_matched_document_gains_every_field_and_keeps_its_own(tmp_path) -> None:
    original = loaded(CLEAN)
    result = enrich(tmp_path, [original])

    assert len(result.documents) == 1
    assert len(result.dropped_filenames) == 0
    enriched = result.documents[0]
    assert set(ENRICHED_FIELDS) <= set(enriched.meta)
    assert enriched.meta["source_path"] == "docs/en/docs/tutorial/first-steps.md"
    assert enriched.meta["bucket"] == "tutorial"
    assert enriched.meta["url"] == "https://fastapi.tiangolo.com/tutorial__first-steps/"
    assert enriched.meta["corpus_commit"] == CORPUS_COMMIT
    assert enriched.meta["quality_score"] == 8.2
    assert enriched.meta["knowledge_domain"] == "Request handling"
    assert enriched.meta["curation_run"] == RUN_ID
    # What loading put there survives, the id is not renumbered, and the input is untouched.
    assert enriched.meta["output_filename"] == CLEAN
    assert enriched.meta["file_path"] == f"docs/{CLEAN}"
    assert enriched.id == original.id
    assert enriched.content == original.content
    assert original.meta == {"output_filename": CLEAN, "file_path": f"docs/{CLEAN}"}


def test_a_flagged_document_is_dropped_logged_and_counted(tmp_path, caplog) -> None:
    with caplog.at_level("WARNING", logger="data_ingestion.metadata"):
        result = enrich(tmp_path, [loaded(CLEAN), loaded(FLAGGED)], flagged=frozenset({FLAGGED}))

    assert [document.meta["output_filename"] for document in result.documents] == [CLEAN]
    assert result.dropped_filenames == [FLAGGED]
    assert len(result.dropped_filenames) == 1
    assert len(result.documents) == 1
    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.name == "data_ingestion.metadata" and record.levelname == "WARNING"
    ]
    assert len(warnings) == 1
    assert FLAGGED in warnings[0]
    assert RUN_ID in warnings[0]


def test_a_missing_run_folder_raises_naming_the_run(tmp_path) -> None:
    manifest_path = write_manifest(tmp_path, [manifest_record(CLEAN)])

    # Match the folder wording: without the folder check, the missing analysis file
    # would raise instead, and its message names the run too.
    with pytest.raises(FileNotFoundError, match=rf"run folder not found.*{RUN_ID}"):
        enrich_documents(
            [loaded(CLEAN)],
            curation_run=RUN_ID,
            manifest_path=manifest_path,
            curation_outputs_root=tmp_path / "outputs",
        )


def test_a_run_without_its_analysis_file_raises_naming_the_file(tmp_path) -> None:
    manifest_path = write_manifest(tmp_path, [manifest_record(CLEAN)])
    (tmp_path / "outputs" / RUN_ID).mkdir(parents=True)

    with pytest.raises(
        FileNotFoundError,
        match=rf"Curation analysis for run '{RUN_ID}' not found: .*{CONTENT_ANALYSIS_FILENAME}",
    ):
        enrich_documents(
            [loaded(CLEAN)],
            curation_run=RUN_ID,
            manifest_path=manifest_path,
            curation_outputs_root=tmp_path / "outputs",
        )


def test_a_missing_manifest_raises_naming_the_path(tmp_path) -> None:
    root = write_run(tmp_path, [curation_record(CLEAN)])
    missing = tmp_path / "no-such-manifest.json"

    with pytest.raises(FileNotFoundError, match=r"Manifest not found: .*no-such-manifest\.json"):
        enrich_documents(
            [loaded(CLEAN)],
            curation_run=RUN_ID,
            manifest_path=missing,
            curation_outputs_root=root,
        )


def test_an_unreadable_manifest_raises_naming_the_path(tmp_path) -> None:
    # A folder where the file should be: present, so not FileNotFoundError, but unreadable.
    unreadable = tmp_path / "manifest.json"
    unreadable.mkdir()

    with pytest.raises(OSError, match=r"Manifest could not be read: .*manifest\.json"):
        enrich_documents(
            [loaded(CLEAN)],
            curation_run=RUN_ID,
            manifest_path=unreadable,
            curation_outputs_root=write_run(tmp_path, [curation_record(CLEAN)]),
        )


@pytest.mark.parametrize(
    ("content", "problem"),
    [
        (b"\xff not utf-8", "not valid UTF-8"),
        (b"{not json", "not valid JSON"),
        (b"1" * 5000, "not valid JSON"),  # json.loads raises a plain ValueError for this
    ],
)
def test_a_manifest_that_is_not_utf8_json_raises_naming_the_path(
    tmp_path, content, problem
) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(content)

    with pytest.raises(ValueError, match=rf"Manifest is {problem}: .*manifest\.json"):
        enrich_documents(
            [loaded(CLEAN)],
            curation_run=RUN_ID,
            manifest_path=manifest_path,
            curation_outputs_root=write_run(tmp_path, [curation_record(CLEAN)]),
        )


def test_a_manifest_entry_missing_a_field_raises_naming_it(tmp_path) -> None:
    record = manifest_record(CLEAN)
    del record["url"]

    with pytest.raises(ValueError, match=rf"'url'.*{CLEAN}"):
        enrich_documents(
            [loaded(CLEAN)],
            curation_run=RUN_ID,
            manifest_path=write_manifest(tmp_path, [record]),
            curation_outputs_root=write_run(tmp_path, [curation_record(CLEAN)]),
        )


def test_a_curation_entry_missing_the_injection_flag_raises_naming_it(tmp_path) -> None:
    # A default here would keep an unreviewed document, so absence must be an error.
    record = curation_record(CLEAN)
    del record["suspected_prompt_injection"]

    with pytest.raises(ValueError, match=rf"'suspected_prompt_injection'.*{CLEAN}"):
        enrich_documents(
            [loaded(CLEAN)],
            curation_run=RUN_ID,
            manifest_path=write_manifest(tmp_path, [manifest_record(CLEAN)]),
            curation_outputs_root=write_run(tmp_path, [record]),
        )


def test_a_document_absent_from_the_manifest_raises_naming_it(tmp_path) -> None:
    with pytest.raises(ValueError, match=rf"no manifest entry for document '{FLAGGED}'"):
        enrich_documents(
            [loaded(CLEAN), loaded(FLAGGED)],
            curation_run=RUN_ID,
            manifest_path=write_manifest(tmp_path, [manifest_record(CLEAN)]),
            curation_outputs_root=write_run(
                tmp_path, [curation_record(CLEAN), curation_record(FLAGGED)]
            ),
        )


def test_a_document_absent_from_the_run_raises_naming_it_and_the_run(tmp_path) -> None:
    with pytest.raises(
        ValueError, match=rf"no entry for document '{FLAGGED}' in curation run '{RUN_ID}'"
    ):
        enrich_documents(
            [loaded(CLEAN), loaded(FLAGGED)],
            curation_run=RUN_ID,
            manifest_path=write_manifest(
                tmp_path, [manifest_record(CLEAN), manifest_record(FLAGGED)]
            ),
            curation_outputs_root=write_run(tmp_path, [curation_record(CLEAN)]),
        )


def test_a_document_with_nothing_to_join_on_raises(tmp_path) -> None:
    with pytest.raises(ValueError, match="has no 'output_filename'"):
        enrich_documents(
            [Document(content="built in memory, never loaded from disk")],
            curation_run=RUN_ID,
            manifest_path=write_manifest(tmp_path, [manifest_record(CLEAN)]),
            curation_outputs_root=write_run(tmp_path, [curation_record(CLEAN)]),
        )


# --- Shape checks (IDX-11): a present field with the wrong value is refused too. ---


def enrich_from(tmp_path: Path, manifest: object, curation: object, *, run_id: str = RUN_ID):
    """Enrich one CLEAN document from raw source payloads, for inputs the builders cannot make."""
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    root = tmp_path / "outputs"
    (root / RUN_ID).mkdir(parents=True)
    (root / RUN_ID / CONTENT_ANALYSIS_FILENAME).write_text(json.dumps(curation))
    return enrich_documents(
        [loaded(CLEAN)],
        curation_run=run_id,
        manifest_path=manifest_path,
        curation_outputs_root=root,
    )


def good_manifest(*records: dict) -> dict:
    return {"source": {"ref": CORPUS_COMMIT}, "files": list(records) or [manifest_record(CLEAN)]}


@pytest.mark.parametrize("flag", ["false", 0, None])
def test_a_non_boolean_injection_flag_raises_naming_it(tmp_path, flag) -> None:
    # bool("false") is True, so coercing would drop a clean document as flagged.
    record = curation_record(CLEAN)
    record["suspected_prompt_injection"] = flag

    with pytest.raises(
        ValueError, match=rf"'suspected_prompt_injection' must be true or false.*{CLEAN}"
    ):
        enrich_from(tmp_path, good_manifest(), [record])


def test_a_repeated_filename_in_the_manifest_raises_naming_it(tmp_path) -> None:
    manifest = good_manifest(manifest_record(CLEAN), manifest_record(CLEAN))

    with pytest.raises(ValueError, match=rf"duplicate 'output_filename' '{CLEAN}' in manifest"):
        enrich_from(tmp_path, manifest, [curation_record(CLEAN)])


def test_a_repeated_filename_in_the_run_raises_naming_it_and_the_run(tmp_path) -> None:
    with pytest.raises(ValueError, match=rf"duplicate .*'{CLEAN}' in curation run '{RUN_ID}'"):
        enrich_from(tmp_path, good_manifest(), [curation_record(CLEAN), curation_record(CLEAN)])


@pytest.mark.parametrize(
    "score", ["8.2", None, True, float("nan"), float("inf"), float("-inf"), 10**400]
)
def test_a_non_numeric_quality_score_raises_naming_it(tmp_path, score) -> None:
    record = curation_record(CLEAN)
    record["analysis"]["overall_quality"] = score

    with pytest.raises(ValueError, match=rf"'overall_quality' must be a number.*{CLEAN}"):
        enrich_from(tmp_path, good_manifest(), [record])


@pytest.mark.parametrize("domain", ["", None, 3, "   ", "\t\n"])
def test_a_blank_or_non_string_domain_raises_naming_it(tmp_path, domain) -> None:
    record = curation_record(CLEAN)
    record["knowledge_domain"] = domain

    with pytest.raises(
        ValueError, match=rf"'knowledge_domain' must be a non-empty string.*{CLEAN}"
    ):
        enrich_from(tmp_path, good_manifest(), [record])


@pytest.mark.parametrize("files", [None, {}, "x"])
def test_manifest_files_that_are_not_a_list_raise(tmp_path, files) -> None:
    manifest = {"source": {"ref": CORPUS_COMMIT}, "files": files}

    with pytest.raises(ValueError, match=r"'files' in manifest .* must be a list"):
        enrich_from(tmp_path, manifest, [curation_record(CLEAN)])


@pytest.mark.parametrize("filename", [42, ["a.md"]])
def test_a_non_string_filename_in_the_manifest_raises(tmp_path, filename) -> None:
    record = manifest_record(CLEAN)
    record["output_filename"] = filename

    with pytest.raises(ValueError, match=r"'output_filename' must be a string in manifest entry 0"):
        enrich_from(tmp_path, good_manifest(record), [curation_record(CLEAN)])


def test_a_non_string_filename_in_the_run_raises(tmp_path) -> None:
    record = curation_record(CLEAN)
    record["output_filename"] = 42

    with pytest.raises(ValueError, match=r"'output_filename' must be a string in curation entry 0"):
        enrich_from(tmp_path, good_manifest(), [record])


@pytest.mark.parametrize("run_id", ["", ".", "..", "../escaped", "/abs/run", "a/b", "a\\b"])
def test_an_unsafe_run_id_raises_naming_it(tmp_path, run_id) -> None:
    # A run id names one folder under the outputs root; anything else could read
    # the root itself or a file outside it.
    with pytest.raises(ValueError, match="single folder name") as raised:
        enrich_from(tmp_path, good_manifest(), [curation_record(CLEAN)], run_id=run_id)
    assert repr(run_id) in str(raised.value)
