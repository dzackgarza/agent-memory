from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict
from pydantic.functional_validators import AfterValidator

ProjectRootStrategy = Literal["git-root"]
MetadataValue = str | bool | list[str]


class MemoryScope(StrEnum):
    PROJECT = "project"
    GLOBAL = "global"


class SearchScope(StrEnum):
    PROJECT = "project"
    GLOBAL = "global"
    BOTH = "both"


class ContentSearchMode(StrEnum):
    EXACT = "exact"
    FUZZY = "fuzzy"
    RANKED = "ranked"


class MemoryType(StrEnum):
    DECISION = "decision"
    TRAP = "trap"
    ADVICE = "advice"
    CONTEXT = "context"
    REFERENCE = "reference"


class InspectOutputFormat(StrEnum):
    JSON = "json"


class InspectPathKind(StrEnum):
    ROOTS = "roots"
    INDEXES = "indexes"
    NOTES = "notes"
    ALL = "all"


class InspectLinkDirection(StrEnum):
    CHILDREN = "children"
    PARENTS = "parents"
    BOTH = "both"


class InspectStatsGroup(StrEnum):
    TYPE = "type"
    SCOPE = "scope"
    DAY = "day"


class InspectExportProfile(StrEnum):
    MAP = "map"
    CONTEXT = "context"
    ARCHIVE = "archive"


class InspectExportFormat(StrEnum):
    GRAPH_JSON = "graph-json"


def require_nonempty(value: str) -> str:
    assert value.strip(), "configuration strings must be nonempty"
    return value


def require_positive_integer(value: int) -> int:
    assert value > 0, "search bounds must be positive"
    return value


NonemptyConfigString = Annotated[str, AfterValidator(require_nonempty)]
PositiveConfigInteger = Annotated[int, AfterValidator(require_positive_integer)]


class ProjectConfigFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    vault: NonemptyConfigString
    project_id: NonemptyConfigString
    project_root_strategy: ProjectRootStrategy
    global_scopes: list[str]
    search_max_results: PositiveConfigInteger
    search_max_tokens: PositiveConfigInteger


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    vault: Path
    project_id: str
    project_root_strategy: ProjectRootStrategy
    global_scopes: tuple[str, ...]
    search_max_results: int
    search_max_tokens: int

    @classmethod
    def from_file_payload(cls, payload: ProjectConfigFile) -> ProjectConfig:
        return cls(
            vault=Path(payload.vault),
            project_id=payload.project_id,
            project_root_strategy=payload.project_root_strategy,
            global_scopes=tuple(payload.global_scopes),
            search_max_results=payload.search_max_results,
            search_max_tokens=payload.search_max_tokens,
        )

    def to_toml_payload(self) -> dict[str, str | int | list[str]]:
        return {
            "vault": str(self.vault),
            "project_id": self.project_id,
            "project_root_strategy": self.project_root_strategy,
            "global_scopes": list(self.global_scopes),
            "search_max_results": self.search_max_results,
            "search_max_tokens": self.search_max_tokens,
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
