"""Offline regression tests for Stage 3's source-identity boundary."""

import importlib
import json
import socket
import sys
from copy import deepcopy
from pathlib import Path

import dotenv
import openai
import pytest
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from data_curation import schemas  # noqa: E402


@pytest.fixture
def file_fields():
    return {
        "filename": "fastapi_core_and_first_api.md",
        "description": "Build a first FastAPI application.",
        "content_focus": ["application setup"],
        "target_audience": "beginner",
        "estimated_length": "2000-3000 words",
        "key_sections": ["First application"],
    }


def test_draft_schema_exposes_ids_and_persisted_schema_exposes_paths():
    assert hasattr(schemas, "DraftSynthesisPlan")
    draft_schema = schemas.DraftSynthesisPlan.model_json_schema()
    persisted_schema = schemas.SynthesisPlan.model_json_schema()

    assert '"source_paths"' not in json.dumps(draft_schema)
    assert '"source_ids"' not in json.dumps(persisted_schema)

    draft_file = draft_schema["$defs"]["DraftDatasetFile"]
    persisted_file = persisted_schema["$defs"]["DatasetFile"]
    assert "source_ids" in draft_file["required"]
    assert "source_paths" in persisted_file["required"]
    assert set(draft_file["properties"]) - {"source_ids"} == (
        set(persisted_file["properties"]) - {"source_paths"}
    )

    selection = draft_file["properties"]["source_ids"]
    assert selection["items"]["type"] == "integer"
    assert selection["minItems"] == 1
    assert selection["maxItems"] == 8

    structure = draft_schema["$defs"]["DraftDatasetStructure"]
    assert list(structure["properties"]) == [
        "01_foundations",
        "02_implementation",
        "03_security_production",
    ]


@pytest.mark.parametrize("source_ids", [[1], [8, 2, 1], list(range(1, 9))])
def test_draft_file_preserves_valid_id_order(file_fields, source_ids):
    draft = schemas.DraftDatasetFile.model_validate({**file_fields, "source_ids": source_ids})
    assert draft.model_dump() == {**file_fields, "source_ids": source_ids}


@pytest.mark.parametrize(
    ("source_ids", "invalid_value"),
    [
        (None, None),
        ([], None),
        (list(range(1, 10)), None),
        ([1, 1], "1"),
        ([True], "True"),
        ([False], "False"),
        (["1"], "'1'"),
        ([1.0], "1.0"),
        ([1.5], "1.5"),
        ([None], "None"),
        ([{}], "{}"),
        (["tutorial/virtual-environments.md"], "'tutorial/virtual-environments.md'"),
    ],
)
def test_draft_file_rejects_invalid_ids_with_filename(file_fields, source_ids, invalid_value):
    with pytest.raises(ValidationError) as caught:
        schemas.DraftDatasetFile.model_validate({**file_fields, "source_ids": source_ids})

    message = str(caught.value)
    assert file_fields["filename"] in message
    if invalid_value is not None:
        assert invalid_value in message


def test_persisted_file_still_requires_exact_source_paths(file_fields):
    payload = {**file_fields, "source_paths": ["virtual-environments.md"]}
    assert schemas.DatasetFile.model_validate(payload).model_dump() == payload

    with pytest.raises(ValidationError):
        schemas.DatasetFile.model_validate({**file_fields, "source_ids": [1]})


SOURCE_PATHS = [
    "tutorial/body.md",
    "virtual-environments.md",
    "z-source-3.md",
    "z-source-4.md",
    "z-source-5.md",
    "z-source-6.md",
    "z-source-7.md",
    "z-source-8.md",
]

EXPECTED_INVENTORY = {
    "01_foundations": [
        "fastapi_core_and_first_api.md",
        "requests_validation_and_responses.md",
    ],
    "02_implementation": [
        "application_structure_and_dependencies.md",
        "data_models_and_crud.md",
        "testing_debugging_and_errors.md",
    ],
    "03_security_production": [
        "security_and_authentication.md",
        "deployment_and_operations.md",
    ],
}


@pytest.fixture
def stage_modules(monkeypatch):
    """Import stages with network access and client construction forbidden."""

    def refuse(*args, **kwargs):
        raise AssertionError("offline test attempted API or network activity")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(openai.OpenAI, "__init__", refuse)
    monkeypatch.setattr(openai.AsyncOpenAI, "__init__", refuse)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.syspath_prepend(str(PROJECT_ROOT / "data_curation"))

    stage3 = importlib.import_module("stage3_learning_taxonomy_final")
    stage4 = importlib.import_module("stage4_synthetic_dataset")
    monkeypatch.setattr(stage3, "chat_structured", refuse)
    monkeypatch.setattr(stage4, "chat", refuse)
    return stage3, stage4


@pytest.fixture
def source_generator(stage_modules, tmp_path):
    stage3, _ = stage_modules
    generator = stage3.FastAPILearningTaxonomyGenerator.__new__(
        stage3.FastAPILearningTaxonomyGenerator
    )
    generator.output_dir = tmp_path
    generator.docs_dir = tmp_path / "docs"
    generator.docs_dir.mkdir()
    generator.knowledge_domains = {
        "content_gaps": {"fixture": "Fixture gap"},
        "synthesis_opportunities": {"fixture": "Fixture opportunity"},
    }
    generator.files_by_source_path = {}
    generator.content_analysis = []
    candidates = []

    for source_id, source_path in enumerate(SOURCE_PATHS, start=1):
        output_filename = f"normalized-{source_id}.md"
        document = generator.docs_dir / output_filename
        document.write_text(f"Content for source {source_id}.", encoding="utf-8")
        candidate = {
            "source_path": source_path,
            "quality_score": 8.0,
            "content_type": "tutorial",
            "target_audience": "beginner",
            "key_concepts": [f"Topic {source_id}"],
            "one_sentence_summary": f"Explains topic {source_id}.",
            "output_filename": output_filename,
            "url": f"https://example.invalid/source/{source_id}",
            "physical_path": str(document),
        }
        candidates.append(candidate)
        generator.files_by_source_path[source_path] = {
            "source_path": source_path,
            "output_filename": output_filename,
        }
        generator.content_analysis.append(
            {
                **candidate,
                "analysis": {"overall_quality": 8.0},
                "suspected_prompt_injection": False,
            }
        )

    generator.source_candidates = list(reversed(candidates))
    generator.eligible_source_paths = set(SOURCE_PATHS)
    generator.source_catalog = dict(enumerate(candidates, start=1))
    return generator


@pytest.fixture
def draft_plan(file_fields):
    return {
        "dataset_structure": {
            category: {
                "description": category,
                "files": [
                    {**deepcopy(file_fields), "filename": filename, "source_ids": [2, 1]}
                    for filename in filenames
                ],
            }
            for category, filenames in EXPECTED_INVENTORY.items()
        },
        "content_prioritization": {
            "phase_1_critical": list(EXPECTED_INVENTORY["01_foundations"]),
            "phase_2_important": list(EXPECTED_INVENTORY["02_implementation"]),
            "phase_3_advanced": list(EXPECTED_INVENTORY["03_security_production"]),
        },
        "synthesis_strategies": {
            "combine_scattered_info": [],
            "fill_knowledge_gaps": [],
            "create_practical_examples": [],
        },
        "quality_criteria": {
            "information_density": "Useful explanations",
            "practical_focus": "Practical examples",
            "security_integration": "Explain security",
            "progressive_complexity": "Start with foundations",
            "real_world_relevance": "Common application needs",
        },
        "success_metrics": {
            "rag_performance": "Requires evaluation",
            "developer_value": "Requires review",
            "security_awareness": "Requires review",
            "practical_application": "Requires testing",
        },
    }


def test_catalog_ids_follow_exact_path_order_under_reordered_input(source_generator):
    assert hasattr(source_generator, "_build_source_catalog")
    expected = dict(enumerate(SOURCE_PATHS, start=1))
    original_order = [source["source_path"] for source in source_generator.source_candidates]

    catalog = source_generator._build_source_catalog()
    assert {source_id: source["source_path"] for source_id, source in catalog.items()} == expected
    assert [
        source["source_path"] for source in source_generator.source_candidates
    ] == original_order

    source_generator.source_candidates.reverse()
    assert source_generator._build_source_catalog() == catalog


def test_prompt_catalog_excludes_identity_fields(source_generator):
    prompt = source_generator._build_synthesis_prompt()
    catalog_text = prompt.split("ELIGIBLE CURRENT-RUN SOURCE METADATA:\n", 1)[1]
    catalog_text = catalog_text.split("\n\nAPPROVED DATASET CONTRACT:", 1)[0]
    catalog = json.loads(catalog_text)

    assert [entry["source_id"] for entry in catalog] == list(range(1, 9))
    for entry in catalog:
        assert set(entry) == {
            "source_id",
            "quality_score",
            "content_type",
            "target_audience",
            "key_concepts",
            "one_sentence_summary",
        }
    assert catalog[0] == {
        "source_id": 1,
        "quality_score": 8.0,
        "content_type": "tutorial",
        "target_audience": "beginner",
        "key_concepts": ["Topic 1"],
        "one_sentence_summary": "Explains topic 1.",
    }
    for source in source_generator.source_candidates:
        for field in ("source_path", "output_filename", "url", "physical_path"):
            assert source[field] not in catalog_text

    inventory_text = prompt.split("APPROVED DATASET CONTRACT:\n", 1)[1]
    inventory, _ = json.JSONDecoder().raw_decode(inventory_text)
    assert inventory == EXPECTED_INVENTORY
    assert "Fixture gap" in prompt
    assert "Fixture opportunity" in prompt


def test_materialization_preserves_exact_paths_order_and_other_fields(source_generator, draft_plan):
    original = deepcopy(draft_plan)
    plan = source_generator._materialize_synthesis_plan(draft_plan)

    assert draft_plan == original
    assert '"source_ids"' not in json.dumps(plan)
    assert schemas.SynthesisPlan.model_validate(plan).model_dump(by_alias=True) == plan

    for category, filenames in EXPECTED_INVENTORY.items():
        files = plan["dataset_structure"][category]["files"]
        assert [file_spec["filename"] for file_spec in files] == filenames
        for index, file_spec in enumerate(files):
            expected = dict(original["dataset_structure"][category]["files"][index])
            expected.pop("source_ids")
            expected["source_paths"] = [
                "virtual-environments.md",
                "tutorial/body.md",
            ]
            assert file_spec == expected

    for field in (
        "content_prioritization",
        "synthesis_strategies",
        "quality_criteria",
        "success_metrics",
    ):
        assert plan[field] == original[field]

    source_generator._validate_synthesis_plan_contract(plan)
    source_generator._validate_synthesis_plan_references(plan)


@pytest.mark.parametrize(
    ("source_ids", "invalid_value"),
    [
        (None, None),
        ([], None),
        (list(range(1, 10)), None),
        ([1, 1], "1"),
        ([True], "True"),
        ([False], "False"),
        (["1"], "'1'"),
        ([1.0], "1.0"),
        ([1.5], "1.5"),
        ([None], "None"),
        ([{}], "{}"),
        ([0], "0"),
        ([-1], "-1"),
        ([99], "99"),
        (["tutorial/virtual-environments.md"], "'tutorial/virtual-environments.md'"),
    ],
)
def test_materialization_rejects_invalid_selections_without_writing(
    stage_modules, source_generator, draft_plan, source_ids, invalid_value
):
    stage3, _ = stage_modules
    file_spec = draft_plan["dataset_structure"]["01_foundations"]["files"][0]
    file_spec["source_ids"] = source_ids
    before = {
        path.relative_to(source_generator.output_dir): path.read_bytes()
        for path in source_generator.output_dir.rglob("*")
        if path.is_file()
    }

    with pytest.raises((ValidationError, stage3.ConfigurationError)) as caught:
        source_generator._materialize_synthesis_plan(draft_plan)

    message = str(caught.value)
    assert file_spec["filename"] in message
    if invalid_value is not None:
        assert invalid_value in message
    after = {
        path.relative_to(source_generator.output_dir): path.read_bytes()
        for path in source_generator.output_dir.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_eight_sources_materialize_without_truncation(source_generator, draft_plan):
    draft_plan["dataset_structure"]["01_foundations"]["files"][0]["source_ids"] = [
        8,
        7,
        6,
        5,
        4,
        3,
        2,
        1,
    ]
    plan = source_generator._materialize_synthesis_plan(draft_plan)
    assert plan["dataset_structure"]["01_foundations"]["files"][0]["source_paths"] == (
        list(reversed(SOURCE_PATHS))
    )
    source_generator._validate_synthesis_plan_contract(plan)


@pytest.mark.parametrize("problem", ["manifest", "eligibility", "missing", "unreadable"])
def test_existing_source_validation_still_rejects_invalid_materialized_sources(
    stage_modules, source_generator, draft_plan, problem
):
    stage3, _ = stage_modules
    plan = source_generator._materialize_synthesis_plan(draft_plan)
    source_path = "virtual-environments.md"
    document = source_generator.docs_dir / "normalized-2.md"

    if problem == "manifest":
        source_generator.files_by_source_path.pop(source_path)
    elif problem == "eligibility":
        source_generator.eligible_source_paths.remove(source_path)
    elif problem == "missing":
        document.unlink()
    else:
        document.write_bytes(b"\xff")

    with pytest.raises(stage3.ConfigurationError) as caught:
        source_generator._validate_synthesis_plan_contract(plan)
    assert source_path in str(caught.value)


def test_invalid_catalog_path_is_not_fuzzily_corrected(stage_modules, source_generator, draft_plan):
    stage3, _ = stage_modules
    source_generator.source_catalog[2]["source_path"] = "tutorial/virtual-environments.md"
    plan = source_generator._materialize_synthesis_plan(draft_plan)
    assert plan["dataset_structure"]["01_foundations"]["files"][0]["source_paths"][0] == (
        "tutorial/virtual-environments.md"
    )
    with pytest.raises(stage3.ConfigurationError, match="absent from the manifest"):
        source_generator._validate_synthesis_plan_contract(plan)


@pytest.mark.parametrize("problem", ["filename", "reference"])
def test_existing_inventory_and_reference_checks_remain_effective(
    stage_modules, source_generator, draft_plan, problem
):
    stage3, _ = stage_modules
    if problem == "filename":
        draft_plan["dataset_structure"]["01_foundations"]["files"][0]["filename"] = "unapproved.md"
    else:
        draft_plan["content_prioritization"]["phase_1_critical"] = ["unapproved.md"]

    plan = source_generator._materialize_synthesis_plan(draft_plan)
    with pytest.raises(stage3.ConfigurationError):
        source_generator._validate_synthesis_plan_contract(plan)
        source_generator._validate_synthesis_plan_references(plan)


def test_stage4_consumes_materialized_paths_in_order(stage_modules, source_generator, draft_plan):
    _, stage4 = stage_modules
    plan = source_generator._materialize_synthesis_plan(draft_plan)
    consumer = stage4.FastAPIDatasetGenerator.__new__(stage4.FastAPIDatasetGenerator)
    consumer.docs_dir = source_generator.docs_dir
    consumer.files_by_source_path = source_generator.files_by_source_path
    consumer.analysis_by_source_path = {
        record["source_path"]: record for record in source_generator.content_analysis
    }

    for section in plan["dataset_structure"].values():
        for file_spec in section["files"]:
            sources = consumer._resolve_sources(file_spec)
            assert [source["source_path"] for source in sources] == [
                "virtual-environments.md",
                "tutorial/body.md",
            ]
            excerpts = consumer._build_excerpts(sources)
            assert excerpts.index("--- SOURCE: virtual-environments.md") < excerpts.index(
                "--- SOURCE: tutorial/body.md"
            )
            assert "Content for source 2." in excerpts
            assert "Content for source 1." in excerpts


def test_preserved_run_materializes_the_exact_virtual_environments_path(stage_modules, draft_plan):
    stage3, _ = stage_modules
    run_dir = PROJECT_ROOT / "data_curation" / "outputs" / "20260921_153155"
    if not run_dir.is_dir() or not stage3.MANIFEST_PATH.is_file():
        pytest.skip("preserved run and normalized corpus are not available")

    generator = stage3.FastAPILearningTaxonomyGenerator(run_dir)
    assert list(generator.source_catalog) == list(range(1, len(generator.source_catalog) + 1))
    assert [source["source_path"] for source in generator.source_catalog.values()] == sorted(
        generator.eligible_source_paths
    )

    source_id = next(
        source_id
        for source_id, source in generator.source_catalog.items()
        if source["source_path"] == "virtual-environments.md"
    )
    for section in draft_plan["dataset_structure"].values():
        for file_spec in section["files"]:
            file_spec["source_ids"] = [source_id]

    plan = generator._materialize_synthesis_plan(draft_plan)
    generator._validate_synthesis_plan_contract(plan)
    generator._validate_synthesis_plan_references(plan)
    for section in plan["dataset_structure"].values():
        for file_spec in section["files"]:
            assert file_spec["source_paths"] == ["virtual-environments.md"]
