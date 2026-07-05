from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

type MetadataValue = (
    None | bool | int | float | str | list[MetadataValue] | dict[str, MetadataValue]
)


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
    PLAN = "plan"


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


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    vault: Path
    # None for a global-only config built without a cwd project binding. Every project
    # path reads this through operations.require_project_id, which fails loud if a global
    # config ever reaches project-scoped code.
    project_id: str | None = None


class BaseNoteMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    type: MemoryType
    title: str
    description: str
    tags: list[str]
    timestamp: str
    scope: MemoryScope
    # Single source of truth for the agent-authored note contract: every note this
    # system writes is sourced from the agent at high confidence and is not eligible
    # for further promotion. These are fixed defaults, so construction sites must not
    # re-spell them; changing the contract happens here, in one place.
    source: Literal["agent"] = "agent"
    confidence: Literal["high"] = "high"
    promotable: bool = False

    def base_yaml_payload(self) -> dict[str, MetadataValue]:
        tags: list[MetadataValue] = [tag for tag in self.tags]
        return {
            "type": self.type.value,
            "title": self.title,
            "description": self.description,
            "tags": tags,
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
