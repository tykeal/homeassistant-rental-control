<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

<!--
# Sync Impact Report
**Version Change**: 0.0.0 → 1.0.0
**Change Type**: MAJOR - Initial constitution ratification

## Modified Principles
- NEW: Principle I - Code Quality & Testing Standards
- NEW: Principle II - Atomic Commit Discipline
- NEW: Principle III - Licensing & Attribution Standards
- NEW: Principle IV - Pre-Commit Integrity (NON-NEGOTIABLE)
- NEW: Principle V - Agent Co-Authorship & DCO Requirements (NON-NEGOTIABLE)
- NEW: Principle VI - User Experience Consistency
- NEW: Principle VII - Performance Requirements

## Added Sections
- Core Principles (complete)
- Development Standards (complete)
- Governance (complete)

## Templates Review Status
- ✅ `.specify/templates/plan-template.md` - reviewed, constitution check section compatible
- ✅ `.specify/templates/spec-template.md` - reviewed, requirements alignment compatible
- ✅ `.specify/templates/tasks-template.md` - reviewed, task organization compatible
- ⚠️  `.specify/templates/commands/*.md` - not present, no updates needed

## Follow-up TODOs
- None - all placeholders filled

## Notes
This is the initial ratification establishing governance for the Rental Control project.
All future code changes must comply with these principles. The constitution emphasizes
small atomic commits, proper licensing, pre-commit hook compliance, and DCO/co-authorship
requirements for agent contributions.
-->

# Rental Control Constitution

## Core Principles

### Principle I: Code Quality & Testing Standards

**Rule**: All code changes MUST meet the following quality standards:

- **Test Coverage**: Code MUST be tested. The project maintains 100% coverage requirement
  (per setup.cfg). New code without adequate test coverage is PROHIBITED.
- **Type Safety**: All Python code MUST include type hints and pass mypy validation.
- **Documentation**: All public functions MUST have docstrings (per interrogate hook
  configuration requiring 100% documentation).
- **Code Style**: All code MUST conform to project linting standards (ruff, yamllint,
  etc.) as defined in `.pre-commit-config.yaml`.
- **Testability**: Code MUST be designed for testability - avoid tight coupling,
  support dependency injection where appropriate.

**Rationale**: This is a Home Assistant integration managing rental properties and lock
codes. Code defects can result in security issues (incorrect lock codes) or service
disruption (tenants unable to access properties). High quality standards are essential
for reliability and security.

### Principle II: Atomic Commit Discipline (NON-NEGOTIABLE)

**Rule**: All code changes MUST be delivered in small atomic commits where each commit
represents ONE logical change only.

- Each commit MUST compile/run successfully.
- Each commit MUST represent a single logical change (one feature, one fix, one
  refactor).
- Each commit MUST have a clear conventional commit message following the project's
  gitlint configuration (types: Fix, Feat, Chore, Docs, Style, Refactor, Perf, Test,
  Revert, CI, Build).
- Large features MUST be broken into multiple atomic commits.
- Mixing unrelated changes in a single commit is PROHIBITED.

**Rationale**: Atomic commits enable:
- Clean git history for debugging and auditing
- Easy reversion of specific changes without affecting unrelated code
- Clear code review process
- Bisect-friendly debugging when issues arise

### Principle III: Licensing & Attribution Standards (NON-NEGOTIABLE)

**Rule**: Each new or modified source file MUST include correct SPDX license and
copyright headers.

**Required Headers**:
```
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: YYYY Name <email@domain>
```

Or for block comment languages:
```
<!--
SPDX-License-Identifier: Apache-2.0
SPDX-FileCopyrightText: YYYY Name <email@domain>
-->
```

**Enforcement**: The reuse-tool pre-commit hook validates compliance. Files without
proper headers will be rejected.

**Rationale**: The project uses the REUSE specification for license compliance.
Proper attribution protects contributors and users, ensures clear licensing terms,
and maintains compliance with open source best practices and Apache 2.0 license
requirements.

### Principle IV: Pre-Commit Integrity (NON-NEGOTIABLE)

**Rule**: All pre-commit hooks MUST pass locally prior to any push. Bypassing hooks
is PROHIBITED.

**Failure Recovery Protocol**:
- If pre-commit hooks fail, the commit MUST be redone from scratch.
- Do NOT amend failing commits - this can mask the true state of changes.
- Redo the commit as if it was never made, fixing the issues first.

**Pre-Commit Requirements**:
All commits must pass the following validations (per `.pre-commit-config.yaml`):
- File integrity checks (no large files, valid AST, proper line endings)
- Conventional commit message format (via gitlint)
- Code formatting and linting (ruff, ruff-format)
- YAML validation (yamllint)
- Type checking (mypy)
- Documentation coverage (interrogate - 100% required)
- License compliance (reuse-tool)
- GitHub Actions validation (actionlint)

**Rationale**: Pre-commit hooks are the first line of defense against defects,
security issues, licensing violations, and technical debt. Bypassing them creates
risk for the entire codebase and can introduce issues that affect production
deployments managing real tenant access to rental properties.

### Principle V: Agent Co-Authorship & DCO Requirements (NON-NEGOTIABLE)

**Rule**: All commits authored by AI agents MUST include proper attribution and
sign-off.

**Required Commit Trailers**:
Every agent-authored commit MUST include:
1. **Co-Authored-By line**: Identifying the AI agent
   ```
   Co-Authored-By: GitHub Copilot <copilot@github.com>
   Co-Authored-By: Claude <claude@anthropic.com>
   ```
   (Use appropriate agent name and a representative email address)

2. **DCO Sign-off line**: Added via `git commit -s`
   ```
   Signed-off-by: Human Author <human@email>
   ```

**Execution**: Use `git commit -s` to automatically add DCO sign-off, then manually
add Co-Authored-By trailer before finalizing commit.

**Rationale**: Transparency in authorship is critical for:
- Legal compliance (Developer Certificate of Origin)
- Audit trail for code provenance
- Understanding AI contribution patterns
- Maintaining trust in the development process

### Principle VI: User Experience Consistency

**Rule**: User-facing features MUST maintain consistency with existing Home Assistant
integration patterns and Rental Control conventions.

- Configuration UI MUST follow Home Assistant config flow patterns.
- Entity naming MUST follow established conventions
  (e.g., `sensor.rental_control_<calendar>_event_N`).
- State attributes MUST maintain backward compatibility unless explicitly versioned.
- Calendar integration MUST follow HA calendar entity specifications.
- Error messages MUST be clear, actionable, and user-friendly.

**Rationale**: Users depend on predictable behavior. This integration manages critical
access control for rental properties. Inconsistent UX can lead to user errors resulting
in tenant access issues or security problems.

### Principle VII: Performance Requirements

**Rule**: Code changes MUST meet performance requirements appropriate for Home Assistant
integration constraints.

- **Calendar refresh**: Configurable from 30s (minimum) to 1440 minutes (daily) with
  default at 2 minutes - new code must not degrade refresh performance.
- **Event processing**: Must efficiently handle multiple calendars with multiple events
  without blocking the HA event loop.
- **Memory efficiency**: Integration must remain lightweight - avoid memory leaks,
  cache responsibly.
- **Async patterns**: Use Home Assistant async patterns - avoid blocking I/O operations.
- **Lock code generation**: Must complete within sensor update cycle (typically 30s).

**Performance testing**: Changes affecting calendar processing, event parsing, or lock
code generation MUST be performance tested before merging.

**Rationale**: Home Assistant runs on diverse hardware (Raspberry Pi to full servers).
Poor performance degrades the entire HA instance. Calendar-based automation depends on
timely event updates - delays can cause automation failures affecting tenant access.

## Development Standards

### Git Workflow Requirements

- **Branch Protection**: Direct commits to `main` are PROHIBITED (enforced via
  pre-commit hook).
- **Conventional Commits**: REQUIRED for all commits (enforced via gitlint).
- **Signed Commits**: DCO sign-off REQUIRED via `git commit -s`.
- **Pull Requests**: All changes MUST go through PR review before merging to main.

### Testing Requirements

- **Coverage**: 100% code coverage REQUIRED (per setup.cfg).
- **Test Types**: Unit tests for all business logic, integration tests for HA
  component integration.
- **Test Execution**: Tests MUST pass before commit (verified via pre-commit if
  configured, or manually).

### Code Review Standards

- Reviewers MUST verify constitutional compliance.
- Reviewers MUST check for atomic commit structure.
- Reviewers MUST verify pre-commit hooks passed.
- Reviewers MUST verify proper licensing headers.
- Reviewers MUST check for agent co-authorship when applicable.

## Governance

### Constitutional Authority

This constitution supersedes all other development practices and guidelines.
When conflicts arise, constitutional principles take precedence.

### Amendment Process

**Version Format**: MAJOR.MINOR.PATCH

- **MAJOR**: Backward-incompatible changes, principle removal/redefinition, or
  fundamental governance changes.
- **MINOR**: New principles added, sections expanded, new mandatory requirements.
- **PATCH**: Clarifications, wording improvements, typo fixes without semantic changes.

**Amendment Requirements**:
1. Proposed changes MUST be documented with rationale.
2. Impact assessment MUST identify affected templates and workflows.
3. All `.specify/templates/*.md` files MUST be updated for consistency.
4. Version increment MUST follow semantic versioning rules above.
5. Amendment history MUST be preserved in Sync Impact Report comments.

### Compliance Review

**Mandatory Review Points**:
- Every PR merge (verify constitutional compliance in review)
- Every sprint/milestone retrospective (check for systematic violations)
- Every quarter (constitution effectiveness review)

**Violation Response**:
- **Pre-commit violations**: Commit rejected, redo required
- **PR violations**: PR rejected until corrected
- **Post-merge violations**: Immediate corrective commit required

### Development Guidance Integration

For runtime agent guidance, refer to `.specify/templates/agent-file-template.md` and
related command templates in `.specify/templates/commands/*.md` (when present).

All development tooling and agent instructions MUST align with constitutional principles.

---

**Version**: 1.0.0 | **Ratified**: 2025-11-15 | **Last Amended**: 2025-11-15
