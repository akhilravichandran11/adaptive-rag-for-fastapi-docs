"""
Shared, non-secret app constants for the data-curation scripts.

Secrets and per-environment overrides (API keys, model choice) live in .env and
are read with os.getenv. This file is for values that are the same in every
environment and are part of the app's identity.
"""

APPLICATION_NAME = "FastAPI Documentation Q&A RAG system"
DOCUMENTATION_NAME = "FastAPI documentation"
DOMAIN_NAME = "FastAPI development"
