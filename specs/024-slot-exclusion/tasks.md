<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Slot-Exclusion Recovery Control

**Input**: Design documents from `specs/024-slot-exclusion/`
**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md),
[research.md](research.md), [data-model.md](data-model.md),
[contracts/set-slot-exclusion-service.md](contracts/set-slot-exclusion-service.md),
[quickstart.md](quickstart.md)
**Feature branch (implementation)**: `024-slot-exclusion`
**Issue**: #802

**Tests**: Test tasks are included and are mandatory. The spec's success
criteria are behavioural, the plan names two required regressions, and the
repository enforces a 95% coverage floor, so new modules need tests in the same
commit as the code they cover.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: May run in parallel — different files, no dependency on an
  incomplete task. Tasks touching the same file are never marked `[P]`.
- **[Story]**: The user story the task serves (US1-US5). Setup, Foundational,
  and Polish tasks carry no story label.
- **⚠️ ATOMIC**: Tasks sharing an atomic-commit group MUST be committed
  together in one commit. Shipping them separately leaves a known-unsafe or
  known-inconsistent tree.

## Validation commands

```bash
uv run pytest tests/ -q -p no:randomly
uv run ruff check custom_components/ tests/
uv run pre-commit run --all-files
```

Every new Python file needs the SPDX header pair. Coverage floor is 95%. Never
pass `--no-verify`, `--no-gpg-sign`, or `git reset` after a failed commit.

`pytest-randomly` is not installed in this environment: `-p no:randomly` is
harmless, but `--randomly-seed` is an unrecognised argument. `-q` output is of
the form `. [1/1]`, so do not assert on a `"N passed"` string.

## Phase ordering note

US1 (unfreeze one property) and US2 (unfreeze unrelated properties) are both P1
and are delivered by **exactly the same mechanism** — the three gate members
plus persistence. US3 (auto-clear) is also P1 and is delivered by the
re-validation phase. US4 (standing visibility) and US5 (deliberate reversal)
are P2 and add only surfaces and a flag direction.

The phases below are therefore ordered by **safety dependency**, not by story
listing order:

- **The pure primitives land before anything consumes them** (Phase 2). The
  parser must be total before any entry's setup can depend on it, or a
  malformed persisted value becomes a setup failure rather than a dropped
  entry (FR-032, SC-010).
- **The claimed-slot extraction lands before the guards use it** (Phase 3),
  as a pure refactor with no behaviour change, so the gate and the service can
  never disagree about what "claimed" means.
- **All three gate members land together** (Phase 4). A tree with one or two of
  them behaves inconsistently for one slot depending on whether the caller is
  ordinary issuance, adoption, or the re-issue preview, and `force_reissue`
  stays blocked while issuance has resumed. The read-only loader lands here
  rather than in Phase 5, because the preview member consumes it; reading a
  stored set is safe in a way that writing or trusting one is not.
- **The service honours `dry_run` from its first commit** (Phase 6). The flag
  is implemented in the same task as the handler and before the registration
  that makes the service callable, so no shippable stage accepts the key and
  ignores it.
- **The service comes after the machinery** (Phase 6), because it is the only
  thing that can create an exclusion and must not exist before the code that
  honours one and retires it. An exclusion created before Phase 5 exists would
  be a persisted value nothing re-validates — which is the 3.5.0 failure mode.

---

## Phase 1: Setup

**Purpose**: Establish the behaviour oracle and confirm the live signatures
this feature modifies still look the way the design documents claim.

- [ ] T001 Run and record the pre-change baseline with
      `uv run pytest tests/ -q -p no:randomly` and
      `uv run ruff check custom_components/ tests/`
      ([quickstart.md](quickstart.md) "Validation gate")
- [ ] T002 Re-read and record the live signatures this feature changes, per
      [quickstart.md](quickstart.md) "Before you start": `unaccounted_slots`,
      `resolve_cycle`, `allocate_request`, `_track_pending_adoption_slots` in
      `custom_components/rental_control/allocator/issuance.py`;
      `_release_guard_reason`, `_owner_still_programmed` in
      `custom_components/rental_control/allocator/allocator.py`;
      `preview_request_and_result` in
      `custom_components/rental_control/allocator/reissue_preview.py`;
      `register_allocator_services`, `_collect_observations` in
      `custom_components/rental_control/allocator/services.py`;
      `allocator_diagnostics` in
      `custom_components/rental_control/allocator/diagnostics.py`;
      `CycleRequest`, `CycleObservation` in
      `custom_components/rental_control/allocator/models.py`;
      `async_resolve_codes`, `_adoption_complete`, `build_cycle_observation`,
      `report_unreadable_slots` in
      `custom_components/rental_control/coordinator_helpers/code_allocation.py`;
      `CodeResolutionRequest` in
      `custom_components/rental_control/coordinator_helpers/reissue.py`;
      `resolve_slot_target`, `_entry_from_sensor_state` in
      `custom_components/rental_control/coordinator_helpers/reissue_targets.py`;
      and `update_listener` in
      `custom_components/rental_control/__init__.py` — adjusting the tasks
      below where source and the design documents disagree
      ([research.md](research.md#appendix-corrected-citations) records the
      drift found during planning)

**Checkpoint**: Baseline is green and the signatures to be modified are known
from source, not from prose.

---

## Phase 2: Foundational - Pure Exclusion Primitives (Blocking Prerequisite)

**Purpose**: Land the constant, the total persisted-shape parser, and the three
refusal guards as pure, Home-Assistant-free code. **No task in any later phase
may read or write a persisted exclusion until this phase is complete.**

**⚠️ ATOMIC group A** — T003 through T006 ship as ONE commit. The constant, the
parser, the serialiser, and the guard evaluation are one coherent unit with no
consumers; splitting them leaves a key with no reader or a reader with no key.
The tolerance tests ship with the parser because FR-032 and SC-010 are the
parser's entire contract: an untested parser that raises on a malformed value
turns a cosmetic data problem into a failed entry setup.

- [ ] T003 Add `CONF_EXCLUDED_SLOTS = "excluded_slots"` to
      `custom_components/rental_control/const.py` in alphabetical position with
      the other `CONF_` names
      ([data-model.md](data-model.md) "Changed entities")
- [ ] T004 Create `custom_components/rental_control/allocator/exclusions.py`
      with the **total** `parse_excluded_slots(raw) -> dict[int, str | None]`
      and `serialize_excluded_slots(exclusions) -> list[dict[str, Any]]`,
      emitting slots in ascending order so an unchanged set serialises
      identically; the parser must never raise and must drop non-list input,
      non-mapping members, missing or non-integer or boolean or `< 1` `slot`
      values, and must keep a member with a non-string `reason` as
      `reason=None` ([data-model.md](data-model.md) "Parsing rules") — FR-006,
      FR-032
- [ ] T005 Add `refusal_for_exclusion(slot, *, start_slot, max_events,
      unreadable_slots, managed_slots, claimed_slots) -> str | None` to
      `custom_components/rental_control/allocator/exclusions.py`, returning
      `slot_out_of_range`, then `slot_readable`, then `slot_claimed` in that
      order, and importing nothing from Home Assistant
      ([contracts/set-slot-exclusion-service.md](contracts/set-slot-exclusion-service.md)
      "Validation order") — FR-009, FR-010, FR-011
- [ ] T006 Create `tests/unit/test_slot_exclusions.py` covering the parser's
      tolerance table (absent key, `None`, `""`, a string, a mapping, a list of
      integers, malformed members, duplicate slots), round-trip stability of
      `serialize_excluded_slots`, and each of the three refusal guards
      including their evaluation order — FR-032, SC-010, SC-005

**Checkpoint**: The persisted shape can be read and written safely and the
three guards are provable without Home Assistant. Nothing consumes them yet.

---

## Phase 3: Foundational - One Definition of "Claimed"

**Purpose**: Make the gate and the service share a single implementation of the
claim check, as a pure refactor with no behaviour change, before either depends
on it.

- [ ] T007 Extract the claim-set computation at
      `custom_components/rental_control/allocator/issuance.py:245-252` into a
      module-level `claimed_slots(allocator, entry_id, lockname) ->
      frozenset[int]` and call it from `unaccounted_slots`, changing no
      behaviour
      ([contracts/set-slot-exclusion-service.md](contracts/set-slot-exclusion-service.md)
      "`issuance.claimed_slots`")
- [ ] T008 Create `tests/unit/test_exclusion_gate.py` with the pre-change
      oracle: `unaccounted_slots` returns exactly today's results for a claimed
      slot, an unclaimed unreadable slot, a lockless observation, and an
      observation with no unreadable slots — asserting T007 changed nothing

**Checkpoint**: "Claimed" has exactly one implementation and a test that pins
its current behaviour.

---

## Phase 4: The Issuance Gate - All Three Members (US1, US2)

**Purpose**: Teach exactly three checks to subtract the excluded set, carry
that set beside the observation, and prove that no other reader of
`unreadable_slots` changed.

**⚠️ ATOMIC group B** — T009 through T016 ship as ONE commit. A tree with one
or two of the three gate members treats the same slot differently depending on
whether the caller is ordinary issuance, adoption completion, or the re-issue
preview: issuance could resume while `force_reissue` still refuses, or an entry
could complete adoption while its own allocations are refused. The
release-evidence regression ships in the same commit because a gate change
whose blast radius is unproven is indistinguishable from a filter applied at
the observation, which is the critical hazard of this feature. The read-only
loader (T012) ships here too, because T013 consumes it; leaving it in Phase 5
would make this commit import a module that does not yet exist. Only the
loader moves — persistence and re-validation stay together in group C, where
they must be.

- [ ] T009 [US1] Add `excluded_slots: frozenset[int] = frozenset()` to
      `CycleRequest` in
      `custom_components/rental_control/allocator/models.py:193-203`, after
      `forced_reissues`, leaving `CycleObservation` **unchanged**
      ([data-model.md](data-model.md) "Changed entities")
- [ ] T010 [US1] Gate member 1 — add keyword-only
      `excluded: frozenset[int] = frozenset()` to `unaccounted_slots` in
      `custom_components/rental_control/allocator/issuance.py:239-253`,
      returning `observation.unreadable_slots - claimed - excluded`; pass
      `request.excluded_slots` to it from `resolve_cycle` at line 157; and pass
      the **gated** set `request.observation.unreadable_slots -
      request.excluded_slots` to `_track_pending_adoption_slots` at lines
      226-237 so an excluded slot is no longer reported as blocking adoption —
      FR-014
- [ ] T011 [US2] Gate member 2 — add keyword-only
      `excluded: frozenset[int] = frozenset()` to `_adoption_complete` in
      `custom_components/rental_control/coordinator_helpers/code_allocation.py:241-259`
      and change **only** the branch at lines 249-250 to
      `if observation.unreadable_slots - excluded:`, leaving
      `readable_coded_slots <= adopted_slots` exactly as it is — FR-015
- [ ] T012 Create
      `custom_components/rental_control/coordinator_helpers/slot_exclusions.py`
      with **only** the read-only `load(hass, entry_id) -> dict[int, str |
      None]`, reading `CONF_EXCLUDED_SLOTS` from the entry's `data` through
      `parse_excluded_slots` and returning an empty mapping for a missing
      entry — no re-validation, no persistence, no notification, which arrive
      in Phase 5. The loader ships inside atomic group B because T013 consumes
      it; deferring it to Phase 5 would leave group B importing a module that
      has not landed. Extend `tests/unit/test_slot_exclusions.py` with its
      missing-entry, absent-key, and malformed-value cases — FR-031, SC-010
- [ ] T013 Gate member 3 — in
      `custom_components/rental_control/allocator/reissue_preview.py:111-115`
      narrow the raw rejection to `observation.unreadable_slots - excluded`,
      pass `excluded=` to `issuance.unaccounted_slots` at line 113, **and**
      forward the same set through `_adoption_would_complete` at line 115 to
      its `_adoption_complete` call at lines 176-178 — leaving that last call
      on its empty default still returns `adoption_pending` for an excluded
      slot after both narrowed checks pass, which fails FR-019 exactly as a
      missed gate member does. The set is loaded through T012's `load`,
      read-only, with no re-validation and no persistence —
      FR-016, FR-019
- [ ] T014 [US1] Add `excluded_slots: frozenset[int] = frozenset()` to
      `CodeResolutionRequest` in
      `custom_components/rental_control/coordinator_helpers/reissue.py:81-91`
      and thread it to `CycleRequest` in `async_resolve_codes`, leaving
      `coerce_code_resolution_request`'s legacy-call rejection unchanged
- [ ] T015 [US2] Extend `tests/unit/test_exclusion_gate.py`: each of the three
      gate members with an empty excluded set behaves identically to the T008
      oracle (SC-006); with a non-empty set only the excluded slot's
      contribution disappears; and an entry with one excluded dead slot **and**
      one unaccounted readable coded slot stays pending adoption while
      unrelated entries stay blocked — FR-015, SC-006, SC-015
- [ ] T016 Create `tests/unit/test_exclusion_release_evidence.py`, the
      mandatory regression: with an excluded unreadable slot and a registry
      owner recorded against it, `_release_guard_reason` still returns
      `code_still_programmed`; the same with the owner **orphaned** (its
      `entry_id` absent from `async_entries(DOMAIN)`) run through
      `async_clear_orphans` retains the code; and a source-level assertion that
      `build_cycle_observation` contains no reference to exclusions —
      FR-016, FR-018, SC-014

**Checkpoint**: The gate honours an excluded set that nothing yet produces, and
the release-evidence path is provably untouched.

---

## Phase 5: Per-Cycle Re-Validation and Persistence (US3)

**Purpose**: Make the persisted set non-authoritative by re-deriving and
correcting it from the live observation on every cycle, and wire it into the
gate.

**⚠️ ATOMIC group C** — T017 through T020 ship as ONE commit. Persistence
without re-validation is exactly the 3.5.0 failure mode the `0622057` revert
and #607 exist to prevent: a stored value that outlives the condition
justifying it and survives every restart. The two must never exist separately
in the tree, not even for one commit. Reading a stored set carries no such
hazard, which is why T012's loader could land earlier without weakening this
group.

- [ ] T017 [US3] Extend
      `custom_components/rental_control/coordinator_helpers/slot_exclusions.py`,
      whose read-only `load` landed in T012, with the **pure**
      `revalidate(exclusions, observation) -> (kept, cleared)` dropping any
      slot not in `observation.unreadable_slots` (FR-020) or not in
      `observation.managed_slots` (FR-023); and
      `persist_excluded_slots(hass, entry_id, exclusions) -> bool` writing
      through `hass.config_entries.async_update_entry` into the entry's
      **`data`** and returning `False` without writing when the serialised
      value is unchanged
      ([contracts/set-slot-exclusion-service.md](contracts/set-slot-exclusion-service.md)
      "`slot_exclusions`") — FR-020, FR-021, FR-023, FR-029, FR-031
- [ ] T018 [US3] In `async_resolve_codes`
      (`custom_components/rental_control/coordinator_helpers/code_allocation.py:46-146`),
      immediately after `build_cycle_observation` at line 68, load and
      `revalidate` against that same observation object; persist when anything
      cleared; log one INFO line per cleared slot naming entry, lock, slot and
      cause; and pass the kept set to `_adoption_complete` and into
      `CodeResolutionRequest.excluded_slots` — FR-022, FR-024, FR-027
- [ ] T019 Pass the **gated** set `sorted(observation.unreadable_slots -
      excluded)` to `report_unreadable_slots` at
      `custom_components/rental_control/coordinator_helpers/code_allocation.py:120-122`,
      so the "issuance is withheld" notification from `6a6602b` names only
      slots that are actually withholding issuance and dismisses itself when
      the last non-excluded unreadable slot is excluded or recovers
- [ ] T020 [US3] Extend `tests/unit/test_slot_exclusions.py`: auto-clear within
      one cycle when the slot becomes readable (SC-003); drop when the managed
      range narrows (FR-023); `persist_excluded_slots` writes nothing when the
      set is unchanged; an entry whose persisted set references slots that no
      longer exist sets up successfully (SC-010); and the **no-reload-loop**
      regression — persisting while `config_entry.options` is empty invokes
      `update_listener` (`__init__.py:256-281`) and returns without rebuilding
      the entry
- [ ] T021 [P] Create `tests/unit/test_exclusion_preview.py` asserting the
      re-issue preview path applies the excluded set to both gate checks but
      performs zero re-validation, zero persistence, and zero notification
      calls — FR-007, FR-019, SC-007

**Checkpoint**: An exclusion placed in an entry's `data` by hand now unfreezes
issuance and disappears by itself when the slot returns. No operator-facing way
to create one exists yet.

---

## Phase 6: The Service Surface (US1, US2, US5)

**Purpose**: Give the operator the one deliberate, single-slot, un-overridable
control that creates and clears an exclusion.

- [ ] T022 Rename `_entry_from_sensor_state`
      (`custom_components/rental_control/coordinator_helpers/reissue_targets.py:127-144`)
      to a public `entry_id_for_entity_attributes` and export it, updating its
      existing caller, with no behaviour change — so the captive-portal
      sensor-attribute contract has exactly one reader
- [ ] T023 [US1] Create
      `custom_components/rental_control/coordinator_helpers/exclusion_targets.py`
      with `ExclusionTarget(entry_id, lockname, slot, coordinator)` and a
      resolver emitting `ambiguous_target`, `missing_target`,
      `unknown_entity`, `unknown_lock`, and `ambiguous_lock`, reusing
      `entry_id_for_entity_attributes` for the entity path and **not** reusing
      `resolve_slot_target`, which refuses with `slot_not_managed` before
      `slot_out_of_range` can be emitted and cannot express FR-012
      ([plan.md](plan.md) "Target resolution") — FR-002, FR-003
- [ ] T024 [US1] Create
      `custom_components/rental_control/allocator/exclusion_service.py` with
      `SERVICE_SET_SLOT_EXCLUSION`, the `ExclusionRequest` value object, and
      `SET_SLOT_EXCLUSION_SCHEMA` — `slot` **required**, `excluded` defaulting
      to `True`, optional `reason`, `dry_run` defaulting to `False`, and
      **no `force` key**
      ([contracts/set-slot-exclusion-service.md](contracts/set-slot-exclusion-service.md)
      "Service declaration") — FR-001, FR-002, FR-004, FR-005, FR-006, FR-009
- [ ] T025 [US1] [US5] Implement the handler in the same module: resolve the
      target, evaluate `refusal_for_exclusion` using the coordinator's
      `start_slot`/`max_events`, a freshly built observation, and
      `issuance.claimed_slots` — **skipping all three guards when `excluded` is
      false** (FR-012) — then persist through `persist_excluded_slots`, log at
      INFO, and return the response contract, reporting `changed: false` and
      writing nothing when the resulting set equals the stored set; every
      refusal returns before any persist, notification, or INFO log.
      **The `dry_run` branch is part of this task, not a later one**: compute
      the resulting set and `changed`, or the refusal that would apply, and
      return **before** any persistence, any notification call, and any
      re-validation. T024 already accepts the key, and T026 registers this
      handler, so implementing the branch later would ship a callable service
      where `dry_run: true` persists an exclusion and reopens issuance —
      FR-007, FR-008, FR-012, FR-013, FR-027
- [ ] T026 Register the service from `register_allocator_services`
      (`custom_components/rental_control/allocator/services.py:39-57`), beside
      `register_force_reissue_service` at line 42, with
      `SupportsResponse.OPTIONAL` and a `has_service` guard
- [ ] T027 [P] Declare `set_slot_exclusion` and its six fields in
      `custom_components/rental_control/services.yaml`,
      `custom_components/rental_control/strings.json`,
      `custom_components/rental_control/translations/en.json`, and
      `custom_components/rental_control/translations/fr.json`, with the service
      description stating the FR-030 residual risk and that an options-flow
      save drops exclusions — FR-030, FR-035
- [ ] T028 [P] [US1] Create `tests/unit/test_exclusion_targets.py` covering
      entity targeting, lock targeting, both supplied, neither supplied, an
      unknown lock, and a lock served by two entries — FR-002, FR-003
- [ ] T029 [US1] [US5] Create `tests/unit/test_exclusion_service.py` covering
      each of `slot_readable`, `slot_claimed`, and `slot_out_of_range` refused
      with nothing persisted; an assertion that the schema has no `"force"` key
      and accepts only a scalar `slot`; clearing succeeding for a readable,
      claimed, and out-of-range slot; a repeated set and a repeated clear each
      reporting `changed: false` with no write; the full response shape; and
      every refusal path and every success path exercised again with
      `dry_run: true`, asserting zero config-entry writes, zero notification
      calls, and an unchanged next cycle — FR-004, FR-007, FR-009, FR-012,
      FR-013, SC-005, SC-007

**Checkpoint**: **MVP.** US1, US2, and US5 are complete and the reported defect
in #802 is remedied end to end for an operator with a frozen property.

---

## Phase 7: Operator-Visible Surfaces (US4)

**Purpose**: Make a standing accepted risk impossible to forget, and make a
support request answerable without further questions.

- [ ] T030 [US4] Add `report_excluded_slots` and `dismiss_excluded_slots` to
      `custom_components/rental_control/coordinator_helpers/slot_exclusions.py`
      with a per-entry notification id `f"{DOMAIN}_excluded_slots_{entry_id}"`,
      following the reconcile-and-dismiss shape of `report_unreadable_slots`
      (`coordinator_helpers/code_allocation.py:160-195`); the message names the
      entry, the lock, the excluded slots with their recorded reasons, and
      states the FR-030 residual risk that the slot may still hold a code the
      integration cannot see — FR-025, FR-030
- [ ] T031 [US4] Call `report_excluded_slots` from `async_resolve_codes` on
      every cycle with the post-re-validation set, so the notification is
      reconciled every cycle and dismissed when the set becomes empty —
      FR-025, SC-008
- [ ] T032 [US4] Dismiss the exclusion notification in
      `custom_components/rental_control/__init__.py` beside the existing
      `dismiss_unreadable_slots` calls at lines 203 and 214, covering both
      unload and permanent removal
- [ ] T033 [P] [US4] Add an `excluded_slots` key to `allocator_diagnostics`
      (`custom_components/rental_control/allocator/diagnostics.py:18-58`)
      mapping `entry_id` to its sorted slots and reasons, derived by walking
      `async_entries(DOMAIN)` through `parse_excluded_slots` and omitting
      entries with an empty set — FR-026
- [ ] T034 [P] [US4] Create `tests/integration/test_exclusion_notifications.py`
      asserting exactly one standing notification per entry with a non-empty
      set and zero otherwise at the end of every cycle; that the unreadable-slot
      notification names only non-excluded slots; and that both are dismissed on
      unload and on removal — SC-008
- [ ] T035 [P] Create `tests/unit/test_exclusion_no_codes.py` asserting no door
      code appears in the service response, the notification message, the
      diagnostics payload, or any captured log record attributable to this
      feature — FR-028, SC-009

**Checkpoint**: US4 complete. An exclusion is visible for as long as it stands
and invisible the moment it clears.

---

## Phase 8: End-to-End Scenarios

**Purpose**: Prove the spec's measurable outcomes as behaviour, not as unit
assertions about parts.

- [ ] T036 Add the shared integration fixtures: one entry with one permanently
      `UNKNOWN` managed slot, and a three-entry fixture on three locks where
      each entry has a new reservation holding **no** existing registry
      allocation, so that existing-code reuse cannot mask the gate
- [ ] T037 [P] [US1] [US3] Create `tests/integration/test_exclusion_recovery.py`
      driving the whole SC-012 arc with no intervention after the single
      service call: freeze observed with `unaccounted_slots`; exclude; next
      cycle issues to every remaining reservation while the excluded slot is
      never written to, never cleared, and never offered as free; slot becomes
      readable; next cycle clears the exclusion from the entry's stored data
      and dismisses the notification; slot becomes unreadable again and the
      entry freezes fail-closed afresh — SC-001, SC-003, SC-004, SC-012
- [ ] T038 [P] [US2] Create `tests/integration/test_exclusion_multi_entry.py`:
      three loaded entries, one with an `UNKNOWN` managed slot; after a
      restart, the two healthy entries are refused with `adoption_pending` and
      the affected entry submits no allocation request at all; one exclusion on
      the affected entry; all three then issue; and a further restart requires
      no additional operator action — SC-002
- [ ] T039 [P] Create `tests/integration/test_exclusion_force_reissue.py`:
      `force_reissue` succeeds on a non-excluded slot of an entry whose only
      unreadable slot is excluded, failing if gate member 3 was missed —
      FR-019, SC-011
- [ ] T040 [P] Create `tests/integration/test_exclusion_edge_cases.py`:
      excluding every managed slot satisfies the gate but reports ordinary
      exhaustion rather than silent success; two entries managing overlapping
      slot numbers on one parent lock do not share an exclusion; an
      options-flow save drops every exclusion and returns the entry to
      fail-closed; and an excluded slot still classifies as
      `ActionKind.BLOCKED, "unreadable"` with no file under
      `custom_components/rental_control/reconciliation/` modified — FR-017,
      SC-004

**Checkpoint**: Every success criterion with a behavioural claim has an
asserting test.

---

## Phase 9: Polish & Cross-Cutting Concerns

- [ ] T041 Document the service in `README.md` beside the existing
      `rental_control.force_reissue` section at line 180, stating the three
      guards, the absence of any override, the auto-clear, the residual risk,
      and that an options-flow save drops exclusions
- [ ] T042 Final gate: `uv run pre-commit run --all-files`,
      `uv run pytest tests/ -q -p no:randomly`, and
      `uv run ruff check custom_components/ tests/`; confirm the 95% coverage
      floor; confirm every new file carries its SPDX header pair; and confirm
      by review that no config or options flow field was added (FR-034), that
      no exclusion is ever created except by an explicit service invocation
      (FR-033), and that nothing under `reconciliation/` changed

---

## Dependencies

### Phase-level

```text
Phase 1 Setup
    └─► Phase 2 Pure primitives (ATOMIC A)   ← nothing may read a stored set first
            └─► Phase 3 Claimed-slot extraction
                    └─► Phase 4 The gate (ATOMIC B)
                            └─► Phase 5 Re-validation + persistence (ATOMIC C)
                                    └─► Phase 6 Service surface, incl. dry run
                                            └─► Phase 7 Surfaces (US4)
                                                    └─► Phase 8 End-to-end
                                                            └─► Phase 9 Polish
```

### Hard task-level edges

- T003 → T004 → T005 → T006 (the key exists before the parser, the parser
  before the guards, the guards before their tests) — and T003 through T006
  ship in **one commit** (ATOMIC A)
- T007 → T005's consumer in T025 and → T010 (one definition of "claimed"
  precedes both callers)
- T008 → T015 (the oracle precedes the comparison it anchors)
- T009 → T010, T014 (the request field exists before `resolve_cycle` reads it)
- T009 through T016 ship in **one commit** (ATOMIC B)
- T010, T011, T013 → T015 (all three members exist before the test that
  compares them)
- T004 → T012 → T013 (the parser precedes the loader, the loader precedes the
  preview that reads through it)
- T004 → T017 (the parser precedes re-validation and persistence)
- T012 → T017 → T018 → T019 (the loader exists before re-validation extends
  its module, and both precede anything consuming the gated set)
- T017 through T020 ship in **one commit** (ATOMIC C)
- T013, T017 → T021 (the preview applies a set it can load)
- T022 → T023 → T024 → T025 → T026 (resolver, schema, handler, registration).
  The dry-run branch is inside T025, so no shippable stage registers a handler
  that accepts `dry_run` without honouring it
- T017 → T030 → T031 → T032 (the notification helper precedes its callers)
- T004 → T033 (diagnostics parse through the same tolerant parser)
- T026, T031 → T036 (end-to-end fixtures need both the service and the
  surfaces)
- T036 → T037, T038, T039, T040
- Phases 2-7 → T041, T042
- Everything → T042

### Atomic-commit groups

| Group | Tasks | Why it must be one commit |
|-------|-------|---------------------------|
| A | T003, T004, T005, T006 | The constant, the parser, the serialiser, and the guard evaluation have no consumers and are meaningless apart: a key with no reader, or a reader with no key. The tolerance tests ship with them because FR-032 and SC-010 *are* the parser's contract — an untested parser that raises on a malformed value turns a cosmetic data problem into a failed entry setup. |
| B | T009, T010, T011, T012, T013, T014, T015, T016 | Fewer than all three gate members means one slot is treated differently depending on the caller: issuance can resume while `force_reissue` still refuses with `unaccounted_slots`, or an entry can complete adoption while its own allocations are refused. The release-evidence regression ships with them because a gate change whose blast radius is unproven is indistinguishable from the forbidden filter-at-the-observation, which would release codes that may still be physically programmed. |
| C | T017, T018, T019, T020 | Persistence without per-cycle re-validation is precisely the 3.5.0 failure mode that `0622057` reverted and #607 redesigned away: a stored value that outlives the condition justifying it and survives every restart. The two must never exist separately in the tree. T019 joins them because a persisted exclusion with the old raw-set notification leaves the operator holding two standing notifications that contradict each other about one slot. |

Every other task is sized to stand alone as one coherent commit.

## Parallel execution examples

**Phase 5** — T021 (`tests/unit/test_exclusion_preview.py`) touches no file in
atomic group C and runs in parallel with it once T013 and T017 are done.

**Phase 6** — T027 (`services.yaml`, `strings.json`, both translations) and
T028 (`tests/unit/test_exclusion_targets.py`) are two disjoint file sets and
run in parallel with each other and with T029 once T026 is done.

**Phase 7** — T033 (`allocator/diagnostics.py`), T034
(`tests/integration/test_exclusion_notifications.py`), and T035
(`tests/unit/test_exclusion_no_codes.py`) are three different files and run in
parallel once T031 is done.

**Phase 8** — T037, T038, T039, and T040 are four different integration test
files and run in parallel once T036's fixtures exist.

**Not parallel, despite looking it**: T007 and T010 both edit
`allocator/issuance.py`; T011, T018, and T019 all edit
`coordinator_helpers/code_allocation.py`; T012, T017, and T030 all edit
`coordinator_helpers/slot_exclusions.py`; T024 and T025 both edit
`allocator/exclusion_service.py`; T004 and T005 both edit
`allocator/exclusions.py`; T006, T012, and T020 all edit
`tests/unit/test_slot_exclusions.py`; T008 and T015 both edit
`tests/unit/test_exclusion_gate.py`; T022 and T023 are sequential because
T023 consumes the symbol T022 exports.

## Implementation strategy

1. **Safety before capability.** The gate learns to honour an excluded set
   (Phase 4) and the system learns to retire one (Phase 5) before anything can
   create one (Phase 6). At no point is the tree in a state where a persisted
   exclusion exists that nothing re-validates, which is the 3.5.0 failure mode.
2. **The filter never moves to the observation.** This is the one way to
   implement the feature that looks right and is dangerous. T016 proves it has
   not happened, including the orphan case that no other guard can catch, and
   asserts it at the source level so a later refactor cannot quietly undo it.
3. **MVP = Phase 1 through Phase 6.** That remedies the defect reported in
   #802 for both freeze regimes (SC-001, SC-002), gives the operator a way to
   reverse a mistake (US5), and a way to rehearse one without consequence
   (dry run, FR-007). The auto-clear that makes the persisted set acceptable
   (US3) is already in place by Phase 5, before any exclusion can be created.
4. **No half-honoured flag.** Dry run is implemented in the same task as the
   handler it guards and before the registration that makes the service
   callable, so `dry_run: true` is never a key the service accepts and
   ignores.
5. **Then visibility (Phase 7)**, which makes the accepted risk impossible to
   forget.
6. **Scope discipline throughout.** No bulk or wildcard mode, no force flag or
   override of any kind, no automatic or proposed exclusion, no timeout or
   expiry of the gate, no new configuration or options-flow field, no new
   sensor attribute, no change under `reconciliation/`, and **no
   hand-maintained changelog file** — this repository generates its release
   notes automatically from merged pull requests through release-drafter
   (`.github/release-drafter.yml`), has never carried a `CHANGELOG.md`, and
   must not gain one as a side effect of a feature PR. Pre-existing oversized
   modules unrelated to this feature are known tech debt and stay out of this
   PR. An adjacent defect gets an issue, not a wider PR.

## Task summary

- **Total tasks**: 42
- **Setup**: 2 · **Pure primitives**: 4 · **Claimed extraction**: 2 ·
  **The gate**: 8 · **Re-validation**: 4 · **Service surface**: 8 ·
  **Surfaces**: 6 · **End-to-end**: 5 · **Polish**: 2
- **Parallelizable**: 10 tasks marked `[P]`
- **Atomic-commit groups**: 3 (group A: 4 tasks, group B: 8 tasks,
  group C: 4 tasks)
- **Requirements coverage**: FR-001 to FR-035 each map to at least one task.
  FR-033 (no automatic exclusion) and FR-034 (no new configuration option) are
  negative scope constraints with no behaviour to assert directly and map to
  T042 as review-verified gates. SC-001 to SC-015 each have at least one
  asserting test task, except SC-013 (zero new configuration values, zero
  Keymaster operations), which is the same negative constraint as FR-034 and is
  review-verified by T042.
