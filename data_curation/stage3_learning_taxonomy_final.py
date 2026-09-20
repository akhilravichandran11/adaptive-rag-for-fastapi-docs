"""
Learning Taxonomy & Synthesis Plan Generator
Create structured learning progression and concrete synthetic dataset blueprint for FastAPI developers.
"""

import asyncio
import json
import logging
from pathlib import Path

from config import APPLICATION_NAME, DOCUMENTATION_NAME, DOMAIN_NAME
from dotenv import load_dotenv
from openai_llm_utils import chat_structured
from pipeline import (
    CONTENT_ANALYSIS_FILE,
    FINAL_LEARNING_TAXONOMY_FILE,
    IMPLEMENTATION_ROADMAP_FILE,
    KNOWLEDGE_DOMAINS_FILE,
    SYNTHESIS_PLAN_FILE,
    resolve_run_dir,
)
from schemas import LearningTaxonomy, SynthesisPlan

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
MANIFEST_PATH = PROJECT_ROOT / "data/fastapi/normalized/github-docs/v1/manifest.json"
APPROVED_DATASET_FILES = {
    "01_foundations": (
        "fastapi_core_and_first_api.md",
        "requests_validation_and_responses.md",
    ),
    "02_implementation": (
        "application_structure_and_dependencies.md",
        "data_models_and_crud.md",
        "testing_debugging_and_errors.md",
    ),
    "03_security_production": (
        "security_and_authentication.md",
        "deployment_and_operations.md",
    ),
}
ELIGIBILITY_THRESHOLD = 7.5
MAX_SOURCE_PATHS = 8

# Load environment variables
load_dotenv(PROJECT_ROOT / ".env")


class ConfigurationError(RuntimeError):
    """Pipeline setup problem that should stop the stage before API calls."""


class FastAPILearningTaxonomyGenerator:
    def __init__(self, run_dir: Path):
        self.output_dir = run_dir
        self.manifest_path = MANIFEST_PATH
        self.docs_dir = self.manifest_path.parent / "docs"

        self.knowledge_domains = self._load_json(
            self.output_dir / KNOWLEDGE_DOMAINS_FILE,
            "Knowledge domains",
            dict,
        )
        self.content_analysis = self._load_json(
            self.output_dir / CONTENT_ANALYSIS_FILE,
            "Content analysis",
            list,
        )
        manifest = self._load_json(
            self.manifest_path,
            "Normalized-doc manifest",
            dict,
        )
        self.files_by_source_path = self._index_manifest(manifest)
        self.source_candidates = self._build_source_candidates()
        self.eligible_source_paths = {
            candidate["source_path"] for candidate in self.source_candidates
        }

    def _load_json(
        self,
        path: Path,
        label: str,
        expected_type: type,
    ) -> list | dict:
        """Load one required local JSON input before either Stage 3 API call."""
        if not path.is_file():
            raise ConfigurationError(f"{label} artifact not found: {path}")

        logger.info(f"Loading {label.lower()} from {path}")
        try:
            with path.open("r", encoding="utf-8") as input_file:
                payload = json.load(input_file)
        except json.JSONDecodeError as error:
            raise ConfigurationError(
                f"{label} artifact is not valid JSON: {path}: {error}"
            ) from error
        except (OSError, UnicodeError) as error:
            raise ConfigurationError(
                f"Could not read {label.lower()} artifact: {path}: {error}"
            ) from error

        if not isinstance(payload, expected_type):
            expected_shape = "array" if expected_type is list else "object"
            raise ConfigurationError(
                f"{label} artifact must contain a JSON {expected_shape}: {path}"
            )

        return payload

    def _index_manifest(self, manifest: dict) -> dict[str, dict]:
        """Validate and index normalized-document manifest entries."""
        manifest_files = manifest.get("files")
        if not isinstance(manifest_files, list) or not manifest_files:
            raise ConfigurationError(
                "Normalized-doc manifest must contain a non-empty 'files' JSON array: "
                f"{self.manifest_path}"
            )

        files_by_source_path: dict[str, dict] = {}
        for index, entry in enumerate(manifest_files):
            if not isinstance(entry, dict):
                raise ConfigurationError(
                    f"Normalized-doc manifest entry #{index} must be a JSON object: "
                    f"{self.manifest_path}"
                )

            source_path = entry.get("source_path")
            output_filename = entry.get("output_filename")
            if not isinstance(source_path, str) or not source_path:
                raise ConfigurationError(
                    f"Normalized-doc manifest entry #{index} must contain a non-empty "
                    f"'source_path' string: {self.manifest_path}"
                )
            if not isinstance(output_filename, str) or not output_filename:
                raise ConfigurationError(
                    f"Normalized-doc manifest entry #{index} must contain a non-empty "
                    f"'output_filename' string: {self.manifest_path}"
                )
            if source_path in files_by_source_path:
                raise ConfigurationError(
                    f"Normalized-doc manifest contains duplicate source_path "
                    f"{source_path!r}: {self.manifest_path}"
                )

            files_by_source_path[source_path] = entry

        return files_by_source_path

    def _build_source_candidates(self) -> list[dict]:
        """Build compact metadata for eligible, manifest-backed Stage 1 records."""
        if not self.content_analysis:
            raise ConfigurationError(
                "Content analysis artifact must contain at least one record: "
                f"{self.output_dir / CONTENT_ANALYSIS_FILE}"
            )

        candidates = []
        seen_source_paths: set[str] = set()

        for index, record in enumerate(self.content_analysis):
            if not isinstance(record, dict):
                raise ConfigurationError(f"Content analysis entry #{index} must be a JSON object")

            source_path = record.get("source_path")
            if not isinstance(source_path, str) or not source_path:
                raise ConfigurationError(
                    f"Content analysis entry #{index} must contain a non-empty 'source_path' string"
                )
            if source_path in seen_source_paths:
                raise ConfigurationError(
                    f"Content analysis contains duplicate source_path {source_path!r}"
                )
            seen_source_paths.add(source_path)

            if "error" in record:
                continue

            scores = record.get("analysis")
            if not isinstance(scores, dict):
                raise ConfigurationError(
                    f"Content analysis record {source_path!r} must contain an "
                    "'analysis' JSON object"
                )

            quality_score = scores.get("overall_quality")
            if isinstance(quality_score, bool) or not isinstance(
                quality_score,
                (int, float),
            ):
                raise ConfigurationError(
                    f"Content analysis record {source_path!r} must contain a numeric "
                    "'analysis.overall_quality' value"
                )

            suspected_injection = record.get("suspected_prompt_injection")
            if not isinstance(suspected_injection, bool):
                raise ConfigurationError(
                    f"Content analysis record {source_path!r} must contain a boolean "
                    "'suspected_prompt_injection' value"
                )

            if suspected_injection or quality_score < ELIGIBILITY_THRESHOLD:
                continue

            manifest_entry = self.files_by_source_path.get(source_path)
            if manifest_entry is None:
                raise ConfigurationError(
                    f"Eligible content analysis source is absent from the manifest: {source_path!r}"
                )

            if record.get("output_filename") != manifest_entry["output_filename"]:
                raise ConfigurationError(
                    f"Eligible content analysis source {source_path!r} does not match "
                    "the manifest output_filename"
                )

            content_type = record.get("content_type")
            target_audience = record.get("target_audience")
            key_concepts = record.get("key_concepts")
            summary = record.get("one_sentence_summary")

            if not isinstance(content_type, str) or not content_type:
                raise ConfigurationError(
                    f"Eligible content analysis source {source_path!r} must contain "
                    "a non-empty 'content_type' string"
                )
            if not isinstance(target_audience, str) or not target_audience:
                raise ConfigurationError(
                    f"Eligible content analysis source {source_path!r} must contain "
                    "a non-empty 'target_audience' string"
                )
            if (
                not isinstance(key_concepts, list)
                or not key_concepts
                or not all(isinstance(concept, str) and concept for concept in key_concepts)
            ):
                raise ConfigurationError(
                    f"Eligible content analysis source {source_path!r} must contain "
                    "a non-empty string list in 'key_concepts'"
                )
            if not isinstance(summary, str) or not summary:
                raise ConfigurationError(
                    f"Eligible content analysis source {source_path!r} must contain "
                    "a non-empty 'one_sentence_summary' string"
                )

            self._validate_normalized_source(source_path)
            candidates.append(
                {
                    "source_path": source_path,
                    "quality_score": quality_score,
                    "content_type": content_type,
                    "target_audience": target_audience,
                    "key_concepts": key_concepts,
                    "one_sentence_summary": summary,
                }
            )

        if not candidates:
            raise ConfigurationError(
                "Content analysis contains no eligible, manifest-backed source candidates"
            )

        return candidates

    def _validate_normalized_source(self, source_path: str) -> None:
        """Require the manifest's exact output filename to resolve to a readable file."""
        manifest_entry = self.files_by_source_path[source_path]
        source_file = self.docs_dir / manifest_entry["output_filename"]

        try:
            source_file.resolve().relative_to(self.docs_dir.resolve())
        except ValueError as error:
            raise ConfigurationError(
                f"Manifest output_filename for {source_path!r} resolves outside "
                f"the normalized docs directory: {source_file}"
            ) from error

        if not source_file.is_file():
            raise ConfigurationError(
                f"Normalized document not found for {source_path!r}: {source_file}"
            )

        try:
            with source_file.open("r", encoding="utf-8") as input_file:
                # Read in bounded chunks so UTF-8 errors surface without retaining the body.
                while input_file.read(8192):
                    pass
        except (OSError, UnicodeError) as error:
            raise ConfigurationError(
                f"Normalized document is not readable for {source_path!r}: {source_file}: {error}"
            ) from error

    async def create_learning_taxonomy(self) -> dict:
        """Create comprehensive learning taxonomy for FastAPI developers"""

        taxonomy_prompt = f"""
You are an expert curriculum designer and technical educator specializing in developer education.

Based on comprehensive analysis of {DOCUMENTATION_NAME} and real-world developer needs, create a structured learning taxonomy for developers wanting to master {DOMAIN_NAME}.

IMPORTANT: Respond ONLY with valid JSON. Do not include any explanatory text, markdown formatting, or other content. Just return the JSON object.

CONTEXT:
- FastAPI is a modern Python web framework for building APIs with type hints.
- Key concepts include path operations, request/response models, validation, dependency injection, routing, middleware, security, testing, OpenAPI generation, and deployment.
- Major developer needs include practical implementation guidance, correct API usage, debugging common errors, understanding when to use framework features, and production readiness.
- This taxonomy supports a {APPLICATION_NAME}.

KNOWLEDGE DOMAIN ANALYSIS:
{json.dumps(self.knowledge_domains.get("knowledge_domains", {}), indent=2)}

IDENTIFIED CONTENT GAPS:
{json.dumps(self.knowledge_domains.get("content_gaps", {}), indent=2)}

LEARNING PROGRESSION NEEDS:
- Beginners: Need FastAPI basics, app structure, path operations, validation, and request/response handling
- Intermediate: Dependencies, routers, error handling, testing, security, and database/application patterns
- Advanced: OpenAPI customization, middleware, deployment, performance, production structure, and edge cases

Create a learning taxonomy that addresses these needs:

{{
  "learning_levels": {{
    "foundational": {{
      "description": "Understanding FastAPI fundamentals, app structure, and core request/response flow",
      "learning_objectives": ["objective1", "objective2"],
      "core_topics": ["topic1", "topic2"],
      "estimated_time": "X hours",
      "prerequisites": ["prereq1", "prereq2"],
      "outcomes": ["outcome1", "outcome2"]
    }},
    "functional": {{
      "description": "Building useful FastAPI applications with validation, dependencies, routers, and tests",
      "learning_objectives": ["objective1", "objective2"],
      "core_topics": ["topic1", "topic2"],
      "estimated_time": "X hours",
      "prerequisites": ["prereq1", "prereq2"],
      "outcomes": ["outcome1", "outcome2"]
    }},
    "proficient": {{
      "description": "Production deployment, security, OpenAPI customization, and advanced FastAPI patterns",
      "learning_objectives": ["objective1", "objective2"],
      "core_topics": ["topic1", "topic2"],
      "estimated_time": "X hours",
      "prerequisites": ["prereq1", "prereq2"],
      "outcomes": ["outcome1", "outcome2"]
    }}
  }},
  "learning_sequences": {{
    "rapid_start": {{
      "description": "Fastest path to building working FastAPI endpoints",
      "target_audience": "Developers who want to get started quickly",
      "sequence": ["step1", "step2", "step3"],
      "estimated_time": "X hours"
    }},
    "comprehensive": {{
      "description": "Complete FastAPI mastery including security, testing, and production deployment",
      "target_audience": "Developers building production FastAPI systems",
      "sequence": ["step1", "step2", "step3"],
      "estimated_time": "X hours"
    }},
    "security_focused": {{
      "description": "Security-first approach to FastAPI development",
      "target_audience": "Security-conscious developers and enterprise teams",
      "sequence": ["step1", "step2", "step3"],
      "estimated_time": "X hours"
    }}
  }},
  "assessment_checkpoints": {{
    "foundational": ["Can create a basic FastAPI app", "Can define path operations and request/response models"],
    "functional": ["Can structure routers and dependencies", "Can test and debug common FastAPI behavior"],
    "proficient": ["Can design secure FastAPI applications", "Can deploy and operate FastAPI services"]
  }},
  "common_misconceptions": [
    "misconception and correction pairs"
  ],
  "real_world_applications": [
    "practical use cases and implementation approaches"
  ]
}}

Focus on practical, hands-on learning that addresses implementation, debugging, security, testing, and production deployment gaps identified in current FastAPI education materials.
"""

        try:
            taxonomy = await chat_structured(taxonomy_prompt, LearningTaxonomy, max_tokens=3000)
            return taxonomy.model_dump()
        except Exception as e:  # noqa: BLE001  (one bad taxonomy call shouldn't kill the stage)
            logger.error(f"Error creating learning taxonomy: {str(e)}")
            return {"error": str(e)}

    async def create_synthesis_plan(self) -> dict:
        """Create concrete plan for synthetic dataset creation"""

        approved_inventory = {
            category: list(filenames) for category, filenames in APPROVED_DATASET_FILES.items()
        }
        synthesis_prompt = f"""
You are a technical curriculum architect creating a concrete implementation plan for a FastAPI developer education dataset.

Based on the knowledge domain analysis, identified gaps, and eligible current-run sources, create a detailed synthesis plan for generating high-quality synthetic dataset files.

IMPORTANT: Respond ONLY with valid JSON. Do not include any explanatory text, markdown formatting, or other content. Just return the JSON object.

KNOWLEDGE GAPS TO ADDRESS:
{json.dumps(self.knowledge_domains.get("content_gaps", {}), indent=2)}

SYNTHESIS OPPORTUNITIES:
{json.dumps(self.knowledge_domains.get("synthesis_opportunities", {}), indent=2)}

ELIGIBLE CURRENT-RUN SOURCE METADATA:
{json.dumps(self.source_candidates, indent=2)}

APPROVED DATASET CONTRACT:
{json.dumps(approved_inventory, indent=2)}

- dataset_structure must contain exactly these three category aliases in this order.
- Each category must contain exactly the listed filenames in the listed order.
- The distribution must be exactly 2 foundations, 3 implementation, and 2 security/production files.
- Do not add, remove, rename, or duplicate a planned filename.
- Every planned file must declare source_paths with 1 through 8 paths selected only from ELIGIBLE CURRENT-RUN SOURCE METADATA.
- Preserve source_paths order and do not repeat a path within one planned file.
- A source path may be reused by a different planned file when it supports both.
- Use only the compact metadata above. Do not request or assume normalized document bodies.

REAL-WORLD FASTAPI DEVELOPER NEEDS:
- Practical implementation guidance for common API tasks
- Clear explanations of request validation, response models, dependencies, routers, and error handling
- Security, authentication, testing, and deployment patterns
- When to use specific FastAPI features versus simpler alternatives
- Comprehensive troubleshooting for common issues and framework behavior

FILENAME CONSISTENCY REQUIREMENTS:
- Treat dataset_structure as the authoritative planned-file inventory.
- Every content_prioritization entry must exactly match a filename in dataset_structure.
- Every combine_scattered_info target_file must exactly match a filename in dataset_structure.
- Every fill_knowledge_gaps files_to_create entry must exactly match a filename in dataset_structure.
- Do not reference future, backlog, or suggested files outside dataset_structure.

For every dataset_structure file object, provide:
- filename
- description
- content_focus
- source_paths
- target_audience
- estimated_length
- key_sections

Use this exact content-prioritization inventory:
{{
  "phase_1_critical": [
    "fastapi_core_and_first_api.md",
    "requests_validation_and_responses.md"
  ],
  "phase_2_important": [
    "application_structure_and_dependencies.md",
    "data_models_and_crud.md",
    "testing_debugging_and_errors.md"
  ],
  "phase_3_advanced": [
    "security_and_authentication.md",
    "deployment_and_operations.md"
  ]
}}

Also provide synthesis_strategies, quality_criteria, and success_metrics using only the approved seven filenames.

Focus on creating a dataset that transforms current scattered FastAPI knowledge into a coherent, practical education system.
"""

        try:
            synthesis_plan = await chat_structured(
                synthesis_prompt,
                SynthesisPlan,
                max_tokens=4000,
            )
            return synthesis_plan.model_dump(by_alias=True)
        except Exception as e:  # noqa: BLE001  (one bad synthesis call shouldn't kill the stage)
            logger.error(f"Error creating synthesis plan: {str(e)}")
            return {"error": str(e)}

    def _validate_synthesis_plan_contract(self, synthesis_plan: dict) -> None:
        """Reject plans outside the approved taxonomy and source contract."""
        dataset_structure = synthesis_plan.get("dataset_structure")
        if not isinstance(dataset_structure, dict):
            raise ConfigurationError(
                "Synthesis plan must contain a 'dataset_structure' JSON object"
            )

        expected_categories = list(APPROVED_DATASET_FILES)
        actual_categories = list(dataset_structure)
        if actual_categories != expected_categories:
            raise ConfigurationError(
                "Synthesis plan categories do not match the approved order: "
                f"expected={expected_categories}, actual={actual_categories}"
            )

        declared_filenames = []
        for category, expected_filenames in APPROVED_DATASET_FILES.items():
            section = dataset_structure[category]
            if not isinstance(section, dict):
                raise ConfigurationError(
                    f"Synthesis plan category {category!r} must be a JSON object"
                )

            files = section.get("files")
            if not isinstance(files, list):
                raise ConfigurationError(
                    f"Synthesis plan category {category!r} must contain a 'files' JSON array"
                )

            actual_filenames = []
            for index, file_spec in enumerate(files):
                if not isinstance(file_spec, dict):
                    raise ConfigurationError(
                        f"Synthesis plan file {category}[{index}] must be a JSON object"
                    )

                filename = file_spec.get("filename")
                if not isinstance(filename, str) or not filename:
                    raise ConfigurationError(
                        f"Synthesis plan file {category}[{index}] must contain "
                        "a non-empty 'filename' string"
                    )

                actual_filenames.append(filename)
                declared_filenames.append(filename)
                self._validate_source_paths(filename, file_spec.get("source_paths"))

            if tuple(actual_filenames) != expected_filenames:
                raise ConfigurationError(
                    f"Synthesis plan filenames for {category!r} do not match "
                    f"the approved contract: expected={list(expected_filenames)}, "
                    f"actual={actual_filenames}"
                )

        duplicate_filenames = sorted(
            {filename for filename in declared_filenames if declared_filenames.count(filename) > 1}
        )
        if duplicate_filenames:
            raise ConfigurationError(
                f"Synthesis plan contains duplicate filenames: {duplicate_filenames}"
            )

    def _validate_source_paths(
        self,
        filename: str,
        source_paths: object,
    ) -> None:
        """Validate one planned file's ordered current-run source list."""
        if not isinstance(source_paths, list):
            raise ConfigurationError(
                f"Synthesis plan file {filename!r} must contain a 'source_paths' JSON array"
            )

        if not 1 <= len(source_paths) <= MAX_SOURCE_PATHS:
            raise ConfigurationError(
                f"Synthesis plan file {filename!r} must declare 1 through "
                f"{MAX_SOURCE_PATHS} source paths; got {len(source_paths)}"
            )

        if not all(isinstance(source_path, str) and source_path for source_path in source_paths):
            raise ConfigurationError(
                f"Synthesis plan file {filename!r} source_paths must contain non-empty strings"
            )

        duplicate_paths = sorted(
            {source_path for source_path in source_paths if source_paths.count(source_path) > 1}
        )
        if duplicate_paths:
            raise ConfigurationError(
                f"Synthesis plan file {filename!r} contains duplicate source paths: "
                f"{duplicate_paths}"
            )

        for source_path in source_paths:
            if source_path not in self.files_by_source_path:
                raise ConfigurationError(
                    f"Synthesis plan file {filename!r} references a source absent "
                    f"from the manifest: {source_path!r}"
                )
            if source_path not in self.eligible_source_paths:
                raise ConfigurationError(
                    f"Synthesis plan file {filename!r} references a source without "
                    f"an eligible current-run Stage 1 record: {source_path!r}"
                )

            self._validate_normalized_source(source_path)

    def _validate_synthesis_plan_references(self, synthesis_plan: dict) -> None:
        """Reject filename references outside the planned dataset structure."""
        dataset_structure = synthesis_plan.get("dataset_structure", {})
        declared_filenames = {
            file_spec["filename"]
            for section in dataset_structure.values()
            for file_spec in section.get("files", [])
        }

        references: list[tuple[str, str]] = []
        for phase, filenames in synthesis_plan.get("content_prioritization", {}).items():
            references.extend(
                (f"content_prioritization.{phase}[{index}]", filename)
                for index, filename in enumerate(filenames)
            )

        strategies = synthesis_plan.get("synthesis_strategies", {})
        for index, strategy in enumerate(strategies.get("combine_scattered_info", [])):
            references.append(
                (
                    f"synthesis_strategies.combine_scattered_info[{index}].target_file",
                    strategy["target_file"],
                )
            )

        for strategy_index, strategy in enumerate(strategies.get("fill_knowledge_gaps", [])):
            references.extend(
                (
                    "synthesis_strategies.fill_knowledge_gaps"
                    f"[{strategy_index}].files_to_create[{file_index}]",
                    filename,
                )
                for file_index, filename in enumerate(strategy["files_to_create"])
            )

        undeclared = sorted(
            (field_path, filename)
            for field_path, filename in references
            if filename not in declared_filenames
        )
        if undeclared:
            details = ", ".join(f"{field_path}={filename!r}" for field_path, filename in undeclared)
            raise ConfigurationError(
                f"Synthesis plan references files not declared in dataset_structure: {details}"
            )

    def generate_implementation_roadmap(self, taxonomy: dict, synthesis_plan: dict) -> dict:
        """Generate concrete implementation roadmap"""

        # One spec per phase: which dataset section supplies its deliverables and
        # which taxonomy level supplies its success criteria.
        phase_specs = [
            {
                "key": "phase_1_foundation",  # gitleaks:allow
                "source": "01_foundations",
                "taxonomy_level": "foundational",
                "duration": "1-2 days",
                "description": "Create core conceptual files that answer 'what/why/how' of FastAPI",
            },
            {
                "key": "phase_2_implementation",  # gitleaks:allow
                "source": "02_implementation",
                "taxonomy_level": "functional",
                "duration": "2-3 days",
                "description": "Create practical implementation guides with working examples",
            },
            {
                "key": "phase_3_production",
                "source": "03_security_production",
                "taxonomy_level": "proficient",
                "duration": "2-3 days",
                "description": "Create advanced guides for production deployment and optimization",
            },
        ]

        dataset_structure = synthesis_plan.get("dataset_structure", {})
        planned_file_count = sum(
            len(section.get("files", [])) for section in dataset_structure.values()
        )
        implementation_phases = {}
        for spec in phase_specs:
            section = dataset_structure.get(spec["source"], {})
            implementation_phases[spec["key"]] = {
                "duration": spec["duration"],
                "description": spec["description"],
                "deliverables": [f["filename"] for f in section.get("files", [])],
                "success_criteria": "; ".join(
                    taxonomy["assessment_checkpoints"][spec["taxonomy_level"]]
                ),
            }

        roadmap = {
            "project_overview": {
                "mission": "Transform scattered FastAPI knowledge into a coherent, practical education system",
                "target_outcome": "Developers can build production-ready, secure FastAPI applications",
                "dataset_size": f"{planned_file_count} planned markdown files",
                "expected_improvement": (
                    "Not measured; requires a recorded RAG retrieval baseline"
                ),
            },
            "implementation_phases": implementation_phases,
            "quality_assurance": {
                "content_validation": (
                    "Review each generated file for technical accuracy and completeness"
                ),
                "rag_testing": ("Test generated files in the RAG pipeline for retrieval quality"),
                "developer_feedback": ("Validate sample generated files with the target audience"),
            },
            "expected_outcomes": {
                "rag_performance": "Higher precision and recall in FastAPI-related queries",
                "learning_effectiveness": "Faster time-to-competency for FastAPI developers",
                "security_posture": "Reduced security and validation mistakes in FastAPI implementations",
                "ecosystem_growth": "More high-quality FastAPI implementations in the wild",
            },
        }

        return roadmap

    async def run_taxonomy_generation(self):
        """Main workflow for generating learning taxonomy and synthesis plan"""

        logger.info(
            f"Starting {DOCUMENTATION_NAME} learning taxonomy and synthesis plan generation..."
        )

        if not self.knowledge_domains:
            logger.error("No knowledge domains found. Run knowledge domain extraction first.")
            return

        # Generate learning taxonomy
        logger.info("Creating learning taxonomy...")
        taxonomy = await self.create_learning_taxonomy()

        if "error" in taxonomy:
            logger.error(f"Taxonomy generation failed: {taxonomy['error']}")
            return

        # Generate synthesis plan
        logger.info("Creating synthesis plan...")
        synthesis_plan = await self.create_synthesis_plan()

        if "error" in synthesis_plan:
            logger.error(f"Synthesis plan generation failed: {synthesis_plan['error']}")
            return

        self._validate_synthesis_plan_contract(synthesis_plan)
        self._validate_synthesis_plan_references(synthesis_plan)

        # Generate implementation roadmap
        logger.info("Creating implementation roadmap...")
        roadmap = self.generate_implementation_roadmap(taxonomy, synthesis_plan)

        # Save results in the supplied run directory under fixed artifact names.
        artifacts = {
            FINAL_LEARNING_TAXONOMY_FILE: taxonomy,
            SYNTHESIS_PLAN_FILE: synthesis_plan,
            IMPLEMENTATION_ROADMAP_FILE: roadmap,
        }
        for filename, payload in artifacts.items():
            output_path = self.output_dir / filename
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            logger.info(f"Saved {output_path}")

        # Assemble a short human summary line-by-line, then emit it once.
        summary_lines = ["\n🎓 LEARNING TAXONOMY GENERATED"]
        summary_lines += [
            f"📚 {level.title()}: {details.get('estimated_time', 'TBD')}"
            for level, details in taxonomy.get("learning_levels", {}).items()
        ]
        if "dataset_structure" in synthesis_plan:
            file_count = sum(
                len(s.get("files", [])) for s in synthesis_plan["dataset_structure"].values()
            )
            summary_lines.append(f"📁 Planned dataset: {file_count} synthetic files")
        if "implementation_phases" in roadmap:
            summary_lines.append(
                f"🚀 Implementation phases: {len(roadmap['implementation_phases'])} phases"
            )
        summary_lines.append("\n✅ Results saved to outputs/")
        print("\n".join(summary_lines))

        return taxonomy, synthesis_plan, roadmap


async def main(run_dir: Path):
    """Run the learning taxonomy generation"""

    generator = FastAPILearningTaxonomyGenerator(run_dir)
    return await generator.run_taxonomy_generation()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate the final learning taxonomy for a knowledge-domain run."
    )
    parser.add_argument(
        "--run-id",
        required=True,
        metavar="ID",
        help="Existing knowledge-domain run id under data_curation/outputs.",
    )
    args = parser.parse_args()

    run_dir = resolve_run_dir(args.run_id)
    try:
        asyncio.run(main(run_dir))
    except ConfigurationError as error:
        logger.error("%s", error)
        raise SystemExit(1) from error
