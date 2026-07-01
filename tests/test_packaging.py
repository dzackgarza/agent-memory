import re
from importlib.metadata import requires


def declared_dependency_names() -> set[str]:
    names: set[str] = set()
    for requirement in requires("agent-memory") or []:
        name = re.split(r"[ ;<>=!~\[]", requirement, maxsplit=1)[0].strip().lower()
        if name:
            names.add(name)
    return names


def test_markdown_it_py_is_declared_direct_dependency() -> None:
    # Issue #1/#2 remediation replaced regex link/heading extraction with markdown-it-py.
    # It must be a first-class dependency, not merely transitively available via cyclopts.
    assert "markdown-it-py" in declared_dependency_names()


def test_python_frontmatter_is_declared_direct_dependency() -> None:
    assert "python-frontmatter" in declared_dependency_names()
