import hashlib
import html
import json
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from data_curation.pipeline_v2.artifacts import artifact_bytes, snapshot_hashes
from data_curation.pipeline_v2.recipe import DISCLOSURE, build_analysis_recipe
from data_curation.pipeline_v2.schemas import (
    AnalysisCheckpoint,
    EnrichmentManifest,
    RunMetadata,
    SourceInventory,
    normalize_topics,
)
from data_curation.pipeline_v2.sources import prepare_source_inventory


def _read_inputs(run_dir):
    saved, hashes = {}, {}
    for name in ("run_metadata", "source_inventory", "analysis_results"):
        content = (run_dir / f"{name}.json").read_bytes()
        try:
            saved[name] = json.loads(content)
        except (ValueError, UnicodeError):
            raise ValueError(f"publication {run_dir / name}: invalid JSON") from None
        hashes[name] = hashlib.sha256(content).hexdigest()
    run = RunMetadata.model_validate(saved["run_metadata"])
    inventory = SourceInventory.model_validate(saved["source_inventory"])
    checkpoint = AnalysisCheckpoint.model_validate(saved["analysis_results"])
    if run.run_id != run_dir.name or checkpoint.run_id != run.run_id:
        raise ValueError(f"publication {run_dir}: run_id mismatch")
    expected = build_analysis_recipe().model_dump()
    expected["analysis_spec"].update(snapshot_hashes(run))
    if any(saved["run_metadata"][key] != value for key, value in expected.items()):
        raise ValueError(f"publication {run_dir}: unsupported recipe or snapshot hashes")
    if run.source_inventory_sha256 != hashes["source_inventory"] or checkpoint.input_hashes != {
        name: hashes[name] for name in ("run_metadata", "source_inventory")
    }:
        raise ValueError(f"publication {run_dir}: input hash mismatch")
    selection = None
    if inventory.corpus.inventory_scope == "selected":
        selection = tuple(doc.source_path for doc in inventory.documents)
    current = prepare_source_inventory(
        Path(run.source_manifest_path), selected_source_paths=selection
    )
    if current.model_dump() != saved["source_inventory"]:
        raise ValueError(f"publication {run_dir}: inventory/source binding changed")
    if [doc["source_id"] for doc in checkpoint.documents] != [
        doc.source_id for doc in inventory.documents
    ]:
        raise ValueError(f"publication {run_dir}: checkpoint document order/identity mismatch")
    _check_document_inputs(run, inventory, checkpoint)
    return run, inventory, checkpoint, hashes


def _check_document_inputs(run, inventory, checkpoint):
    docs_dir = (Path(run.source_manifest_path).parent / "docs").resolve()
    for source, analyzed in zip(inventory.documents, checkpoint.documents, strict=True):
        context = f"publication {source.source_id}"
        if not analyzed["cycles"] or analyzed["cycles"][-1]["outcome"] is None:
            raise ValueError(f"{context}: latest analysis cycle is not final")
        path = (docs_dir / source.output_filename).resolve()
        if not path.is_relative_to(docs_dir):
            raise ValueError(f"{context}: document escapes docs directory")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != source.content_sha256:
            raise ValueError(f"{context}: source hash changed")
        original = content.decode("utf-8")
        provenance = analyzed["supplied_document"]
        selected = original
        if provenance["selection"] == "prefix":
            selected = original[: provenance["submitted_characters"] - len(DISCLOSURE)]
        supplied = selected + (DISCLOSURE if provenance["truncated"] else "")
        if (
            provenance["original_characters"] != len(original)
            or provenance["submitted_characters"] != len(supplied)
            or provenance["text_sha256"] != hashlib.sha256(supplied.encode("utf-8")).hexdigest()
        ):
            raise ValueError(f"{context}: supplied-document binding mismatch")
        for cycle in analyzed["cycles"]:
            prediction = cycle["model_analysis"]
            if prediction is None:
                continue
            evidence = prediction["injection_evidence"]
            if (
                prediction["injection_suspected"] and (not evidence or evidence not in selected)
            ) or (not prediction["injection_suspected"] and evidence):
                raise ValueError(f"{context}: invalid injection evidence")


def _build_manifest(run, inventory, checkpoint, hashes, revision):
    records = _build_document_records(inventory, checkpoint)
    manifest = EnrichmentManifest(
        schema_version="1.0",
        run_id=run.run_id,
        publication_id=revision,
        published_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        corpus=inventory.corpus.model_dump(),
        analysis_spec=run.analysis_spec,
        input_hashes={**hashes, "topic_reviews": None},
        documents=records,
    )
    _check_manifest_correspondence(manifest, run, inventory, checkpoint, hashes)
    return manifest


def _build_document_records(inventory, checkpoint):
    records = []
    for source, analyzed in zip(inventory.documents, checkpoint.documents, strict=True):
        cycle = analyzed["cycles"][-1]
        prediction = cycle["model_analysis"]
        records.append(
            {
                **source.model_dump(),
                "outcome": cycle["outcome"],
                "exclusion_reason": {
                    "ready": None,
                    "quarantined": "injection_policy",
                    "analysis_failed": "analysis_unavailable",
                }[cycle["outcome"]],
                "overall_quality": None
                if prediction is None
                else round(sum(prediction["scores"].values()) / 5, 1),
                "model_analysis": prediction,
                "assigned_topics": []
                if prediction is None
                else [
                    dict(topic=topic, assigned_by="model", review_status="unreviewed")
                    for topic in normalize_topics(prediction["topics"])
                ],
                "topic_reviews": [],
                "analysis_provenance": dict(
                    finished_at=cycle["finished_at"],
                    response_model=cycle["attempts"][-1]["response_model"],
                    supplied_document=analyzed["supplied_document"],
                ),
                "analysis_error": cycle["analysis_error"],
            }
        )
    return records


def _check_manifest_correspondence(manifest, run, inventory, checkpoint, hashes):
    if (
        manifest.run_id != run.run_id
        or manifest.corpus != inventory.corpus.model_dump()
        or manifest.analysis_spec != run.analysis_spec
        or manifest.input_hashes != {**hashes, "topic_reviews": None}
    ):
        raise ValueError("publication construction: header differs from validated inputs")
    for source, analyzed, record in zip(
        inventory.documents, checkpoint.documents, manifest.documents, strict=True
    ):
        cycle = analyzed["cycles"][-1]
        if (
            any(record[key] != value for key, value in source.model_dump().items())
            or record["source_id"] != analyzed["source_id"]
            or any(
                record[key] != cycle[key] for key in ("outcome", "model_analysis", "analysis_error")
            )
            or record["analysis_provenance"]
            != dict(
                finished_at=cycle["finished_at"],
                response_model=cycle["attempts"][-1]["response_model"],
                supplied_document=analyzed["supplied_document"],
            )
        ):
            raise ValueError(
                f"publication construction {source.source_id}: copied fields differ from validated inputs"
            )


def _render_report(manifest):
    counts = {
        outcome: sum(doc["outcome"] == outcome for doc in manifest.documents)
        for outcome in ("ready", "quarantined", "analysis_failed")
    }
    lines = _report_summary(manifest, counts)
    _append_document_outcomes(lines, manifest)
    _append_quarantines(lines, manifest, counts)
    _append_failures(lines, manifest, counts)
    _append_coverage_and_limitations(lines, manifest)
    return "\n".join(lines) + "\n"


def _report_summary(manifest, counts):
    lines = [
        f"# Metadata publication {manifest.publication_id}",
        "",
        f"- Run: {_markdown(manifest.run_id)}",
        f"- Published at: {_markdown(manifest.published_at)}",
        f"- Inventory scope: {_markdown(manifest.corpus['inventory_scope'])}",
        "- Manifest: [enrichment_manifest.json](enrichment_manifest.json)",
        f"- Requested model: {_markdown(manifest.analysis_spec['requested_model'])}",
        "",
        "## Counts",
        "",
        "| Outcome | Documents | Eligible for ingestion |",
        "|---|---:|---:|",
    ]
    for outcome, count in counts.items():
        lines.append(f"| {outcome} | {count} | {count if outcome == 'ready' else 0} |")
    lines.extend(
        [
            f"| Total | {len(manifest.documents)} | {counts['ready']} |",
            "",
            "Every selected document has a final outcome.",
        ]
    )
    if manifest.corpus["inventory_scope"] == "selected":
        lines.append("These counts describe the selected inventory, not the whole corpus.")
    if not counts["ready"]:
        lines.append(
            "Zero eligible records: no document in this publication is eligible for ingestion."
        )
    lines.append("Publication does not perform ingestion.")
    return lines


def _append_document_outcomes(lines, manifest):
    lines.extend(
        [
            "",
            "## Document outcomes",
            "",
            "| Source | Outcome | Quality | Assigned topics |",
            "|---|---|---:|---|",
        ]
    )
    for doc in manifest.documents:
        topics = (
            ", ".join(_markdown(assignment["topic"]) for assignment in doc["assigned_topics"])
            or "—"
        )
        quality = "—" if doc["overall_quality"] is None else f"{doc['overall_quality']:.1f}"
        lines.append(
            f"| {_markdown(doc['source_path'])} | {doc['outcome']} | {quality} | {topics} |"
        )
    lines.extend(
        [
            "",
            "Quality is the rounded mean of five model scores.",
            "It is not an ingestion threshold.",
        ]
    )


def _append_quarantines(lines, manifest, counts):
    lines.extend(["", "## Quarantined documents", ""])
    if not counts["quarantined"]:
        lines.append("None")
    for doc in manifest.documents:
        if doc["outcome"] == "quarantined":
            lines.extend(
                [
                    f"### {_markdown(doc['source_path'])}",
                    "",
                    f"- Exclusion reason: {_markdown(doc['exclusion_reason'])}",
                    "- Analysis completed successfully.",
                    "- The model reported suspected manipulation of the analysis.",
                    "- Exact evidence is preserved in the manifest's model_analysis.injection_evidence field.",
                    "- The whole document is excluded.",
                    "",
                ]
            )
    if counts["quarantined"]:
        lines.append(
            "This is a model-derived quarantine decision, not a verified security verdict."
        )


def _append_failures(lines, manifest, counts):
    lines.extend(["", "## Failed analyses", ""])
    if not counts["analysis_failed"]:
        lines.append("None")
    for doc in manifest.documents:
        error = doc["analysis_error"]
        if error:
            lines.extend(
                [
                    f"### {_markdown(doc['source_path'])}",
                    "",
                    f"- Exclusion reason: {_markdown(doc['exclusion_reason'])}",
                    f"- Error: {_markdown(error['code'])}",
                    f"- Attempts in the latest cycle: {error['attempts']}",
                    f"- Message: {_markdown(error['message'])}",
                    "- No valid model predictions or quality score are available.",
                    "",
                ]
            )


def _append_coverage_and_limitations(lines, manifest):
    full = sum(
        doc["analysis_provenance"]["supplied_document"]["selection"] == "full"
        for doc in manifest.documents
    )
    lines.extend(
        [
            "",
            "## Input coverage",
            "",
            f"Full documents: {full}; prefixes: {len(manifest.documents) - full}.",
            "",
        ]
    )
    for doc in manifest.documents:
        supplied = doc["analysis_provenance"]["supplied_document"]
        coverage = f"full document; {supplied['original_characters']} original characters"
        if supplied["selection"] == "prefix":
            coverage = (
                f"prefix; {supplied['submitted_characters'] - len(DISCLOSURE)} selected original characters"
                f" + {len(DISCLOSURE)} disclosure characters; "
                f"{supplied['original_characters']} original characters"
            )
        lines.append(f"- {_markdown(doc['source_path'])}: {coverage}.")
    lines.extend(
        [
            "",
            "A failed document's supplied-input record does not establish successful analysis or provider receipt.",
            "",
            "## Limitations",
            "",
            "- All assigned topics are model-assigned and unreviewed. Every topic_reviews collection is empty.",
            "- Topics are open-ended. Naming normalization does not add synonyms or parent categories, so topic filters may omit relevant documents.",
            "- Prefix predictions and injection detection cover only the supplied excerpt. They do not establish coverage or safety of the omitted remainder.",
            "- Schema, provenance and freshness checks do not certify technical accuracy, model judgment quality, executable examples or retrieval improvement.",
            "- Publication makes no model calls and does not retry failed analyses.",
            "- Current sources were checked at publication. Consumers must verify their correspondence again when loading documents.",
            "- Later analysis retries do not alter this publication.",
        ]
    )


def _markdown(value):
    text = html.escape(" ".join(value.splitlines()), quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+!~])", r"\\\1", text).replace("|", "&#124;")


def publish_run(run_dir: Path) -> Path:
    run, inventory, checkpoint, hashes = _read_inputs(run_dir)
    publications = run_dir / "publications"
    if publications.is_symlink() or (publications.exists() and not publications.is_dir()):
        raise ValueError(f"publication {publications}: must be a directory, not a file or symlink")
    publications.mkdir(exist_ok=True)
    numbers = [
        int(path.name) for path in publications.iterdir() if re.fullmatch(r"[0-9]+", path.name)
    ]
    revision = f"{max(numbers, default=0) + 1:03d}"
    final = publications / revision
    if final.exists() or final.is_symlink():
        raise FileExistsError(f"Publication destination occupied: {final}")
    manifest = _build_manifest(run, inventory, checkpoint, hashes, revision)
    buffers = {
        "enrichment_manifest.json": artifact_bytes(manifest.model_dump(), "enrichment_manifest"),
        "report.md": _render_report(manifest).encode("utf-8"),
    }
    temporary = Path(tempfile.mkdtemp(prefix=f".publish-{revision}-", dir=publications))
    for name, content in buffers.items():
        (temporary / name).write_bytes(content)
    saved_manifest = None
    for name, expected in buffers.items():
        content = (temporary / name).read_bytes()
        if content != expected:
            raise ValueError(
                f"publication {temporary / name}: readback differs from prepared bytes"
            )
        if name == "enrichment_manifest.json":
            saved_manifest = EnrichmentManifest.model_validate(json.loads(content))
    if saved_manifest.run_id != run.run_id or saved_manifest.publication_id != revision:
        raise ValueError(
            f"publication {temporary}: saved identity differs from intended destination"
        )
    if final.exists() or final.is_symlink():
        raise FileExistsError(f"Publication destination occupied: {final}")
    temporary.rename(final)
    return final
