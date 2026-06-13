# Explicit Deferrals

Source: [DESIGN-TRANSCRIPT.md](../../../DESIGN-TRANSCRIPT.md)

These items are outside the MVP proof obligations. They remain deferred because the
accepted implementation target is a thin IWE wrapper that proves central vault setup,
project bootstrap, scoped note creation, scoped body search, graph retrieval, promotion,
and doctor verification.

| Deferred item                                         | Source anchor                                                                                                    | Deferral reason                                                                                                                             | Proof status                                                                  |
| ----------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| `zk` indexed search and note creation                 | Transcript sections "Where zk fits" and "Final recommendation" mark `zk` as optional.                            | `rg` proves body search without adding a rebuildable SQLite index dependency to the MVP.                                                    | Not required by IOB-001 through IOB-007.                                      |
| Probe ranked contextual search                        | Transcript sections "Scoping implementation" and "Search and aggregation policy" describe Probe as a complement. | The MVP owns exact scoped body search through `rg`; ranked contextual search is a second retrieval mode.                                    | Not required by IOB-004.                                                      |
| Native IWE graph-filter scoped search                 | Transcript suggests hiding experimental IWE query syntax behind the wrapper.                                     | The MVP uses structural IWE indexes for graph shape and filesystem roots for body search; direct graph-query merge is not needed for proof. | Covered for graph layout by TEST-001 and TEST-002, deferred for graph search. |
| Automatic Git init, staging, or commits for the vault | Transcript says promotion may optionally commit or stage changes.                                                | The MVP writes durable files and leaves Git policy to the caller. Auto-commit behavior would add workflow authority.                        | Not required by any MVP IOB.                                                  |
| Forking IWE                                           | Transcript says to compose first and fork only after hard requirements appear.                                   | No current MVP obligation requires first-class IWE changes.                                                                                 | Delegated to IWE.                                                             |
