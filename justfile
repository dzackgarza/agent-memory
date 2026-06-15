ZK_VERSION := "v0.15.5"
ZK_ASSET := "zk-" + ZK_VERSION + "-linux-amd64.tar.gz"
LOCAL_BIN := env_var("HOME") / ".local/bin"

install: _install-iwe2 _install-iwe _install-ripgrep _install-zk _install-probe _verify-toolchain

setup: install
    #!/usr/bin/env bash
    set -euo pipefail
    vault="$(gum input --prompt 'Global memory vault: ' --value "$HOME/.agent-memory-vault")"
    : "${vault:?Global memory vault path is required}"
    iwe2 maintain init-global --vault "$vault"

test:
    @just -f ~/ai-review-ci/justfiles/python.just -d . test

test-ci:
    @just -f ~/ai-review-ci/justfiles/python.just -d . test-ci

[private]
_install-iwe2:
    #!/usr/bin/env bash
    set -euo pipefail
    uv tool install --force --editable "{{justfile_directory()}}"
    bin_dir="$(uv tool dir --bin)"
    case ":$PATH:" in
        *":$bin_dir:"*) ;;
        *)
            printf 'ERROR: uv tool bin directory is not on PATH: %s\n' "$bin_dir" >&2
            printf 'Run: uv tool update-shell\n' >&2
            exit 1
            ;;
    esac
    installed="$(command -v iwe2)"
    if [[ "$installed" != "$bin_dir/iwe2" ]]; then
        printf 'ERROR: PATH resolves iwe2 to %s, expected %s/iwe2\n' "$installed" "$bin_dir" >&2
        exit 1
    fi
    iwe2 --help >/dev/null

[private]
_install-iwe:
    #!/usr/bin/env bash
    set -euo pipefail
    if command -v iwe; then
        iwe --version
        exit 0
    fi
    command -v cargo
    cargo install iwe iwes iwec
    command -v iwe
    iwe --version

[private]
_install-ripgrep:
    #!/usr/bin/env bash
    set -euo pipefail
    if command -v rg; then
        rg --version
        exit 0
    fi
    command -v cargo
    cargo install ripgrep
    command -v rg
    rg --version

[private]
_install-zk:
    #!/usr/bin/env bash
    set -euo pipefail
    install_dir="{{LOCAL_BIN}}"
    mkdir -p "$install_dir"
    case ":$PATH:" in
        *":$install_dir:"*) ;;
        *)
            printf 'ERROR: local bin directory is not on PATH: %s\n' "$install_dir" >&2
            exit 1
            ;;
    esac
    command -v gh
    command -v tar
    command -v install
    command -v trash
    temp_dir="$(mktemp -d)"
    trap 'trash "$temp_dir"' EXIT
    gh release download "{{ZK_VERSION}}" --repo zk-org/zk --pattern "{{ZK_ASSET}}" --dir "$temp_dir"
    tar -xzf "$temp_dir/{{ZK_ASSET}}" -C "$temp_dir"
    install -m 0755 "$temp_dir/zk" "$install_dir/zk"
    installed="$(command -v zk)"
    if [[ "$installed" != "$install_dir/zk" ]]; then
        printf 'ERROR: PATH resolves zk to %s, expected %s/zk\n' "$installed" "$install_dir" >&2
        exit 1
    fi
    zk --version

[private]
_install-probe:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v npx
    npx -y @probelabs/probe@latest --version

[private]
_verify-toolchain:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v uv
    command -v git
    command -v gum
    command -v iwe
    command -v rg
    command -v npx
    command -v zk
    command -v iwe2
    git --version
    gum --version
    iwe --version
    rg --version
    npx --version
    zk --version
    npx -y @probelabs/probe@latest --version
    iwe2 --help >/dev/null
