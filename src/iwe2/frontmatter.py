from __future__ import annotations

from iwe2.models import MetadataValue


def dump_frontmatter(metadata: dict[str, MetadataValue]) -> str:
    lines: list[str] = []
    for key, value in metadata.items():
        if isinstance(value, bool):
            lines.append(f"{key}: {str(value).lower()}")
        elif isinstance(value, str):
            lines.append(f"{key}: {quoted_string(value)}")
        else:
            lines.append(f"{key}:")
            lines.extend(f"- {quoted_string(item)}" for item in value)
    return "\n".join(lines) + "\n"


def load_frontmatter(text: str) -> dict[str, MetadataValue]:
    lines = text.splitlines()
    metadata: dict[str, MetadataValue] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        key, separator, raw_value = line.partition(":")
        assert separator == ":", f"frontmatter line must contain a key separator: {line}"
        assert key, "frontmatter key must be nonempty"
        stripped_value = raw_value.strip()
        if stripped_value == "":
            items: list[str] = []
            index += 1
            while index < len(lines) and lines[index].startswith("- "):
                items.append(parse_scalar(lines[index][2:].strip()))
                index += 1
            metadata[key] = items
        else:
            continuation_lines: list[str] = []
            index += 1
            while index < len(lines) and lines[index].startswith("  "):
                continuation_lines.append(lines[index].strip())
                index += 1
            if continuation_lines:
                stripped_value = " ".join([stripped_value, *continuation_lines])
            metadata[key] = parse_value(stripped_value)
    return metadata


def parse_value(raw_value: str) -> MetadataValue:
    if raw_value == "true":
        return True
    if raw_value == "false":
        return False
    return parse_scalar(raw_value)


def parse_scalar(raw_value: str) -> str:
    if raw_value.startswith("'") and raw_value.endswith("'"):
        return raw_value[1:-1].replace("''", "'")
    return raw_value


def quoted_string(value: str) -> str:
    escaped = value.replace("'", "''")
    return f"'{escaped}'"
