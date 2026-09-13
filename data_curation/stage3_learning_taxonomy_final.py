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

# Load environment variables
load_dotenv(PROJECT_ROOT / ".env")


class ConfigurationError(RuntimeError):
    """Pipeline setup problem that should stop the stage before API calls."""


class FastAPILearningTaxonomyGenerator:
    def __init__(self, run_dir: Path):
        self.output_dir = run_dir

        # Load previous analysis results
        self.knowledge_domains = self._load_knowledge_domains()

    def _load_knowledge_domains(self) -> dict:
        """Load knowledge domain mapping results"""
        domains_file = self.output_dir / KNOWLEDGE_DOMAINS_FILE
        if not domains_file.is_file():
            raise ConfigurationError(f"Knowledge domains artifact not found: {domains_file}")

        logger.info(f"Loading knowledge domains from {domains_file}")
        try:
            with domains_file.open("r", encoding="utf-8") as input_file:
                knowledge_domains = json.load(input_file)
        except json.JSONDecodeError as error:
            raise ConfigurationError(
                f"Knowledge domains artifact is not valid JSON: {domains_file}: {error}"
            ) from error

        if not isinstance(knowledge_domains, dict):
            raise ConfigurationError(
                f"Knowledge domains artifact must contain a JSON object: {domains_file}"
            )

        return knowledge_domains

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

        synthesis_prompt = f"""
You are a technical curriculum architect creating a concrete implementation plan for a FastAPI developer education dataset.

Based on the knowledge domain analysis and identified gaps, create a detailed synthesis plan for generating high-quality synthetic dataset files.

IMPORTANT: Respond ONLY with valid JSON. Do not include any explanatory text, markdown formatting, or other content. Just return the JSON object.

KNOWLEDGE GAPS TO ADDRESS:
{json.dumps(self.knowledge_domains.get("content_gaps", {}), indent=2)}

SYNTHESIS OPPORTUNITIES:
{json.dumps(self.knowledge_domains.get("synthesis_opportunities", {}), indent=2)}

REAL-WORLD FASTAPI DEVELOPER NEEDS:
- Practical implementation guidance for common API tasks
- Clear explanations of request validation, response models, dependencies, routers, and error handling
- Security, authentication, testing, and deployment patterns
- When to use specific FastAPI features versus simpler alternatives
- Comprehensive troubleshooting for common issues and framework behavior

Create a synthesis plan with this structure:

{{
  "dataset_structure": {{
    "01_foundations": {{
      "description": "Core FastAPI concepts and decision frameworks",
      "files": [
        {{
          "filename": "fastapi_overview_and_core_concepts.md",
          "description": "What FastAPI is, why developers use it, and how its core pieces fit together",
          "content_focus": ["path operations", "type hints", "validation", "automatic OpenAPI docs"],
          "target_audience": "beginner",
          "estimated_length": "2000-3000 words",
          "key_sections": ["What is FastAPI", "Core app structure", "Request and response flow", "When to use FastAPI"]
        }}
      ]
    }}
  }},
  "content_prioritization": {{
    "phase_1_critical": ["files that must be created first"],
    "phase_2_important": ["files for comprehensive coverage"],
    "phase_3_advanced": ["files for expert-level content"]
  }},
  "synthesis_strategies": {{
    "combine_scattered_info": [
      {{
        "target_file": "fastapi_security_authentication_guide.md",
        "source_concepts": ["authentication patterns", "authorization best practices", "security dependencies"],
        "synthesis_approach": "Consolidate security information into an actionable FastAPI guide"
      }}
    ],
    "fill_knowledge_gaps": [
      {{
        "gap": "missing beginner tutorials",
        "solution": "Create step-by-step implementation guides",
        "files_to_create": ["building_first_fastapi_app.md", "request_response_basics.md"]
      }}
    ],
    "create_practical_examples": [
      {{
        "need": "real-world implementation patterns",
        "approach": "Extract patterns from high-quality source files and create comprehensive examples",
        "focus": "production-ready, testable FastAPI implementations"
      }}
    ]
  }},
  "quality_criteria": {{
    "information_density": "Each file should be self-contained and immediately useful",
    "practical_focus": "Emphasize implementation over theory",
    "security_integration": "Security considerations woven throughout, not separate",
    "progressive_complexity": "Build from simple to complex within each file",
    "real_world_relevance": "Address actual problems developers face"
  }},
  "success_metrics": {{
    "rag_performance": "Higher retrieval relevance due to focused, complete content",
    "developer_value": "Developers can implement FastAPI features after reading",
    "security_awareness": "Security and validation are integrated from foundational level",
    "practical_application": "Clear path from concept to production deployment"
  }}
}}

Focus on creating a dataset that transforms current scattered FastAPI knowledge into a coherent, practical education system.
"""

        try:
            synthesis_plan = await chat_structured(synthesis_prompt, SynthesisPlan, max_tokens=4000)
            return synthesis_plan.model_dump(by_alias=True)
        except Exception as e:  # noqa: BLE001  (one bad synthesis call shouldn't kill the stage)
            logger.error(f"Error creating synthesis plan: {str(e)}")
            return {"error": str(e)}

    def generate_implementation_roadmap(self, taxonomy: dict, synthesis_plan: dict) -> dict:
        """Generate concrete implementation roadmap"""

        # One spec per phase: which dataset_structure section feeds its deliverables,
        # plus the phase's fixed metadata. Drives the implementation_phases build below.
        phase_specs = [
            {
                "key": "phase_1_foundation",  # gitleaks:allow
                "source": "01_foundations",
                "duration": "1-2 days",
                "description": "Create core conceptual files that answer 'what/why/how' of FastAPI",
                "success_criteria": "Beginners understand FastAPI app structure and can build basic endpoints",
            },
            {
                "key": "phase_2_implementation",  # gitleaks:allow
                "source": "02_implementation",
                "duration": "2-3 days",
                "description": "Create practical implementation guides with working examples",
                "success_criteria": "Developers can build, validate, test, and debug FastAPI features",
            },
            {
                "key": "phase_3_production",
                "source": "03_security_production",
                "duration": "2-3 days",
                "description": "Create advanced guides for production deployment and optimization",
                "success_criteria": "Teams can deploy secure, scalable FastAPI applications",
            },
        ]

        dataset_structure = synthesis_plan.get("dataset_structure", {})
        implementation_phases = {}
        for spec in phase_specs:
            section = dataset_structure.get(spec["source"], {})
            implementation_phases[spec["key"]] = {
                "duration": spec["duration"],
                "description": spec["description"],
                "deliverables": [f["filename"] for f in section.get("files", [])],
                "success_criteria": spec["success_criteria"],
            }

        roadmap = {
            "project_overview": {
                "mission": "Transform scattered FastAPI knowledge into a coherent, practical education system",
                "target_outcome": "Developers can build production-ready, secure FastAPI applications",
                "dataset_size": "25-30 high-quality markdown files",
                "expected_improvement": "3x better RAG relevance through focused content",
            },
            "implementation_phases": implementation_phases,
            "quality_assurance": {
                "content_validation": "Each file reviewed for technical accuracy and completeness",
                "rag_testing": "Files tested in RAG pipeline for retrieval quality",
                "developer_feedback": "Sample files validated with target audience",
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
