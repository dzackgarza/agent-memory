# QC Status

Target reader: a future agent deciding which QC facts are settled now and which findings
belong to the post-goal cleanup pass.

Reader task: preserve the exact delegated QC failure without letting it block the MVP
proof closure.

## Current Delegated QC Run

Command:

```bash
just test
```

Exit status: `1`

Passed before failure:

- Python project preflight check
- Structured text formatting
- Semgrep auto-fix and scan
- uvx/npx tool runner setup
- Tool installation
- Ruff format and check
- Python syntax validation
- mypy type checking

Failure stage: deptry dependency linting.

Raw finding block:

```text
pyproject.toml: DEP002 'python-slugify' defined as a dependency but not used in the codebase
pyproject.toml: DEP002 'PyYAML' defined as a dependency but not used in the codebase
pyproject.toml: DEP002 'types-PyYAML' defined as a dependency but not used in the codebase
src/iwe2/operations.py:10:8: DEP001 'yaml' imported but missing from the dependency definitions
src/iwe2/operations.py:11:1: DEP001 'slugify' imported but missing from the dependency definitions
Found 5 dependency issues.
```

## Deferral Classification

The live objective explicitly permits deferring QC triage when remediation would derail
completion of the viable tool and proof obligations. This deptry output is therefore
preserved as deferred QC evidence for the post-goal cleanup pass.

This document does not classify the findings as false positives or true positives.
That classification belongs to the later QC triage pass. The current implementation must
not add local deptry configuration, local suppressions, dependency renames, or wrapper
code only to satisfy this detector.

## Product Proof Route

The product proof still rests on the public CLI integration tests in
`tests/test_cli_workflows.py`.

Direct product proof command:

```bash
uv run --project /home/dzack/iwe2 --directory /home/dzack/iwe2 pytest tests/test_cli_workflows.py -q
```

Result:

```text
5 passed in 12.15s
```

The delegated `just test` run did not reach pytest because deptry stopped the gate
first. This QC document therefore records both the direct product-test result and the
delegated gate status.
