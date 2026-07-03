## Intended result

The repository's normal verification gates are useful again: pytest no longer has the stale `list` diagnostic failure, `just test` reaches a clean mypy result, and `just test-ci` is no longer blocked by pre-existing `POLICY.NO_BOOLEAN_MODE` findings.

## Scope

- Included: #64 stale CLI misuse diagnostic, #63 pre-existing mypy errors, #49 pre-existing boolean-mode policy findings.
- Excluded: functional CLI features such as todo mutation (#62), vault-reading resilience (#52), and worktree side-effect bug #66.
- Preserved behavior: do not make checks green by weakening policy, blanket-suppressing findings, widening types to `Any`, or deleting meaningful tests.

## GitHub tracking

- Target issue set: #64, #63, #49
- Milestone: unassigned QC/gate debt work unit.
- Closes on merge:
  - Closes #64
  - Closes #63
  - Closes #49
- References only:
  - Refs #41
  - Refs #65

## Implementation plan

Resolve the gates from fastest signal to deepest policy risk: first decide and fix the stale `list` misuse test in #64; then remediate the typed-construction and test typing errors in #63 with explicit types or narrowed boundary casts; finally address #49 by splitting boolean mode flags into intention-named paths or documenting a scoped, policy-compatible exception only where the boolean is true data rather than a behavior mode.

## Claim map

- [ ] **#64 - pytest misuse diagnostic matches the actual CLI surface**
  - Proof obligations claimed: the misuse test still exercises a genuine unknown-command path or the CLI command set is corrected if `list` was accidental.
  - Partial / not claimed: no unrelated CLI redesign.
  - Evidence required: failing-before/green-after targeted test plus full pytest or documented gate context.
  - Current evidence: issue reproducer only.

- [ ] **#63 - mypy gate has no pre-existing error floor**
  - Proof obligations claimed: all 30 listed errors are fixed by real typing/narrowing, not hidden by blanket `Any` or broad ignores.
  - Partial / not claimed: no broad model rewrite beyond what the type errors require.
  - Evidence required: `just -f ~/ai-review-ci/justfiles/python.just -d . _mypy` green and `just test` reaching the next stage cleanly.
  - Current evidence: issue reproducer only.

- [ ] **#49 - push-tier boolean-mode policy findings are remediated**
  - Proof obligations claimed: every listed `POLICY.NO_BOOLEAN_MODE` finding is removed by policy-preserving API shape, or a scoped data-not-mode disposition is justified for a specific finding.
  - Partial / not claimed: no weakening of central ai-review-ci policy rules.
  - Evidence required: `just test-ci` no longer reports the listed pre-existing findings; any exception is precise and reviewable.
  - Current evidence: issue reproducer only.

## Automated gates

Keep draft until the PR body cites current passing evidence for pytest, mypy, and push-tier QC, or names a newly discovered blocker as a separate issue without claiming this gate-restoration unit complete.
