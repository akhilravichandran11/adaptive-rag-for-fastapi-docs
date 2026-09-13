"""
Synthetic Dataset Generation Script
Generate high-quality curated FastAPI documentation files based on analysis and synthesis plan.
"""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai_llm_utils import chat
from tqdm import tqdm

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
OUTPUTS_DIR = SCRIPT_DIR / "outputs"

CONTENT_ANALYSIS_FILENAME = "content_analysis.json"
SYNTHESIS_PLAN_FILENAME = "synthesis_plan.json"

# Load environment variables
load_dotenv(PROJECT_ROOT / ".env")


class FastAPIDatasetGenerator:
    def __init__(
        self,
        content_analysis_filename: str = CONTENT_ANALYSIS_FILENAME,
        synthesis_plan_filename: str = SYNTHESIS_PLAN_FILENAME,
    ):
        self.manifest_path = PROJECT_ROOT / "data/fastapi/normalized/github-docs/v1/manifest.json"
        self.docs_dir = self.manifest_path.parent / "docs"
        self.output_dir = OUTPUTS_DIR
        self.output_dir.mkdir(exist_ok=True)
        self.curated_dir = PROJECT_ROOT / "data/fastapi/curated/synthetic/v1"

        with self.manifest_path.open("r", encoding="utf-8") as manifest_file:
            manifest = json.load(manifest_file)

        self.files_by_source_path = {
            file_entry["source_path"]: file_entry for file_entry in manifest["files"]
        }

        # Load analysis results
        self.analysis_data = self._load_json(content_analysis_filename, "content analysis")
        self.synthesis_plan = self._load_json(synthesis_plan_filename, "synthesis plan")

        # Create output directory
        self.curated_dir.mkdir(parents=True, exist_ok=True)

    def _load_json(self, filename: str, label: str) -> dict:
        """Load a JSON artifact from the outputs dir; return {} on any error."""
        try:
            path = self.output_dir / filename
            logger.info(f"Loading {label} from {path}")
            with open(path) as f:
                return json.load(f)
        except Exception as e:  # noqa: BLE001  (one bad synthesis call shouldn't kill the stage)
            logger.error(f"Error loading {label}: {e}")
            return {}

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
        """Select up to 15 high-quality (score >= 7.5) sources relevant to a category.

        If the category maps to keywords, keep only high-quality sources whose path
        matches one; otherwise fall back to the first 10 high-quality sources.
        """
        if not self.analysis_data:
            return []

        # Canonical keyword tiers; aliases that share a keyword list are grouped.
        keyword_groups = [
            (
                ("01_foundations",),
                ["first-steps", "index", "features", "python-types", "body", "response"],
            ),
            (("foundations",), ["first-steps", "features", "python-types", "body", "response"]),
            (
                ("02_practical_implementations", "02_practical_examples"),
                ["dependencies", "background", "testing", "responses", "status", "advanced"],
            ),
            (
                ("practical_implementations",),
                ["dependencies", "background", "testing", "responses", "status"],
            ),
            (
                (
                    "03_integration_and_advanced_topics",
                    "03_integration_guides",
                    "integration_and_advanced_topics",
                ),
                ["security", "databases", "deployment", "advanced", "middleware", "openapi"],
            ),
        ]
        category_keywords = {
            alias: keywords for aliases, keywords in keyword_groups for alias in aliases
        }

        keywords = category_keywords.get(category, [])
        if keywords:
            relevant = [
                item
                for item in self.analysis_data
                if item.get("analysis", {}).get("overall_quality", 0) >= 7.5
                and any(keyword in item.get("source_path", "").lower() for keyword in keywords)
            ]
        else:
            relevant = [
                item
                for item in self.analysis_data
                if item.get("analysis", {}).get("overall_quality", 0) >= 7.5
            ][:10]

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

        try:
            response_text = await chat(
                synthesis_prompt,
                temperature=0.3,  # Slightly creative but focused
                max_tokens=4000,  # Enough for comprehensive content
            )
            return self._strip_code_fence(response_text.strip())

        except Exception as e:  # noqa: BLE001  (one bad synthesis call shouldn't kill the stage)
            logger.error(f"Error generating {filename}: {str(e)}")
            return f"# {filename.replace('_', ' ').title()}\n\nError generating content: {str(e)}"

    async def _generate_one_file(self, file_spec: dict, category: str, category_dir: Path) -> dict:
        """Generate, save, and log a single curated file.

        Returns a uniform result record: {"ok": True, "record": {...}} on success,
        or {"ok": False, "error": "..."} if anything in the attempt fails.
        """
        try:
            filename = file_spec["filename"]
            file_path = category_dir / filename

            logger.info(f"Generating: {filename}")

            # Generate content
            content = await self.generate_curated_file(file_spec, category)

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
                },
            }
        except Exception as e:  # noqa: BLE001  (one bad file shouldn't kill the batch)
            error_msg = f"Error generating {file_spec.get('filename', 'unknown')}: {str(e)}"
            logger.error(error_msg)
            return {"ok": False, "error": error_msg}

    async def generate_all_files(self):
        """Generate all curated files according to synthesis plan"""

        if not self.synthesis_plan.get("dataset_structure"):
            logger.error("No synthesis plan found!")
            return

        timestamp = datetime.now().strftime("%Y_%m_%d_%H%M%S")

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

        # Save generation log
        with open(self.output_dir / f"dataset_generation_log-v1-{timestamp}.json", "w") as f:
            json.dump(generation_log, f, indent=2)

        self._print_summary(generation_log)

        return generation_log

    def _print_summary(self, log: dict) -> None:
        """Print the end-of-run generation summary."""
        print("\n🎯 DATASET GENERATION COMPLETE")
        print(f"✅ Generated: {len(log['generated_files'])} files")
        print(f"❌ Errors: {len(log['errors'])} files")
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

### 02_practical_implementations/
Hands-on tutorials and implementation guides
- First app and endpoint tutorials
- Dependency and routing examples
- Request validation and response model patterns

### 03_integration_and_advanced_topics/
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


async def main(
    content_analysis_filename: str = CONTENT_ANALYSIS_FILENAME,
    synthesis_plan_filename: str = SYNTHESIS_PLAN_FILENAME,
):
    """Run the dataset generation"""

    generator = FastAPIDatasetGenerator(
        content_analysis_filename=content_analysis_filename,
        synthesis_plan_filename=synthesis_plan_filename,
    )

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
        description="Generate curated FastAPI documentation from analysis + synthesis-plan artifacts."
    )
    parser.add_argument(
        "--content-analysis",
        default=CONTENT_ANALYSIS_FILENAME,
        metavar="FILENAME",
        help="Content-analysis JSON filename in the outputs dir. Default: %(default)s",
    )
    parser.add_argument(
        "--synthesis-plan",
        default=SYNTHESIS_PLAN_FILENAME,
        metavar="FILENAME",
        help="Synthesis-plan JSON filename in the outputs dir. Default: %(default)s",
    )
    args = parser.parse_args()

    asyncio.run(
        main(
            content_analysis_filename=args.content_analysis,
            synthesis_plan_filename=args.synthesis_plan,
        )
    )
