"""Source facts returned by the offline v2 inventory preparation step."""

from typing import Literal

from pydantic import BaseModel, Field


class InventoryCorpus(BaseModel):
    source_commit: str = Field(pattern=r"^[0-9a-fA-F]{40}$")
    collection_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    inventory_scope: Literal["full", "selected"]


class SourceDocument(BaseModel):
    source_id: str
    source_path: str
    output_filename: str
    source_url: str
    bucket: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SourceInventory(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    corpus: InventoryCorpus
    documents: list[SourceDocument]
