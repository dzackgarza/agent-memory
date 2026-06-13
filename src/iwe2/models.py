from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

ProjectRootStrategy = Literal["git-root"]
MetadataValue = str | bool | list[str]

GLOBAL_SCOPES: tuple[str, ...] = (
    "global/advice",
    "global/traps",
    "global/workflows",
    "global/tools",
)


class MemoryScope(StrEnum):
    PROJECT = "project"
    GLOBAL = "global"


class SearchScope(StrEnum):
    PROJECT = "project"
    GLOBAL = "global"
    BOTH = "both"


class MemoryType(StrEnum):
    DECISION = "decision"
    TRAP = "trap"
    WORKFLOW = "workflow"
    FACT = "fact"
    ADVICE = "advice"
    CONVENTION = "convention"


class ProjectConfigFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    vault: str
    project_id: str
    project_root_strategy: ProjectRootStrategy
    global_scopes: list[str]

    @field_validator("vault", "project_id")
    @classmethod
    def require_nonempty(cls, value: str) -> str:
        assert value.strip(), "configuration strings must be nonempty"
        return value


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    vault: Path
    project_id: str
    project_root_strategy: ProjectRootStrategy
    global_scopes: tuple[str, ...]

    @classmethod
    def from_file_payload(cls, payload: ProjectConfigFile) -> ProjectConfig:
        return cls(
            vault=Path(payload.vault),
            project_id=payload.project_id,
            project_root_strategy=payload.project_root_strategy,
            global_scopes=tuple(payload.global_scopes),
        )

    def to_toml_payload(self) -> dict[str, str | list[str]]:
        return {
            "vault": str(self.vault),
            "project_id": self.project_id,
            "project_root_strategy": self.project_root_strategy,
            "global_scopes": list(self.global_scopes),
        }


class BaseNoteMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    type: MemoryType
    title: str
    description: str
    tags: list[str]
    timestamp: str
    scope: MemoryScope
    source: Literal["agent"]
    confidence: Literal["high"]
    promotable: bool

    def base_yaml_payload(self) -> dict[str, MetadataValue]:
        return {
            "type": self.type.value,
            "title": self.title,
            "description": self.description,
            "tags": self.tags,
            "timestamp": self.timestamp,
            "scope": self.scope.value,
            "source": self.source,
            "confidence": self.confidence,
            "promotable": self.promotable,
        }


class ProjectNoteMetadata(BaseNoteMetadata):
    project_id: str

    def to_yaml_payload(self) -> dict[str, MetadataValue]:
        payload = self.base_yaml_payload()
        payload["project_id"] = self.project_id
        return payload


class GlobalNoteMetadata(BaseNoteMetadata):
    def to_yaml_payload(self) -> dict[str, MetadataValue]:
        return self.base_yaml_payload()


class PromotedNoteMetadata(BaseNoteMetadata):
    origin_project_id: str

    def to_yaml_payload(self) -> dict[str, MetadataValue]:
        payload = self.base_yaml_payload()
        payload["origin_project_id"] = self.origin_project_id
        return payload
