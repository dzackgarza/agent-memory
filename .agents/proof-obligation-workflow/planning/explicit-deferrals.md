# Explicit Deferrals

Source: [DESIGN-TRANSCRIPT.md](../../../DESIGN-TRANSCRIPT.md)

These items are outside the MVP proof obligations. They remain deferred because the
accepted implementation target is a thin IWE wrapper that proves central vault setup,
project bootstrap, scoped note creation, scoped body and title/key search, graph
retrieval, promotion, and doctor verification.

Dropped scope is not listed here as future work. Wrapper MCP server support,
action-sensitive frontmatter, and lifecycle state frontmatter are rejected from the
target even when the source transcript mentions adjacent ideas.

| Deferred item                       | Source anchor                                                                  | Deferral reason                                                                                                                         | Proof status                 |
| ----------------------------------- | ------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------- |
| Automatic Git commits for the vault | Transcript says promotion may optionally commit changes.                       | The wrapper initializes the vault Git repository and stages vault mutations, but leaves commit message and author policy to the caller. | Not required by any MVP IOB. |
| Forking IWE                         | Transcript says to compose first and fork only after hard requirements appear. | No current MVP obligation requires first-class IWE changes.                                                                             | Delegated to IWE.            |
