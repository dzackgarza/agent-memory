ZK_VERSION := "v0.15.5"
ZK_ASSET := "zk-" + ZK_VERSION + "-linux-amd64.tar.gz"
LOCAL_BIN := env_var("HOME") / ".local/bin"

install: _install-agent-memory _install-iwe _install-ripgrep _install-zk _install-probe _verify-toolchain

setup: install
    #!/usr/bin/env bash
    set -euo pipefail
    vault="$(gum input --prompt 'Global memory vault: ' --value "$HOME/.agent-memory-vault")"
    : "${vault:?Global memory vault path is required}"
    agent-memory maintain init-global --vault "$vault"

test:
    #!/usr/bin/env bash
    set -euo pipefail
    direnv exec "{{ justfile_directory() }}" just -f "$HOME/ai-review-ci/justfiles/python.just" -d "{{ justfile_directory() }}" test

test-ci:
    #!/usr/bin/env bash
    set -euo pipefail
    direnv exec "{{ justfile_directory() }}" just -f "$HOME/ai-review-ci/justfiles/python.just" -d "{{ justfile_directory() }}" test-ci

[private]
_install-agent-memory:
    #!/usr/bin/env bash
    set -euo pipefail
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
_install-iwe:
    #!/usr/bin/env bash
    set -euo pipefail
    cargo --version
    cargo install --force iwe iwes iwec
    iwe --version

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
    npx --version
    npx -y @probelabs/probe@latest --version

[private]
_verify-toolchain:
    #!/usr/bin/env bash
    set -euo pipefail
    test -x "$(uv tool dir --bin)/agent-memory"
    test -x "{{ LOCAL_BIN }}/zk"
    uv --version
    git --version
    gum --version
    iwe --version
    rg --version
    npx --version
    "{{ LOCAL_BIN }}/zk" --version
    npx -y @probelabs/probe@latest --version
    "$(uv tool dir --bin)/agent-memory" --help >/dev/null
