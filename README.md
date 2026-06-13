# iwe2

`iwe2` is a Python CLI wrapper around an IWE-backed Markdown memory vault.

The canonical implementation target is [DESIGN-TRANSCRIPT.md](DESIGN-TRANSCRIPT.md).
The proof obligations are tracked under `.agents/proof-obligation-workflow/`.

Public commands:

- `iwe2 vault init <vault>`
- `iwe2 project init --vault <vault>`
- `iwe2 note --scope <project|global> --type <type> --title <title> --content <content>`
- `iwe2 search --scope <project|global|both> <query>`
- `iwe2 search-context --scope <project|global|both> --max-results <count> --max-tokens <count> <query>`
- `iwe2 retrieve <key>`
- `iwe2 squash <key> --depth <depth>`
- `iwe2 promote <key> --to <global-subdir>`
- `iwe2 doctor`
