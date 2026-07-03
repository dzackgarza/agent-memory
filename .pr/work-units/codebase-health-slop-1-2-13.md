## Intended result

The codebase no longer owns the slop patterns reported in #1/#2: Markdown semantics are parsed with structured Markdown tooling, frontmatter parsing uses the existing frontmatter library boundary, and search-result parsing uses structured output rather than brittle string splits.

## Scope

- Included: reconcile the overlapping #1/#2 reports against current code, replace the live hand-rolled Markdown link/heading/frontmatter/search parsing surfaces, and close parent #13 if those reports exhaust the current Codebase Health subtree.
- Excluded: unrelated architecture cleanup, style-only rewrites, and administrative duplicate closure without code/evidence.
- Preserved behavior: memory graph/query behavior remains semantically equivalent or more correct, with fixtures covering code blocks, nested/reference-style links where supported, and filenames containing colons for search parsing.

## GitHub tracking

- Target issue set / subtree: #1, #2, #13
- Milestone: Codebase Health and Slop Remediation
- Closes on merge:
  - Closes #1
  - Closes #2
  - Closes #13
- References only:
  - Refs #41

## Implementation plan

Treat #1 and #2 as overlapping reports, not a license to close one as duplicate. First verify which cited paths still exist after the current refactors. Then replace current live slop sites with structured parsers/APIs and add regression fixtures that would have broken the original regex/string-splitting implementations.

## Claim map

- [ ] **#1/#2 - reported reinvention slop is remediated against current code**
  - Proof obligations claimed: Markdown links/headings are AST-backed or equivalent structured parsing; frontmatter splitting is delegated to `python-frontmatter`; ripgrep/search output parsing uses structured JSON or equivalent; obsolete/stale findings are explicitly dispositioned with current-code evidence.
  - Partial / not claimed: no broad rewrite of unrelated operations code.
  - Evidence required: tests for the known brittle cases; `just test`/relevant gate evidence; explicit current-code mapping from each report item to fix or stale disposition.
  - Current evidence: issue reports only.

- [ ] **#13 - Codebase Health parent subtree is complete**
  - Proof obligations claimed: #1 and #2 are the only open child obligations under #13 at ready time, and both are fully evidenced.
  - Partial / not claimed: if new codebase-health obligations are discovered, split them and remove #13 from `Closes`.
  - Evidence required: final issue-tree check before ready.
  - Current evidence: issue body lists only #1/#2.

## Automated gates

Keep draft until every report item is either fixed or explicitly falsified against current source, and parent #13 closure is still valid.
