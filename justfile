# ai-review-ci contract variables consumed by doctor and workflow installers.
ai_review_ci_schema_version := "1"
ai_review_ci_profile := "python"
ai_review_ci_ref := "main"
ai_review_ci_release_channel := "main"
ai_review_ci_workflow_template_version := "1"
ai_review_ci_local_delegation := "global-justfile"
ai_review_ci_default_branch := "main"

ZK_VERSION := "v0.15.5"
ZK_ASSET := "zk-" + ZK_VERSION + "-linux-amd64.tar.gz"
LOCAL_BIN := env_var("HOME") / ".local/bin"

# List available recipes.
default:
    @just --list

[private]
_test-target +args:
    uv run --group dev pytest {{args}}

# Install the agent-memory toolchain (agent-memory, ripgrep, zk, probe).
install: _install-agent-memory _install-ripgrep _install-zk _install-probe _verify-toolchain

# Install the toolchain, then initialize the global memory vault.
setup: install
    #!/usr/bin/env bash
    set -euo pipefail
    vault="$(gum input --prompt 'Global memory vault: ' --value "$HOME/.agent-memory-vault")"
    : "${vault:?Global memory vault path is required}"
    agent-memory maintain init-global --vault "$vault"

# Run commit-tier Python QC through the central implementation.
test-commit:
    #!/usr/bin/env bash
    set -euo pipefail
    just -f "$HOME/ai-review-ci/justfiles/python.just" -d . test-commit

# Run the full Python test suite before pushing.
test-push:
    #!/usr/bin/env bash
    set -euo pipefail
    just -f "$HOME/ai-review-ci/justfiles/python.just" -d . test-push

# Run CI acceptance QC through the central implementation.
test-ci:
    #!/usr/bin/env bash
    set -euo pipefail
    just -f "$HOME/ai-review-ci/justfiles/python.just" -d . test-ci

# Full-repo deferred-debt audit (complexity, dead code, duplication). Scheduled, not push-blocking.
ambient:
    #!/usr/bin/env bash
    set -euo pipefail
    direnv exec "{{ justfile_directory() }}" just -f "$HOME/ai-review-ci/justfiles/qc-tooling.just" -d "{{ justfile_directory() }}" ambient

[private]
_install-agent-memory:
    #!/usr/bin/env bash
    set -euo pipefail
    cargo --version
    uv tool install --force --editable "{{ justfile_directory() }}"
    bin_dir="$(uv tool dir --bin)"
    test -x "$bin_dir/agent-memory"
    case ":$PATH:" in
        *":$bin_dir:"*) ;;
        *)
            printf 'ERROR: uv tool bin directory is not on PATH: %s\n' "$bin_dir" >&2
            printf 'Run: uv tool update-shell\n' >&2
            exit 1
            ;;
    esac
    "$bin_dir/agent-memory" --help >/dev/null

[private]
_install-ripgrep:
    #!/usr/bin/env bash
    set -euo pipefail
    cargo --version
    cargo install --force ripgrep
    rg --version

[private]
_install-zk:
    #!/usr/bin/env bash
    set -euo pipefail
    install_dir="{{ LOCAL_BIN }}"
    mkdir -p "$install_dir"
    case ":$PATH:" in
        *":$install_dir:"*) ;;
        *)
            printf 'ERROR: local bin directory is not on PATH: %s\n' "$install_dir" >&2
            exit 1
            ;;
    esac
    gh --version
    tar --version
    install --version
    trash --version
    temp_dir="$(mktemp -d)"
    trap 'trash "$temp_dir"' EXIT
    gh release download "{{ ZK_VERSION }}" --repo zk-org/zk --pattern "{{ ZK_ASSET }}" --dir "$temp_dir"
    tar -xzf "$temp_dir/{{ ZK_ASSET }}" -C "$temp_dir"
    install -m 0755 "$temp_dir/zk" "$install_dir/zk"
    "$install_dir/zk" --version

[private]
_install-probe:
    #!/usr/bin/env bash
    set -euo pipefail
    gh --version
    tar --version
    install --version
    sha256sum --version
    uvx --from trash-cli trash --version
    probe_version="$(uv run --project "{{ justfile_directory() }}" python -c 'from agent_memory.operations import PROBE_VERSION; print(PROBE_VERSION)')"
    probe_binary="$(uv run --project "{{ justfile_directory() }}" python -c 'from agent_memory.operations import PROBE_BINARY; print(PROBE_BINARY)')"
    probe_tag="v${probe_version}"
    probe_asset="probe-${probe_tag}-x86_64-unknown-linux-musl.tar.gz"
    probe_directory="probe-${probe_tag}-x86_64-unknown-linux-musl"
    temp_dir="$(mktemp -d)"
    trap 'uvx --from trash-cli trash "$temp_dir"' EXIT
    gh release download "$probe_tag" --repo probelabs/probe --pattern "${probe_asset}*" --dir "$temp_dir"
    (
        cd "$temp_dir"
        sha256sum -c "${probe_asset}.sha256"
    )
    tar -xzf "$temp_dir/$probe_asset" -C "$temp_dir"
    install -D -m 0755 "$temp_dir/$probe_directory/probe" "$probe_binary"
    "$probe_binary" --version

[private]
_verify-toolchain:
    #!/usr/bin/env bash
    set -euo pipefail
    test -x "$(uv tool dir --bin)/agent-memory"
    test -x "{{ LOCAL_BIN }}/zk"
    test -x "{{ LOCAL_BIN }}/probelabs-probe"
    uv --version
    git --version
    gum --version
    cargo --version
    rg --version
    "{{ LOCAL_BIN }}/zk" --version
    "{{ LOCAL_BIN }}/probelabs-probe" --version
    "$(uv tool dir --bin)/agent-memory" --help >/dev/null
