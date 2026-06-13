from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter

from iwe2.models import MemoryScope, MemoryType, SearchScope
from iwe2.operations import (
    JsonValue,
    create_note,
    init_project,
    init_vault,
    promote_note,
    retrieve_note,
    search_context,
    search_notes,
    squash_note,
)
from iwe2.operations import (
    doctor as run_doctor,
)

app = App(name="iwe2")
vault_app = app.command(App(name="vault"))
project_app = app.command(App(name="project"))


@vault_app.command(name="init")
def vault_init(vault: Path) -> None:
    emit(init_vault(vault))


@project_app.command(name="init")
def project_init(*, vault: Path) -> None:
    emit(init_project(vault=vault, cwd=Path.cwd()))


@app.command(name="note")
def note(
    *,
    scope: MemoryScope,
    memory_type: Annotated[MemoryType, Parameter(name="type")],
    title: str,
    content: str,
) -> None:
    emit(
        create_note(
            scope=scope,
            memory_type=memory_type,
            title=title,
            content=content,
            cwd=Path.cwd(),
        )
    )


@app.command(name="search")
def search(query: str, *, scope: SearchScope) -> None:
    print(search_notes(scope=scope, query=query, cwd=Path.cwd()), end="")


@app.command(name="search-context")
def search_context_command(
    query: str,
    *,
    scope: SearchScope,
    max_results: int,
    max_tokens: int,
) -> None:
    print(
        search_context(
            scope=scope,
            query=query,
            max_results=max_results,
            max_tokens=max_tokens,
            cwd=Path.cwd(),
        ),
        end="",
    )


@app.command(name="retrieve")
def retrieve(key: str) -> None:
    print(retrieve_note(key=key, cwd=Path.cwd()), end="")


@app.command(name="squash")
def squash(key: str, *, depth: int) -> None:
    print(squash_note(key=key, depth=depth, cwd=Path.cwd()), end="")


@app.command(name="promote")
def promote(key: str, *, destination: Annotated[str, Parameter(name="to")]) -> None:
    emit(promote_note(key=key, destination=destination, cwd=Path.cwd()))


@app.command(name="doctor")
def doctor() -> None:
    emit(run_doctor(cwd=Path.cwd()))


def emit(payload: Mapping[str, JsonValue]) -> None:
    print(json.dumps(payload, sort_keys=True))
