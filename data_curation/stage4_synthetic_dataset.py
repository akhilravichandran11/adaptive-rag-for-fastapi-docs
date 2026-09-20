"""
Synthetic Dataset Generation Script
Generate high-quality curated FastAPI documentation files based on analysis and synthesis plan.
"""

import asyncio
import json
import logging
import re
from pathlib import Path

from dotenv import load_dotenv
from openai_llm_utils import chat
from pipeline import (
    CONTENT_ANALYSIS_FILE,
    DATASET_GENERATION_LOG_FILE,
    SYNTHESIS_PLAN_FILE,
    resolve_run_dir,
)
from tqdm import tqdm

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

# Load environment variables
load_dotenv(PROJECT_ROOT / ".env")


class FastAPIDatasetGenerator:
    def __init__(self, run_dir: Path):
        self.manifest_path = PROJECT_ROOT / "data/fastapi/normalized/github-docs/v1/manifest.json"
        self.docs_dir = self.manifest_path.parent / "docs"
        self.output_dir = run_dir
        self.curated_dir = run_dir / "synthetic_dataset"
        self.category_keywords = {
            "01_foundations": [
                "first-steps",
                "index",
                "features",
                "python-types",
                "body",
                "response",
            ],
            "02_implementation": [
                "dependencies",
                "background",
                "testing",
                "responses",
                "status",
                "advanced",
            ],
            "03_security_production": [
                "security",
                "databases",
                "deployment",
                "advanced",
                "middleware",
                "openapi",
            ],
        }

        content_analysis_path = self.output_dir / CONTENT_ANALYSIS_FILE
        self.analysis_data = self._load_json(
            content_analysis_path,
            "Content analysis",
            list,
        )

        synthesis_plan_path = self.output_dir / SYNTHESIS_PLAN_FILE
        self.synthesis_plan = self._load_json(
            synthesis_plan_path,
            "Synthesis plan",
            dict,
        )
        dataset_structure = self.synthesis_plan.get("dataset_structure")
        if not isinstance(dataset_structure, dict) or not dataset_structure:
            raise ValueError(
                "Synthesis plan artifact must contain a non-empty "
                f"'dataset_structure' JSON object: {synthesis_plan_path}"
            )

        expected_categories = set(self.category_keywords)
        actual_categories = set(dataset_structure)
        if actual_categories != expected_categories:
            missing = sorted(expected_categories - actual_categories)
            unexpected = sorted(actual_categories - expected_categories)
            raise ValueError(
                "Synthesis plan categories do not match the Stage 3 contract: "
                f"missing={missing}, unexpected={unexpected}: {synthesis_plan_path}"
            )

        manifest = self._load_json(
            self.manifest_path,
            "Normalized-doc manifest",
            dict,
        )
        manifest_files = manifest.get("files")
        if not isinstance(manifest_files, list):
            raise ValueError(
                f"Normalized-doc manifest must contain a 'files' JSON array: {self.manifest_path}"
            )

        for file_entry in manifest_files:
            if (
                not isinstance(file_entry, dict)
                or not isinstance(file_entry.get("source_path"), str)
                or not isinstance(file_entry.get("output_filename"), str)
            ):
                raise ValueError(
                    "Normalized-doc manifest entries must contain string "
                    f"'source_path' and 'output_filename' values: {self.manifest_path}"
                )

        self.files_by_source_path = {
            file_entry["source_path"]: file_entry for file_entry in manifest_files
        }

        # Create output directory only after local inputs and configuration are valid.
        self.curated_dir.mkdir(parents=True, exist_ok=True)

    def _load_json(
        self,
        path: Path,
        label: str,
        expected_type: type,
    ) -> list | dict:
        """Load and validate one required local JSON input."""
        if not path.is_file():
            raise ValueError(f"{label} artifact not found: {path}")

        logger.info(f"Loading {label.lower()} from {path}")
        try:
            with path.open("r", encoding="utf-8") as input_file:
                payload = json.load(input_file)
        except json.JSONDecodeError as error:
            raise ValueError(f"{label} artifact is not valid JSON: {path}: {error}") from error
        except (OSError, UnicodeError) as error:
            raise ValueError(f"Could not read {label.lower()} artifact: {path}: {error}") from error

        if not isinstance(payload, expected_type):
            expected_shape = "array" if expected_type is list else "object"
            raise ValueError(f"{label} artifact must contain a JSON {expected_shape}: {path}")

        return payload

    def _get_source_content(self, source_path: str) -> str:
        """Get normalized content for a manifest source path."""
        file_entry = self.files_by_source_path.get(source_path)
        if not file_entry:
            logger.warning(f"Source path not found in manifest: {source_path}")
            return ""

        source_file = self.docs_dir / file_entry["output_filename"]
        if source_file.exists():
            with source_file.open("r", encoding="utf-8") as file:
                return file.read()

        logger.warning(f"Normalized source file not found: {source_file}")
        return ""

    def _select_sources(self, category: str) -> list[dict]:
        """Select up to 15 high-quality sources for one canonical Stage 3 category."""
        keywords = self.category_keywords[category]
        relevant = [
            item
            for item in self.analysis_data
            if item.get("analysis", {}).get("overall_quality", 0) >= 7.5
            and any(keyword in item.get("source_path", "").lower() for keyword in keywords)
        ]

        if not relevant:
            raise ValueError(
                f"No high-quality sources matched category {category!r} using keywords {keywords}"
            )

        return relevant[:15]

    def _build_excerpts(self, sources: list[dict]) -> str:
        """Collect up to 8 source excerpts and format them for the synthesis prompt."""
        excerpts = []
        for source in sources[:8]:  # Limit to prevent token overflow
            source_path = source["source_path"]
            content = self._get_source_content(source_path)
            if content:
                excerpts.append(
                    {
                        "source_path": source_path,
                        "quality_score": source.get("analysis", {}).get("overall_quality", 0),
                        "excerpt": content[:1500],  # First 1500 chars
                    }
                )
        return "\n\n".join(
            f"--- SOURCE: {s['source_path']} (Quality: {s['quality_score']}/10) ---\n{s['excerpt']}"
            for s in excerpts
        )

    def _strip_code_fence(self, text: str) -> str:
        """Remove a wrapping ```markdown ... ``` fence if the model added one."""
        return text.removeprefix("```markdown").removesuffix("```")

    def _parse_estimated_length(self, estimated_length: str) -> tuple[int, int]:
        """Parse a '<minimum>-<maximum> words' target range."""
        match = re.fullmatch(
            r"\s*(\d+)\s*-\s*(\d+)\s+words\s*",
            estimated_length,
        )
        if not match:
            raise ValueError(
                f"Estimated length must use '<minimum>-<maximum> words': {estimated_length!r}"
            )

        minimum, maximum = (int(value) for value in match.groups())
        if minimum > maximum:
            raise ValueError(f"Estimated length minimum exceeds maximum: {estimated_length!r}")

        return minimum, maximum

    async def generate_curated_file(self, file_spec: dict, category: str) -> str:
        """Generate a single curated file using LLM synthesis"""

        filename = file_spec["filename"]
        description = file_spec["description"]
        content_focus = file_spec.get("content_focus", [])
        target_audience = file_spec.get("target_audience", "intermediate")
        key_sections = file_spec.get("key_sections", [])
        estimated_length = file_spec.get("estimated_length", "2000-3000 words")

        # Get relevant source materials and format them for the prompt
        source_files = self._select_sources(category)
        sources_text = self._build_excerpts(source_files)

        synthesis_prompt = f"""You are an expert technical writer creating high-quality FastAPI documentation.

TASK: Create a comprehensive, standalone markdown file that developers can use to learn and implement FastAPI effectively.

FILE TO CREATE: {filename}
DESCRIPTION: {description}
TARGET AUDIENCE: {target_audience}
ESTIMATED LENGTH: {estimated_length}

CONTENT FOCUS AREAS:
{chr(10).join(f"- {focus}" for focus in content_focus)}

KEY SECTIONS TO INCLUDE:
{chr(10).join(f"- {section}" for section in key_sections)}

QUALITY REQUIREMENTS:
1. **Self-contained**: Complete coverage of the topic without requiring other files
2. **Practical focus**: Emphasize implementation over theory
3. **Code examples**: Include working, tested code snippets where relevant
4. **Progressive structure**: Start simple, build to more complex concepts
5. **RAG-optimized**: Each section should be independently useful for retrieval
6. **FastAPI-aware**: Use accurate FastAPI, Pydantic, and Python terminology
7. **Beginner-friendly**: Clear explanations even for complex topics

SOURCE MATERIALS TO SYNTHESIZE:
{sources_text}

INSTRUCTIONS:
- Create comprehensive markdown content
- Use clear headings and structure
- Include practical code examples
- Add security considerations where relevant
- Make each section independently valuable
- Include troubleshooting tips where applicable
- Use simple language while maintaining technical accuracy
- Focus on what developers actually need to implement FastAPI successfully

Generate the complete markdown file content:"""

        response_text = await chat(
            synthesis_prompt,
            temperature=0.3,  # Slightly creative but focused
            max_tokens=4000,  # Enough for comprehensive content
        )
        stripped_response = response_text.strip()
        content = self._strip_code_fence(stripped_response)
        if not content.strip():
            raise RuntimeError(f"Empty response generating {filename}")
        return content

    async def _generate_one_file(self, file_spec: dict, category: str, category_dir: Path) -> dict:
        """Generate, save, and log a single curated file.

        Returns a uniform result record: {"ok": True, "record": {...}} on success,
        or {"ok": False, "error": "..."} if anything in the attempt fails.
        """
        try:
            filename = file_spec["filename"]
            estimated_length = file_spec.get("estimated_length", "2000-3000 words")
            target_min_words, target_max_words = self._parse_estimated_length(estimated_length)
            file_path = category_dir / filename

            logger.info(f"Generating: {filename}")

            # Generate content
            content = await self.generate_curated_file(file_spec, category)
            word_count = len(content.split())
            if word_count < target_min_words:
                length_status = "below_target"
            elif word_count > target_max_words:
                length_status = "above_target"
            else:
                length_status = "within_target"

            if length_status != "within_target":
                logger.warning(
                    f"Generated {filename} with {word_count} words; "
                    f"target range is {target_min_words}-{target_max_words} words"
                )

            # Save file
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)

            # Delay to respect rate limits
            await asyncio.sleep(2)

            return {
                "ok": True,
                "record": {
                    "category": category,
                    "filename": filename,
                    "path": str(file_path),
                    "size": len(content),
                    "target_audience": file_spec.get("target_audience", "unknown"),
                    "estimated_length": estimated_length,
                    "target_min_words": target_min_words,
                    "target_max_words": target_max_words,
                    "word_count": word_count,
                    "length_status": length_status,
                },
            }
        except Exception as e:  # noqa: BLE001  (one bad file shouldn't kill the batch)
            error_msg = f"Error generating {file_spec.get('filename', 'unknown')}: {str(e)}"
            logger.error(error_msg)
            return {"ok": False, "error": error_msg}

    async def generate_all_files(self):
        """Generate all curated files according to synthesis plan"""

        # Generate every file across every category, collecting one result per attempt.
        results = []
        for category, category_spec in self.synthesis_plan["dataset_structure"].items():
            category_dir = self.curated_dir / category
            category_dir.mkdir(exist_ok=True)

            logger.info(f"Generating files for category: {category}")

            for file_spec in tqdm(category_spec.get("files", []), desc=f"Generating {category}"):
                results.append(await self._generate_one_file(file_spec, category, category_dir))

        # Build the generation log from the collected results.
        generated_files = [r["record"] for r in results if r["ok"]]
        errors = [r["error"] for r in results if not r["ok"]]
        generation_log = {
            "generated_files": generated_files,
            "errors": errors,
            "generation_time": "",
            "total_files": len(generated_files),
        }

        self._print_summary(generation_log)

        if errors:
            raise RuntimeError(
                f"Stage 4 failed: {len(errors)} of {len(results)} planned files failed"
            )

        generation_log_path = self.output_dir / DATASET_GENERATION_LOG_FILE
        with generation_log_path.open("w", encoding="utf-8") as output_file:
            json.dump(generation_log, output_file, indent=2, ensure_ascii=False)

        return generation_log

    def _print_summary(self, log: dict) -> None:
        """Print the end-of-run generation summary."""
        length_target_misses = sum(
            record["length_status"] != "within_target" for record in log["generated_files"]
        )

        print("\n🎯 DATASET GENERATION COMPLETE")
        print(f"✅ Generated: {len(log['generated_files'])} files")
        print(f"❌ Errors: {len(log['errors'])} files")
        print(f"⚠️  Length target misses: {length_target_misses} files")
        print(f"📁 Output location: {self.curated_dir}")

        if log["errors"]:
            print("\n⚠️  Errors encountered:")
            for error in log["errors"]:
                print(f"   - {error}")

    async def create_readme(self):
        """Create a README for the curated dataset"""

        readme_content = """# Curated FastAPI Knowledge Dataset

This dataset contains high-quality, curated FastAPI documentation optimized for RAG systems and developer learning.

## 📊 Dataset Statistics

- **Source Files Analyzed**: Generated from the current FastAPI content analysis run
- **Content Focus**: Implementation-focused FastAPI guidance
- **Target Audiences**: Beginner to Advanced developers

## 📁 Dataset Structure

### 01_foundations/
Core FastAPI concepts and decision frameworks
- Overview and purpose explanations
- Glossary and terminology
- Request and response flow basics

### 02_implementation/
Hands-on tutorials and implementation guides
- First app and endpoint tutorials
- Dependency and routing examples
- Request validation and response model patterns

### 03_security_production/
Advanced FastAPI guides and integration patterns
- Security and authentication guides
- Database integration patterns
- Deployment and operational topics
- Error handling workflows

## 🎯 Quality Criteria

Each file in this dataset meets these standards:
- **Self-contained**: Complete topic coverage without dependencies
- **Practical focus**: Implementation over theory
- **FastAPI-aware**: Accurate FastAPI, Pydantic, and Python terminology
- **Progressive complexity**: Simple to complex within each file
- **RAG-optimized**: Each section independently useful for retrieval

## 🚀 Usage for RAG Systems

This dataset is optimized for:
- Better retrieval relevance (fewer, higher-quality chunks)
- More focused responses (targeted content)
- Clear learning progression (beginner to advanced)
- Real-world applicability (production-ready examples)

Generated using LLM-assisted analysis and synthesis from FastAPI documentation.
"""

        readme_path = self.curated_dir / "README.md"
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(readme_content)

        logger.info(f"README created at {readme_path}")


async def main(run_dir: Path):
    """Run the dataset generation"""

    generator = FastAPIDatasetGenerator(run_dir)

    logger.info("Starting curated FastAPI dataset generation...")

    # Create README first
    await generator.create_readme()

    # Generate all curated files
    generation_log = await generator.generate_all_files()

    logger.info("Dataset generation complete!")

    return generation_log


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate curated FastAPI documentation for an existing pipeline run."
    )
    parser.add_argument(
        "--run-id",
        required=True,
        metavar="ID",
        help="Existing pipeline run id under data_curation/outputs.",
    )
    args = parser.parse_args()

    try:
        asyncio.run(main(resolve_run_dir(args.run_id)))
    except Exception as error:  # noqa: BLE001  (CLI converts stage failures to exit status)
        logger.error("%s", error)
        raise SystemExit(1) from error
