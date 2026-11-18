"""
Knowledge Domain Mapping Script
Extract and map FastAPI concepts, relationships, and knowledge domains from high-quality content.
"""

import asyncio
import json
import logging
import os
import unicodedata
from itertools import batched
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI
from tqdm import tqdm

from data_curation.pipeline import (
    CONTENT_ANALYSIS_FILE,
    KNOWLEDGE_DOMAINS_FILE,
    LEARNING_TAXONOMY_FILE,
    resolve_run_dir,
)

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

APPLICATION_NAME = "FastAPI Documentation Q&A RAG system"
DOCUMENTATION_NAME = "FastAPI documentation"
DOMAIN_NAME = "FastAPI implementation and concepts"

# Load environment variables
load_dotenv(PROJECT_ROOT / ".env")


class ConfigurationError(RuntimeError):
    """Pipeline setup problem that should stop the stage before API calls."""


class FastAPIKnowledgeMapper:
    def __init__(self, run_dir: Path):
        self.client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.manifest_path = PROJECT_ROOT / "data/fastapi/normalized/github-docs/v1/manifest.json"
        self.docs_dir = self.manifest_path.parent / "docs"
        self.output_dir = run_dir

        with self.manifest_path.open("r", encoding="utf-8") as manifest_file:
            manifest = json.load(manifest_file)

        self.files_by_source_path = {
            file_entry["source_path"]: file_entry for file_entry in manifest["files"]
        }

        # Load previous analysis results
        self.content_analysis = self._load_content_analysis()
        self.high_quality_files = self._get_high_quality_files()

    def _load_content_analysis(self) -> list[dict]:
        """Load previous content analysis results"""
        analysis_file = self.output_dir / CONTENT_ANALYSIS_FILE
        if not analysis_file.is_file():
            raise ConfigurationError(f"Content analysis artifact not found: {analysis_file}")

        logger.info(f"Loading content analysis from {analysis_file}")
        try:
            with analysis_file.open("r", encoding="utf-8") as input_file:
                analyses = json.load(input_file)
        except json.JSONDecodeError as error:
            raise ConfigurationError(
                f"Content analysis artifact is not valid JSON: {analysis_file}: {error}"
            ) from error

        if not isinstance(analyses, list):
            raise ConfigurationError(
                f"Content analysis artifact must contain a JSON list: {analysis_file}"
            )

        return analyses

    def _get_high_quality_files(self) -> list[dict]:
        """Filter to high-quality files (7.5+ score)"""
        return [
            analysis
            for analysis in self.content_analysis
            if "error" not in analysis
            and not analysis.get("suspected_prompt_injection", False)
            and analysis.get("analysis", {}).get("overall_quality", 0) >= 7.5
        ]

    def _strip_code_fence(self, text: str) -> str:
        text = text.strip()
        return text.removeprefix("```json").removesuffix("```")

    def _prepare_batch_item(self, file_info: dict) -> dict | None:
        source_path = file_info["source_path"]
        file_path = self._get_file_path(source_path)

        if file_path is None:
            return None

        try:
            content = file_path.read_text(encoding="utf-8")

            return {
                "source_path": source_path,
                "content": content[:3000],
                "quality_score": file_info["analysis"]["overall_quality"],
                "content_type": file_info.get("content_type", "unknown"),
                "key_concepts": file_info.get("key_concepts", []),
            }
        except Exception as error:  # noqa: BLE001  (one bad file shouldn't kill the batch)
            logger.warning(f"Could not read {source_path}: {error}")
            return None

    async def extract_knowledge_domains(self, file_batch: list[dict]) -> dict:
        """Extract knowledge domains from a batch of high-quality files"""

        # Prepare batch content for analysis
        batch_content = []

        for file_info in file_batch:
            batch_item = self._prepare_batch_item(file_info)

            if batch_item is not None:
                batch_content.append(batch_item)

        if not batch_content:
            return {"error": "No readable files found in this batch"}

        # Create knowledge extraction prompt
        knowledge_prompt = f"""
You are an expert in technical documentation analysis and knowledge architecture. Analyze this batch of high-quality {DOCUMENTATION_NAME} files to extract knowledge domains for a {APPLICATION_NAME}.

BATCH SIZE: {len(batch_content)} files
AVERAGE QUALITY: {sum(f["quality_score"] for f in batch_content) / len(batch_content):.1f}/10

From these files, extract:

1. **CORE CONCEPTS**: Fundamental FastAPI ideas that developers must understand
   - What are the essential building blocks?
   - What are the key abstractions and mental models?

2. **IMPLEMENTATION PATTERNS**: How things are actually built in FastAPI
   - Common implementation approaches
   - Code patterns and templates
   - Best practices and conventions

3. **API AND FRAMEWORK DETAILS**: Technical behavior and framework mechanics
   - Path operations, request/response handling, validation, dependency injection, and OpenAPI behavior
   - Important classes, functions, decorators, and parameters
   - Error handling patterns

4. **INTEGRATION SCENARIOS**: How FastAPI fits into real applications
   - Database, authentication, background task, testing, deployment, and client integration patterns
   - Application structure and operational scenarios
   - Common use cases

5. **TROUBLESHOOTING KNOWLEDGE**: Common problems and solutions
   - Error patterns and debugging approaches
   - Performance considerations
   - Security issues and mitigations

For each domain, identify:
- **Essential topics** (must-know for basic FastAPI development)
- **Advanced topics** (for sophisticated implementations)
- **Prerequisites** (what you need to know first)
- **Learning sequence** (optimal order to learn topics)

FILES TO ANALYZE:
{json.dumps([{"source_path": f["source_path"], "key_concepts": f["key_concepts"], "content_type": f["content_type"], "content": f["content"]} for f in batch_content], indent=2)}

Respond with valid JSON in this format:
{{
  "knowledge_domains": {{
    "core_concepts": {{
      "essential": ["concept1", "concept2"],
      "advanced": ["concept3", "concept4"],
      "prerequisites": ["prereq1", "prereq2"]
    }},
    "implementation_patterns": {{
      "essential": ["pattern1", "pattern2"],
      "advanced": ["pattern3", "pattern4"],
      "prerequisites": ["prereq1", "prereq2"]
    }},
    "api_and_framework_details": {{
      "essential": ["detail1", "detail2"],
      "advanced": ["detail3", "detail4"],
      "prerequisites": ["prereq1", "prereq2"]
    }},
    "integration_scenarios": {{
      "essential": ["scenario1", "scenario2"],
      "advanced": ["scenario3", "scenario4"],
      "prerequisites": ["prereq1", "prereq2"]
    }},
    "troubleshooting": {{
      "essential": ["issue1", "issue2"],
      "advanced": ["issue3", "issue4"],
      "prerequisites": ["prereq1", "prereq2"]
    }}
  }},
  "concept_relationships": {{
    "foundational_flow": ["concept A leads to concept B"],
    "dependency_chains": ["X requires Y requires Z"],
    "parallel_topics": ["topics that can be learned together"]
  }},
  "learning_paths": {{
    "beginner_to_basic": ["step1", "step2", "step3"],
    "basic_to_intermediate": ["step4", "step5", "step6"],
    "intermediate_to_advanced": ["step7", "step8", "step9"]
  }},
  "content_gaps": {{
    "missing_beginner_content": ["gap1", "gap2"],
    "missing_practical_examples": ["gap3", "gap4"],
    "missing_integration_guides": ["gap5", "gap6"]
  }},
  "synthesis_opportunities": {{
    "combine_scattered_info": ["opportunity1", "opportunity2"],
    "create_complete_tutorials": ["tutorial1", "tutorial2"],
    "fill_implementation_gaps": ["gap1", "gap2"]
  }}
}}
"""

        try:
            response = await self.client.chat.completions.create(
                model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                messages=[{"role": "user", "content": knowledge_prompt}],
                temperature=0.1,
                max_tokens=2000,
            )

            # Parse JSON response and Clean up markdown formatting
            result_text = self._strip_code_fence(response.choices[0].message.content)

            knowledge_domains = json.loads(result_text)

            quality_scores = [item["quality_score"] for item in batch_content]

            content_types = {item["content_type"] for item in batch_content}

            knowledge_domains["batch_info"] = {
                "files_analyzed": len(batch_content),
                "average_quality": sum(quality_scores) / len(quality_scores),
                "content_types": list(content_types),
            }

            return knowledge_domains

        except Exception as e:  # noqa: BLE001  (one bad file shouldn't kill the batch)
            logger.error(f"Error extracting knowledge domains: {str(e)}")
            return {"error": str(e)}

    def _get_file_path(self, source_path: str) -> Path | None:
        """Get the normalized file path for a manifest source path."""
        file_entry = self.files_by_source_path.get(source_path)
        if not file_entry:
            return None

        file_path = self.docs_dir / file_entry["output_filename"]
        return file_path if file_path.exists() else None

    async def map_all_knowledge(self) -> dict:
        """Process all high-quality files in batches to extract knowledge domains"""

        logger.info(
            f"Processing {len(self.high_quality_files)} high-quality files for knowledge mapping..."
        )

        # Process files in batches to stay within context limits
        # batch_size = 8
        batch_size = 5
        file_batches = [list(batch) for batch in batched(self.high_quality_files, batch_size)]

        all_knowledge_extractions = []

        for batch in tqdm(
            file_batches,
            desc="Extracting knowledge domains",
        ):
            knowledge_extraction = await self.extract_knowledge_domains(batch)

            if "error" not in knowledge_extraction:
                all_knowledge_extractions.append(knowledge_extraction)

            # Rate limiting pause
            await asyncio.sleep(1.0)

        # Synthesize all extractions into unified knowledge map
        unified_knowledge = self._synthesize_knowledge_extractions(all_knowledge_extractions)

        return unified_knowledge

    def _merge_unique(self, target: list, incoming: list) -> None:
        """Append new items from `incoming` to `target`, preserving order."""
        existing = set(target)
        for item in incoming:
            if item not in existing:
                target.append(item)

    @staticmethod
    def _concept_comparison_key(concept: str) -> str:
        """Return the mechanical comparison key for a concept."""
        normalized = unicodedata.normalize("NFKC", concept).casefold()
        normalized_characters = [
            character if character.isalnum() else " " for character in normalized
        ]
        return " ".join("".join(normalized_characters).split())

    def _deduplicate_concepts(self, concepts: list[str]) -> list[str]:
        """Retain the first concept for each normalized comparison key."""
        seen_keys = set()
        deduplicated = []

        for concept in concepts:
            comparison_key = self._concept_comparison_key(concept)
            if comparison_key not in seen_keys:
                seen_keys.add(comparison_key)
                deduplicated.append(concept)

        return deduplicated

    def _synthesize_knowledge_extractions(self, extractions: list[dict]) -> dict:
        """Combine multiple knowledge extractions into unified knowledge map"""

        if not extractions:
            return {"error": "No valid knowledge extractions"}

        # Initialize the unified structure programmatically from its schema.
        DOMAINS = (
            "core_concepts",
            "implementation_patterns",
            "api_and_framework_details",
            "integration_scenarios",
            "troubleshooting",
        )
        TIERS = ("essential", "advanced", "prerequisites")
        FLAT_SECTIONS = {
            "concept_relationships": ("foundational_flow", "dependency_chains", "parallel_topics"),
            "learning_paths": (
                "beginner_to_basic",
                "basic_to_intermediate",
                "intermediate_to_advanced",
            ),
            "content_gaps": (
                "missing_beginner_content",
                "missing_practical_examples",
                "missing_integration_guides",
            ),
            "synthesis_opportunities": (
                "combine_scattered_info",
                "create_complete_tutorials",
                "fill_implementation_gaps",
            ),
        }

        # Initialize unified structure
        unified = {"knowledge_domains": {d: {t: [] for t in TIERS} for d in DOMAINS}}
        for section, categories in FLAT_SECTIONS.items():
            unified[section] = {c: [] for c in categories}

        # Combine and deduplicate across all extractions
        for extraction in extractions:
            if "knowledge_domains" in extraction:
                for domain, categories in extraction["knowledge_domains"].items():
                    if domain in unified["knowledge_domains"]:
                        for category, items in categories.items():
                            if category in unified["knowledge_domains"][domain]:
                                self._merge_unique(
                                    unified["knowledge_domains"][domain][category], items
                                )

            # Combine other sections similarly
            for section in FLAT_SECTIONS:
                if section in extraction:
                    for category, items in extraction[section].items():
                        if category in unified[section]:
                            self._merge_unique(unified[section][category], items)

        for categories in unified["knowledge_domains"].values():
            for tier in TIERS:
                categories[tier] = self._deduplicate_concepts(categories[tier])

            essential_keys = {
                self._concept_comparison_key(concept) for concept in categories["essential"]
            }
            categories["advanced"] = [
                concept
                for concept in categories["advanced"]
                if self._concept_comparison_key(concept) not in essential_keys
            ]

        # Collect each extraction's batch metadata once
        batch_details = [extraction.get("batch_info", {}) for extraction in extractions]

        content_types = {
            content_type
            for details in batch_details
            for content_type in details.get("content_types", [])
        }

        # Add synthesis metadata
        unified["synthesis_metadata"] = {
            "total_extractions": len(extractions),
            "files_processed": sum(details.get("files_analyzed", 0) for details in batch_details),
            "average_quality": sum(details.get("average_quality", 0) for details in batch_details)
            / len(batch_details),
            "content_types_seen": list(content_types),
        }

        return unified

    def generate_learning_taxonomy(self, knowledge_map: dict) -> dict:
        """Create structured learning taxonomy from knowledge map"""

        taxonomy = {
            "learning_levels": {
                "foundational": {
                    "description": "Prerequisite knowledge needed to begin FastAPI development",
                    "concepts": [],
                    "outcomes": "Has the Python, HTTP, and framework background needed to learn FastAPI",
                },
                "functional": {
                    "description": "Practical skills to build basic FastAPI applications",
                    "concepts": [],
                    "outcomes": "Can build simple FastAPI endpoints with validation and responses",
                },
                "proficient": {
                    "description": "Advanced skills for production FastAPI systems",
                    "concepts": [],
                    "outcomes": "Can design, secure, test, and deploy FastAPI applications",
                },
            },
            "learning_sequences": {},
            "prerequisite_map": {},
        }

        # Map knowledge categories to learning levels
        domains = knowledge_map.get("knowledge_domains", {})

        level_by_category = {
            "prerequisites": "foundational",
            "essential": "functional",
            "advanced": "proficient",
        }

        for domain, categories in domains.items():
            taxonomy["prerequisite_map"][domain] = list(categories.get("prerequisites", []))

            for category, learning_level in level_by_category.items():
                concepts = [f"{domain}: {concept}" for concept in categories.get(category, [])]

                taxonomy["learning_levels"][learning_level]["concepts"].extend(concepts)

        # Create learning sequences from learning paths
        learning_paths = knowledge_map.get("learning_paths", {})
        for path_name, steps in learning_paths.items():
            taxonomy["learning_sequences"][path_name] = {
                "steps": steps,
                "estimated_time": f"{len(steps) * 2} hours",
                "difficulty": "beginner" if "beginner" in path_name else "intermediate",
            }

        return taxonomy

    def _print_summary(self, knowledge_map: dict) -> None:
        metadata = knowledge_map.get("synthesis_metadata", {})

        print("\n🧠 KNOWLEDGE MAPPING COMPLETE")
        print(f"📊 Files processed: {metadata.get('files_processed', 0)}")

        domains = knowledge_map.get("knowledge_domains", {})
        for domain, categories in domains.items():
            essential_count = len(categories.get("essential", []))
            advanced_count = len(categories.get("advanced", []))
            print(f"📚 {domain}: {essential_count} essential + {advanced_count} advanced concepts")

        summary_sections = (
            ("🎯 Content gaps identified", "content_gaps"),
            ("💡 Synthesis opportunities", "synthesis_opportunities"),
        )

        for label, section_name in summary_sections:
            section = knowledge_map.get(section_name, {})
            total = sum(len(items) for items in section.values())
            print(f"{label}: {total}")

        print(f"\n✅ Results saved to {self.output_dir}")

    async def run_knowledge_mapping(self):
        """Main knowledge mapping workflow"""

        logger.info(f"Starting {DOCUMENTATION_NAME} knowledge domain mapping...")

        if not self.high_quality_files:
            logger.error("No high-quality files found. Run content analysis first.")
            return

        # Extract knowledge domains
        knowledge_map = await self.map_all_knowledge()

        if "error" in knowledge_map:
            logger.error(f"Knowledge mapping failed: {knowledge_map['error']}")
            return

        # Generate learning taxonomy
        learning_taxonomy = self.generate_learning_taxonomy(knowledge_map)

        # Save results
        artifacts = (
            (
                "Knowledge domains",
                self.output_dir / KNOWLEDGE_DOMAINS_FILE,
                knowledge_map,
            ),
            (
                "Learning taxonomy",
                self.output_dir / LEARNING_TAXONOMY_FILE,
                learning_taxonomy,
            ),
        )

        for label, output_path, payload in artifacts:
            with output_path.open("w", encoding="utf-8") as output_file:
                json.dump(payload, output_file, indent=2, ensure_ascii=False)

            logger.info(f"{label} saved to {output_path}")

        # Print summary
        self._print_summary(knowledge_map)

        return knowledge_map, learning_taxonomy


async def main(run_dir: Path):
    """Run the knowledge domain mapping"""

    mapper = FastAPIKnowledgeMapper(run_dir)
    await mapper.run_knowledge_mapping()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract knowledge domains from a completed analysis run."
    )
    parser.add_argument(
        "--run-id",
        required=True,
        metavar="ID",
        help="Existing analyzer run id under data_curation/outputs.",
    )
    args = parser.parse_args()

    run_dir = resolve_run_dir(args.run_id)
    try:
        asyncio.run(main(run_dir))
    except ConfigurationError as error:
        logger.error("%s", error)
        raise SystemExit(1) from error
