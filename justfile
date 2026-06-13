install:
    uv tool install --force --editable .

setup:
    #!/usr/bin/env bash
    set -euo pipefail
    just install
    gum --version
    vault="$(gum input --prompt 'Global memory vault: ' --value "$HOME/.agent-memory-vault")"
    iwe2 maintain init-global --vault "$vault"

test:
    @just -f ~/ai-review-ci/justfiles/python.just -d . test

test-ci:
    @just -f ~/ai-review-ci/justfiles/python.just -d . test-ci
