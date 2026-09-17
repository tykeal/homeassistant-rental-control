<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Shared Door Code Allocator with Persisted Registry

**Feature Branch**: `022-shared-code-allocator`
**Created**: 2026-09-16
**Status**: Draft
**Input**: GitHub issue #743 — "Add shared door code allocator with persisted
registry". A production property runs ten Rental Control config entries against
one shared parent lock. Two entries generated the same door code. Because a
downstream captive-portal service authenticates guests by door code across all
integrations, a duplicate code authorizes the wrong guest. Each config entry
today generates codes in isolation from booking-local data only, so nothing can
detect that a sibling entry already issued a code.

## Context

Door codes are generated per config entry from booking-local data
(`custom_components/rental_control/codegen.py`). Three generators exist:
`date_based` (the default), `static_random`, and `last_four`. There is no
shared state between entries, so uniqueness across entries is accidental at
best, and for `date_based` it is impossible: that generator derives the code
solely from reservation start/end dates, so any two entries holding
reservations with identical dates produce an identical code one hundred
percent of the time.

Three merged fixes narrowed but did not close the gap: #732 (a `randrange`
step misuse that collapsed the usable space to a quarter), #733 (global RNG
mutation), and #738 / PR #739 (generation unified into `codegen.py`).

A second, related defect is display parity. The calendar sensor
(`sensors/calsensor.py`, `_generate_door_code` at line 130) independently
recomputes a code from a single event's attributes rather than reading the
code the coordinator assigned. Any scheme that the sensor path cannot
reproduce makes the sensor and the lock disagree.

Keymaster already implements the shared-singleton-plus-persisted-store pattern
this feature adopts: `_async_get_or_create_coordinator`
(`custom_components/keymaster/__init__.py`, ~line 106) creates one coordinator
on `hass.data[DOMAIN][COORDINATOR]`, reuses it for later entries, pops it on
setup failure, and keeps per-entry state in a separate `LOCK_COORDINATORS`
map; its coordinator persists state through
`homeassistant.helpers.storage.Store` with `STORAGE_KEY = f"{DOMAIN}.locks"`
(`coordinator.py`, ~lines 89 and 257) and saves through `_async_save_data`
(~line 571).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - No Two Guests Share a Door Code (Priority: P1)

As a property manager running several Rental Control config entries against one
shared parent lock, I want the integration to guarantee that no two concurrent
reservations anywhere on my Home Assistant system are issued the same door
code, so a guest's code can never unlock another guest's unit or authenticate
them as another guest in the downstream captive portal.

**Why this priority**: This is the security defect in #743. Every other part of
this feature exists to make this guarantee hold.

**Independent Test**: Configure two entries whose reservations have identical
start and end dates under the default `date_based` generator, run a
reconciliation cycle for each, and confirm the two reservations hold different
codes and that both codes are recorded in the shared registry.

**Acceptance Scenarios**:

1. **Given** two config entries hold concurrent reservations that both derive
   the same preferred code, **When** both entries allocate codes, **Then** the
   first claimant keeps the preferred code and the second receives a different,
   deterministically derived code.
2. **Given** a code is already recorded in the registry for another
   reservation, **When** any entry requests a code, **Then** that code is never
   returned to the requesting reservation.
3. **Given** a reservation already holds an allocation, **When** the same
   reservation requests a code again in a later refresh, **Then** the same code
   is returned and the registry is unchanged.
4. **Given** every candidate code in the configured code space is already
   allocated, **When** an entry requests a code, **Then** no code is issued, the
   condition is reported to the operator, and no duplicate is created.

---

### User Story 2 - The Sensor and the Lock Agree (Priority: P1)

As a property manager, I want the calendar sensor's `slot_code` attribute to
show exactly the code that was programmed into the lock slot, so automations,
guest messaging, and the captive portal all reference one value.

**Why this priority**: Codes that are allocated centrally but recomputed
locally by the sensor would diverge the moment collision resolution changed a
code. Parity is what makes the allocator observable and trustworthy, and it
closes the defect that also sank the rejected partitioning design.

**Independent Test**: Force a collision so an entry's reservation receives a
resolved code that differs from its generator's preferred code, then read the
sensor's `slot_code` attribute and confirm it matches the allocated code rather
than the recomputed preferred one.

**Acceptance Scenarios**:

1. **Given** a lock-backed reservation whose allocated code differs from its
   generator's preferred code, **When** the sensor updates after physical
   confirmation, **Then** `slot_code` shows the confirmed allocated code; before
   confirmation it retains the last observed code or reports no code if none is
   safe.
2. **Given** a reservation the coordinator has not yet allocated a code for,
   **When** the sensor updates, **Then** the sensor reports no code rather than
   generating one of its own.
3. **Given** a lock-backed allocation changes between refreshes, **When** the
   matching write is physically confirmed, **Then** the sensor reflects the new
   allocated value without recomputation; a lockless entry publishes the
   allocated value immediately because it has no physical confirmation step.

---

### User Story 3 - Existing Guests' Codes Do Not Change (Priority: P1)

As a property manager with guests already in residence and codes already
printed, texted, and programmed into locks, I want upgrading to this feature to
leave every in-flight code exactly as it is.

**Why this priority**: Rotating a live guest's code locks that guest out. An
upgrade that rotates codes is not deployable, so migration behaviour is part of
the minimum viable feature rather than a follow-up.

**Independent Test**: Populate locks with codes for active reservations, start
with an empty registry, run one reconciliation cycle, and confirm no code
changed and that each observed code now appears in the registry owned by the
reservation that holds it.

**Acceptance Scenarios**:

1. **Given** an empty registry and active reservations whose codes are readable
   from the lock, **When** reconciliation runs, **Then** each observed code is
   adopted into the registry for its reservation and no code is rotated.
2. **Given** two active reservations were already sharing one code before the
   upgrade, **When** adoption runs, **Then** both are recorded, the conflict is
   reported to the operator, and neither code is silently changed.
3. **Given** the registry is missing or unreadable at startup, **When** the
   integration loads, **Then** it starts from an empty registry, warns, and
   rebuilds itself by adoption without rotating readable codes.

---

### User Story 4 - Codes Survive Restarts and Reloads (Priority: P2)

As a property manager, I want a reservation's door code to be identical before
and after a Home Assistant restart, a config entry reload, or a reinstall of the
integration, so nothing I have already communicated to a guest goes stale.

**Why this priority**: Determinism was previously a property of pure
re-derivation. Once collision resolution can move a code off its preferred
value, persistence is what keeps the code stable. Stability matters, but a
restart that rotated a code would be caught by the same adoption path that
protects migration, so this ranks below the correctness stories.

**Independent Test**: Allocate codes including at least one collision-resolved
code, restart Home Assistant, and confirm every reservation holds the same code
it held before the restart.

**Acceptance Scenarios**:

1. **Given** allocations exist, **When** Home Assistant restarts, **Then** every
   still-active reservation holds the same code as before.
2. **Given** allocations exist, **When** one config entry is reloaded while
   others remain loaded, **Then** the reloaded entry's reservations keep their
   codes and the other entries are unaffected.
3. **Given** a reservation's allocation was released, **When** the same
   reservation later reappears and its preferred code is unclaimed, **Then** it
   receives its preferred code again.

---

### Edge Cases

- **Code space exhaustion**: with a four-digit code space and a large number of
  concurrent reservations, allocation can legitimately fail. The system must
  refuse to issue a code rather than issue a duplicate, and must tell the
  operator, since raising code length (#741) is the operator's remedy.
- **Concurrent allocation**: two entries refreshing at the same moment must not
  both claim the same code. Allocation is serialized.
- **Entry without a managed lock**: sensor-only entries still publish codes that
  the downstream captive portal consumes, so they participate in allocation.
- **Setup ordering**: whichever entry loads first creates the allocator; later
  entries must find and reuse it, never create a second one.
- **Allocator creation failure**: a partially created allocator must not be left
  on `hass.data[DOMAIN]` for the next entry to adopt.
- **Unload of one entry among many**: the allocator and the registry survive;
  only the unloaded entry's per-entry state is removed. Its allocations are not
  released by unload alone, so a reload does not rotate codes.
- **Reservation that vanishes from the feed**: a cancelled or deleted booking
  must eventually release its code, or the code space leaks.
- **Reservation still physically programmed on a lock**: its code must not be
  handed to another reservation while it remains on the lock.
- **Two separate Home Assistant systems driving one parent lock**: out of scope;
  the registry is per Home Assistant system and cannot see the other.

## Requirements *(mandatory)*

### Functional Requirements

#### Shared allocator lifecycle

- **FR-001**: The system MUST maintain exactly one door code allocator per Home
  Assistant system, created by whichever config entry sets up first and reused
  by every config entry that sets up afterwards.
- **FR-002**: If allocator creation fails, the system MUST remove the partially
  created allocator so a later config entry setup attempts a clean creation, and
  MUST report the config entry as not ready.
- **FR-003**: Unloading one config entry while other entries remain loaded MUST
  leave the allocator, the registry, and the other entries' allocations intact,
  removing only the unloaded entry's per-entry state.
- **FR-004**: When a config entry is removed (as opposed to unloaded), the
  system MUST release allocations owned by that entry only after no managed
  lock still reports the code programmed; otherwise it MUST retain and report
  the orphaned allocation until allocator-level verification or an explicit
  operator recovery path confirms the code has been cleared.

#### Uniqueness

- **FR-005**: The system MUST NOT issue a code that the registry records as
  allocated to a different reservation. This is the feature's core invariant
  and MUST hold for every generator.
- **FR-006**: Allocation MUST be serialized so that concurrent requests from
  different config entries cannot both claim the same code.
- **FR-007**: Allocation MUST be keyed on a reservation's stable identity and
  MUST be idempotent: repeating a request for an already-allocated reservation
  returns the existing code and does not modify the registry. These continuity
  guarantees apply while that stable identity remains unchanged.
- **FR-008**: When no unallocated candidate remains in the configured code
  space, the system MUST decline to issue a code, MUST report the exhaustion to
  the operator, and MUST NOT issue a duplicate.

#### Code selection

- **FR-009**: For each reservation the system MUST first attempt the preferred
  code produced by the config entry's configured generator — `date_based`,
  `static_random`, or `last_four` — using the existing generation behaviour in
  `codegen.py`, including its fallback to `date_based` when a generator cannot
  produce a code.
- **FR-010**: When the preferred code is unallocated, or is already allocated to
  the requesting reservation itself, the system MUST allocate the preferred
  code, so codes remain meaningful in the common non-colliding case.
- **FR-011**: When the preferred code is allocated to a different reservation,
  the system MUST select a replacement from a candidate sequence that is
  deterministically derived from the requesting reservation's stable identity,
  so the same reservation facing the same registry always reaches the same
  result. The sequence MUST be non-repeating across all valid codes in the
  configured code space before exhaustion is reported.
- **FR-012**: Every issued code MUST conform to the config entry's configured
  code length. This feature MUST NOT change the configured or default code
  length.

#### Release

- **FR-013**: The system MUST release a reservation's allocation once the owning
  config entry no longer manages a lock slot for that reservation — that is,
  after the booking has ended or been cancelled and its slot has been cleared —
  so the code space does not leak. For config entries with no managed lock
  slot, the system MUST use the same booking-end, cancellation, and
  disappearance-grace rules that protect against transient feed omissions before
  releasing the allocation.
- **FR-014**: The system MUST NOT release an allocation for a code that is still
  programmed on a managed lock.
- **FR-015**: A released code MUST become available for future allocation, and a
  reservation whose allocation was released MUST be able to receive its
  preferred code again if that code is unallocated at the time of the request.

#### Persistence and determinism

- **FR-016**: The registry MUST be persisted through Home Assistant storage, in
  the same manner as Keymaster's `Store` usage, so allocations survive Home
  Assistant restarts.
- **FR-017**: A reservation's allocated code MUST remain identical across Home
  Assistant restarts, config entry reloads, and integration package
  reinstalls that preserve config entry identity, for as long as the
  reservation is active.
- **FR-018**: If the persisted registry is absent, unreadable, or fails
  validation, the system MUST start from an empty registry, MUST warn the
  operator, and MUST rebuild lock-backed allocations by adoption (FR-020).
  Any relevant lock slot that cannot be read and adopted, and any lockless
  active reservation without an observed or durable code source, MUST fail
  closed by publishing no code rather than assigning a replacement.

#### Display parity

- **FR-019**: The calendar sensor MUST NOT generate a code of its own. For
  lock-backed reservations, it MUST display the allocator's code only after the
  matching physical lock write has been confirmed; until then it MUST retain the
  last observed code, or report no code if no safe observed value exists. For
  lockless reservations, which have no physical confirmation step, it MUST
  display the allocator's persisted value immediately. When no allocation exists
  for the displayed reservation, the sensor MUST report no code rather than
  generating one.

#### Migration and adoption

- **FR-020**: Codes already observed on managed locks, including the codes
  retained today by `_resolve_observed_code` in
  `coordinator_helpers/reservations.py`, MUST be adopted into the registry as
  allocations owned by the observing reservation, so upgrading does not rotate
  any in-flight guest code. The system MUST complete adoption for all currently
  loaded entries before issuing new allocations, so a later-observed programmed
  code cannot be missed by an earlier allocation request.
- **FR-021**: An adopted code MUST take precedence over the generator's
  preferred code for that reservation; adoption MUST NOT trigger reallocation.
- **FR-022**: When adoption encounters the same code on two different
  reservations — the pre-existing duplicate condition from #743 — the system
  MUST record both, MUST report the conflict to the operator, and MUST NOT
  silently rotate either code. A code with one or more observed owners MUST
  remain unavailable to new allocations until the conflict is cleared.
  Correcting an existing duplicate is #735's force-re-issue operation, not
  this feature's.

#### Coverage and configuration

- **FR-023**: Config entries with no managed lock MUST participate in
  allocation on the same terms as lock-backed entries, because their sensor
  codes are consumed by downstream door-code authentication.
- **FR-024**: This feature MUST NOT introduce any new operator-supplied
  configuration, and specifically MUST NOT require any lock capacity value or
  any setting that must be kept consistent across config entries.
- **FR-025**: The system MUST log allocation, collision resolution, release,
  adoption, adoption conflicts, and exhaustion with enough detail for an
  operator to determine which reservation holds which masked or hashed code
  identifier and why. Logs and diagnostics MUST NOT include raw door codes.

### Key Entities

- **Door Code Allocator**: the single per-Home-Assistant-system authority that
  issues and releases codes. Owns the registry and serializes allocation. Held
  on shared integration data and shared by all config entries.
- **Allocation Registry**: the persisted mapping from an issued code to the
  reservation or observed conflict set holding it. Survives restarts. Its
  contents are the sole basis for collision detection.
- **Allocation**: one issued code bound to one reservation, or one observed
  conflict membership for a code temporarily held by multiple reservations.
  Carries the issued code, the owning config entry, the reservation's stable
  identity, and whether the code was generator-preferred, collision-resolved,
  adopted from a lock, or part of an adoption conflict.
- **Reservation Identity**: the stable fingerprint already used by the
  reconciliation planner to identify a booking across feed refreshes. It is the
  allocation key and the seed for deterministic collision resolution.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Across any number of config entries on one Home Assistant system,
  zero concurrent reservations are ever issued the same door code — this holds
  for all three generators, including two entries whose reservations have
  identical dates under the default `date_based` generator.
- **SC-002**: One hundred percent of the configured code space is available to
  every config entry; no entry is restricted to a fraction of it.
- **SC-003**: For every reservation, the code shown by the calendar sensor is
  never independently generated. For lock-backed reservations it matches the
  allocator's code only after that code is confirmed on the physical lock slot,
  including collision-resolved codes; before confirmation it retains the last
  observed code or no code. For lockless reservations it matches the allocator's
  persisted value immediately.
- **SC-004**: Upgrading an installation with active reservations rotates zero
  in-flight guest codes.
- **SC-005**: Every active reservation with an available registry, durable
  source, or observed lock code holds an identical code before and after a Home
  Assistant restart, a single entry reload, and an integration reinstall.
- **SC-006**: Every code issued to a reservation whose booking has ended and
  whose release guard has passed becomes available for reuse, so steady-state
  registry size tracks active allocations plus explicitly retained orphan and
  conflict records rather than growing without bound.
- **SC-007**: Operators need to supply zero additional configuration values to
  obtain the uniqueness guarantee.
- **SC-008**: Code space exhaustion and pre-existing duplicate codes are both
  surfaced to the operator rather than resolved by issuing a duplicate or by
  silent rotation.

## Assumptions

- One Home Assistant system is the uniqueness boundary. Two Home Assistant
  systems driving the same parent lock remain able to collide; that is outside
  this feature.
- The reconciliation planner's existing stable reservation identity is
  sufficient as an allocation key and as a deterministic seed; no new identity
  scheme is needed.
- The existing observed-code retention in `_resolve_observed_code` remains the
  safety net when the registry is lost, and is the mechanism through which the
  registry is rebuilt.
- The default four-digit code space is sufficient for realistic concurrent-slot
  counts once the whole space is usable and released codes are recycled.
- Adoption of codes read back from locks is trustworthy enough to seed the
  registry; the integration already relies on those reads today.

## Out of Scope

- **Code length (#741)**: `DEFAULT_CODE_LENGTH` and the configured code length
  are unchanged. Making the full code space usable is what makes four digits
  comfortable; raising the length remains an independent, optional change.
- **Force re-issue of a door code (#735)**: the registry makes
  release-and-reallocate a natural operation, but exposing it is separate work.
  Consequently the duplicate currently live on the affected property will not
  self-heal: both codes are on the lock, both are retained by adoption, and
  #735 is the remedy.
- **Per-instance code space partitioning**: specified in PR #742 and rejected.
  It required an operator-maintained lock capacity value that had to match
  across every entry on a lock, where a mismatch silently re-admitted
  collisions undetectably; it gave each entry only a fraction of the space; it
  could not express uniqueness for `date_based` or `last_four`; and the sensor
  path could not reproduce it, so the sensor and the lock would disagree. It
  MUST NOT be reintroduced.
- **Issue #734**: superseded by this feature and closed.

## Relationship to Other Work

- **Absorbs #736**: `slot_code` is documented as never persisted to the Home
  Assistant Store (`reconciliation/plan_models.py`, line 31). A persisted
  allocation registry addresses that directly.
- **Supersedes #734**, which is being closed.
- **Enables #735**, which becomes a release-and-reallocate operation against the
  registry.
- **Builds on** #732, #733, and #738 / PR #739, which corrected and unified
  generation but could not provide cross-entry uniqueness on their own.
