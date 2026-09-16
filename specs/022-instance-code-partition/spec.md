<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Instance-Partitioned Static Random Door Codes

**Feature Branch**: `022-instance-code-partition`
**Created**: 2026-09-16
**Status**: Draft
**Input**: Issue #734 — "Door codes are not unique across Rental Control instances sharing a parent lock"

## Overview

A property may run many Rental Control instances against a single parent
lock, each instance owning a small, non-overlapping range of slots carved
out of that lock. Today the `static_random` generator derives a code from
booking-local data only (the reservation UID, falling back to the event
description). Every instance therefore draws from the *same* code space
with no awareness of its siblings, so two units can — and in production
did — issue the identical door code.

The consequence is not cosmetic. The parent lock holds every code, so the
duplicate broke programming on the parent while the child locks, which
sync only their own subset, never noticed. Downstream consumers treat the
door code as a credential and resolve a booking by code across *all*
integrations, so a duplicate can authorize the wrong guest's booking.

For valid, enabled partitioned inputs, this feature makes cross-instance
collisions structurally impossible for the `static_random` generator by
partitioning the code space into disjoint per-instance blocks derived from
configuration each instance already has, with no live coordination between
instances, and resolves the remaining within-instance collisions with a
deterministic probe for newly generated, non-degraded bookings. Degraded
and misconfigured paths that fall back to whole-space generation,
pre-existing duplicate retained PINs, plus retained out-of-block legacy
or manual codes that siblings cannot observe, are explicit exceptions
and forfeit that guarantee.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - No Two Units Share a Door Code (Priority: P1)

As the operator of a multi-unit property whose units are separate Rental
Control instances writing into one parent lock, I want every generated
door code to be unique across all of my units, so that the parent lock
programs correctly and so that code-based guest authentication can never
resolve to the wrong guest.

**Why this priority**: This is the reported production defect and the
security exposure. Everything else in this feature exists to support it.

**Independent Test**: Configure several partition-enabled instances with
adjacent, non-overlapping slot ranges, the same effective capacity, enough
block capacity for their planned bookings, and the `static_random`
generator, generate codes for a full set of bookings in each, and verify
that the union of all generated codes contains no duplicates.

**Acceptance Scenarios**:

1. **Given** two partition-enabled instances sharing a parent lock with
   non-overlapping slot ranges, the same effective capacity, enough block
   capacity for their planned bookings, and the `static_random` generator,
   **When** each generates codes for its bookings, **Then** no code
   produced by one instance equals any code produced by the other, for any
   combination of reservation UIDs.
2. **Given** a partition-enabled instance configured with `static_random`,
   valid non-degraded partition inputs, and siblings that share the same
   effective capacity and code length with non-overlapping ranges, **When**
   it generates a code for a booking with a usable seed, **Then** the code
   falls inside the block of the code space derived from that instance's
   own slot range and no sibling block includes that value.
3. **Given** partition-enabled instances with non-overlapping slot ranges,
   the same effective capacity and code length, usable seeds, no retained
   duplicate or out-of-block PINs, and enough block capacity, **When**
   codes are generated, **Then** uniqueness is achieved without any
   instance querying another instance, a shared registry, or parent-lock
   slots outside its own managed range.

---

### User Story 2 - Codes Survive a Restart Unchanged (Priority: P1)

As an operator who has already told guests their codes, I want the same
set of bookings to produce exactly the same codes after a Home Assistant
restart, reinstall, or configuration reload, so that guests are never
locked out by a restart and so that the codes on the lock remain
trustworthy.

**Why this priority**: Determinism is the defining property of
`static_random` and the reason the original design seeds from reservation
identity. A partitioning scheme that broke it would be a regression worse
than the defect it fixes.

**Independent Test**: Generate codes for a fixed set of bookings and
observed retained-code state, discard all in-memory state, regenerate from
the same inputs and observed state, and verify the mapping from booking to
code is byte-identical.

**Acceptance Scenarios**:

1. **Given** an unchanged set of bookings, unchanged instance
   configuration, and unchanged observed retained-code state, **When**
   codes are generated repeatedly across restarts, **Then** each booking
   receives the identical code every time.
2. **Given** a booking whose slot assignment changes during
   reconciliation, **When** its code is generated, **Then** the code is
   unchanged, because the code derives from reservation identity and the
   instance's configured block — never from the slot the booking currently
   occupies.
3. **Given** a booking whose calendar description changes but whose UID is
   stable, **When** its code is regenerated, **Then** the code is
   unchanged.

---

### User Story 3 - Two Bookings in One Unit Never Collide (Priority: P2)

As an operator of a single unit with several concurrent bookings, I want
two bookings in the *same* unit to never share a code, so that the unit's
own slots program correctly and code-based authentication is unambiguous
within the unit as well as across units.

**Why this priority**: Disjoint blocks only remove *cross*-instance
collisions. Within one instance the block is small — at the default
capacity and a 4-digit code length the per-slot-equivalent space is
roughly 39 values — so same-unit collisions are likely enough to need
explicit handling.

**Independent Test**: Construct a set of bookings whose seeds are
contrived to land on the same value within one instance's block, generate
the plan, and verify all issued codes are distinct and that the resolution
is identical on a second run.

**Acceptance Scenarios**:

1. **Given** two bookings in one non-degraded instance plan whose
   candidate codes are equal and whose block has a free value for the
   later booking, **When** codes are generated, **Then** the later booking
   in the fixed resolution order receives the next free value in its
   instance's block and the two codes differ.
2. **Given** the same colliding set of bookings, **When** generation is
   repeated on a later run or after a restart, **Then** exactly the same
   booking keeps the original candidate and exactly the same booking is
   moved, because the resolution order is fixed by reservation UID rather
   than by calendar order or processing order.
3. **Given** a booking whose candidate value is free, **When** codes are
   generated, **Then** it receives its candidate value unchanged.

---

### User Story 4 - Large Parent Locks and Virtual Slots (Priority: P2)

As the operator of a large building whose parent lock is addressed with
name-based "virtual slots" well beyond the usual physical slot count, I
want to raise the assumed parent-lock capacity, so that every one of my
units still receives a block and uniqueness still holds.

**Why this priority**: The default capacity covers ordinary deployments,
but large buildings exist today and would otherwise fall outside the
partitioning scheme. It must be possible to extend the scheme without
requiring every ordinary operator to configure anything.

**Independent Test**: Configure an instance whose slot range sits above
the default capacity, raise the capacity override on that instance *and
on every instance sharing the parent lock*, and verify the instance
receives a block disjoint from its siblings.

**Acceptance Scenarios**:

1. **Given** a deployment whose managed slot ranges end within the default
   capacity and whose partitioning and capacity options are left at
   defaults, **When** codes are generated, **Then** partitioning works
   with no capacity configuration on any instance.
2. **Given** an operator who raises the capacity override on the instances
   of a large building and the resulting per-instance blocks are
   non-empty with enough capacity for planned bookings, **When** codes are
   generated, **Then** blocks are sized against the raised capacity and
   remain disjoint between those instances.
3. **Given** an instance whose slot range extends beyond the configured
   capacity, **When** codes are generated, **Then** the integration warns
   that the capacity is too small for the configured slot range and tells
   the operator to raise the override.
4. **Given** an operator viewing the capacity option, **When** they read
   its help text or the documentation, **Then** both state that the value
   describes the shared parent lock and must be set identically on every
   instance using that lock, and warn that a partial or mismatched
   override can produce overlapping blocks and duplicate codes.
5. **Given** one instance raises the override while its siblings remain
   at the default, **When** codes are generated, **Then** blocks may
   overlap despite disjoint slot ranges and duplicates become possible —
   an accepted, documented, undetectable misconfiguration rather than a
   behaviour the integration can correct.

---

### User Story 5 - Opting Out (Priority: P3)

As an operator with an unusual deployment, or one debugging a code
problem, I want to turn the partitioning off and return to the previous
whole-space generation, so that I am never trapped by the new behaviour.

**Why this priority**: The feature is enabled by default because the
defect it fixes is a security issue; an escape hatch is still warranted
but is not needed for the fix to deliver value.

**Independent Test**: Disable the option on an instance and verify the
codes it produces match the values produced by the pre-feature generator
for the same bookings.

**Acceptance Scenarios**:

1. **Given** a fresh installation or an upgrade with no new configuration,
   **When** the `static_random` generator runs, **Then** partitioning is
   active (opt-out, not opt-in).
2. **Given** an operator who disables the option on an instance, **When**
   codes are generated for new or unretained bookings on that instance,
   **Then** the codes are exactly those the previous whole-space generator
   produced for the same bookings.
3. **Given** the option is disabled on one instance only, **When** codes
   are generated, **Then** that instance warns that opt-out forfeits the
   cross-instance uniqueness guarantee and other instances continue to
   partition normally.
4. **Given** an operator disables partitioning, **When** the confirmation
   text or documentation is shown, **Then** it states that whole-space
   generation can collide with sibling instances and should be used only
   when the operator accepts that risk.

---

### Edge Cases

- **Block too small to hold the instance's bookings, or exhausted by
  probing**: the instance must still issue a code for every booking. When
  no free value remains in the block, the generator falls back to the
  unpartitioned whole-space value for that booking and records a clear
  warning naming the instance and advising a longer code length or a lower
  capacity only when the lower value is the real shared parent-lock
  capacity and is applied uniformly to every sibling instance. The warning
  is recorded once for each affected booking, because each fallback
  credential can collide independently. Issuing a
  possibly-duplicate code is preferred over issuing none, because a
  missing code locks a guest out immediately. The fallback carries a
  residual collision and ambiguous-authentication risk until the operator
  increases usable capacity or uses the separate reissue/remediation flow.
- **Empty computed block**: when the configured capacity and the
  instance's slot range yield no code values for that instance's
  half-open interval, partitioning cannot be expressed for that instance.
  This can happen when the capacity is larger than the usable code space,
  but only the computed interval matters; a multi-slot instance can still
  receive a non-empty block. The integration warns and falls back to
  whole-space generation for that instance rather than producing
  degenerate blocks.
- **Slot range extending past the configured capacity**: blocks can no
  longer be guaranteed disjoint for that instance. The integration warns
  and recommends raising the capacity override.
- **Partitioning disabled for one instance**: that instance deliberately
  returns to whole-space generation and can collide with sibling
  instances. The integration warns or requires confirmation when the
  operator disables partitioning, and documentation states that opt-out
  forfeits the cross-instance uniqueness guarantee.
- **Capacity override applied unevenly across instances**
  (misconfiguration, and the most likely way this feature silently
  fails): capacity is the divisor that defines block boundaries, so
  instances that disagree about it partition the same code space into
  different-sized blocks, and those blocks can overlap even though the
  slot ranges remain disjoint. Worked example in a 4-digit space of 9998
  usable values — instance A on slots 10-30 with capacity 250 derives
  offsets `[359, 1199)` and code values `0360` through `1199`, while
  instance B on slots 40-60 with capacity 500 derives offsets
  `[779, 1199)` and code values `0780` through `1199`: 420 code values
  are shared and cross-instance collisions return. This is a realistic
  path, not a
  contrived one: an operator who hits the capacity ceiling will naturally
  raise the override on the one instance that warned rather than on all
  ten. The override MUST therefore be applied uniformly to every instance
  sharing a parent lock, and both the option help text and the
  documentation must say so.
- **Detection of a capacity mismatch is not possible by design**: an
  instance has no visibility into its siblings' configuration and
  FR-005 forbids acquiring any, so the integration cannot warn about a
  mismatch it cannot observe. Uniform capacity is an operator obligation
  enforced by documentation only. This is the same class of limitation as
  overlapping slot ranges below, and is accepted rather than solved,
  because solving it would require the live cross-instance coordination
  this feature deliberately avoids.
- **Overlapping or duplicated slot ranges across instances**
  (misconfiguration): two instances derive the same block, so uniqueness
  degrades to within-block probing per instance and cross-instance
  collisions become possible again. The feature does not detect or repair
  this; non-overlapping slot ranges are a precondition.
- **A booking is cancelled**: probe order may shift, so a *later* booking
  that has not yet been written to the lock may receive a different code
  than it would have. This is accepted; see Assumptions.
- **Generators other than `static_random`**: `date_based` and `last_four`
  are untouched and keep their present behaviour and their present
  uniqueness weaknesses.
- **No usable seed** (neither reservation UID nor description): the
  existing fallback to the date-based generator applies unchanged, and the
  resulting code is not partitioned. Cross-instance uniqueness is not
  guaranteed for that booking. The integration warns for that booking,
  without logging the generated code, so the operator can see that
  partitioning degraded.
- **Instance reconfigured** (slot range, maximum events, capacity, or code
  length changed): its block changes, so codes for bookings not yet
  written to the lock change. Codes already on the lock are retained by
  existing retention behaviour. A retained out-of-block code can overlap a
  sibling instance's current block because siblings cannot observe one
  another's retained PINs; this is an explicit retention/no-coordination
  exception to the cross-instance guarantee.

## Requirements *(mandatory)*

### Functional Requirements

#### Scope

- **FR-001**: The system MUST apply code-space partitioning only to the
  `static_random` generator. The `date_based` and `last_four` generators
  MUST be left byte-for-byte unchanged.
- **FR-002**: The system MUST expose one behaviourally identical
  partitioned allocation contract to every `static_random` generation
  path, so that coordinator-driven and sensor-driven `static_random`
  generation produce identical codes for the same booking set and observed
  retention state.
- **FR-003**: The system MUST NOT change the configured or default code
  length as part of this feature.

#### Block derivation

- **FR-004**: The system MUST derive each instance's block of the code
  space solely from that instance's own existing configuration — its
  starting slot, its maximum event count, its code length — measured
  against a parent-lock capacity value.
- **FR-004a**: The code-space domain used for partitioning MUST be the
  existing `static_random` candidate domain: integers `1` through
  `10^code_length - 2`, inclusive, rendered with zero padding to the
  configured length. The domain therefore contains
  `10^code_length - 2` usable values.
- **FR-004b**: Blocks MUST be derived with a canonical half-open formula.
  Let `D = 10^code_length - 2`, `C = effective_capacity`,
  `S = start_slot - 1`, and `E = S + max_events`. The instance owns
  offsets `[floor(S * D / C), floor(E * D / C))` within the domain, then
  maps each offset `o` to code value `o + 1`. For example, with
  `D = 9998` and `C = 250`, slots 1-10 own offsets `[0, 399)` and code
  values `0001` through `0399`, while slots 11-20 own offsets
  `[399, 799)` and code values `0400` through `0799`; adjacent slot
  ranges therefore never share an endpoint.
- **FR-004c**: If `start_slot < 1`, `max_events < 1`, or
  `start_slot + max_events - 1` exceeds the effective capacity, block
  derivation is invalid for that instance and MUST use the degraded path
  defined for out-of-range slots rather than clamping into a neighbouring
  block.
- **FR-005**: The system MUST NOT require any live coordination between
  instances: no instance may query another instance, a shared registry, a
  shared file, or parent-lock slots outside its own managed range in order
  to generate a code. Reading this instance's own managed slots for the
  existing retention behaviour is permitted and does not provide
  cross-instance visibility.
- **FR-006**: Given valid, in-range, non-degraded instances with
  non-overlapping slot ranges, the same effective capacity, the same code
  length, and sufficient block capacity, the system MUST produce blocks
  that are pairwise disjoint, such that no in-block generated code from
  one instance can equal an in-block generated code from another. Equal
  effective capacity is a precondition of disjointness, not an incidental
  detail: capacity is the divisor that defines block boundaries, so
  instances that disagree about it carve the code space differently and
  their blocks can overlap even when their slot ranges do not.
- **FR-007**: The system MUST treat the parent-lock capacity as a constant
  defaulting to 250, so that an ordinary deployment requires no new
  configuration on any instance.
- **FR-008**: The system MUST offer an advanced configuration option that
  overrides the capacity for deployments whose parent lock is addressed
  beyond the default — for example large buildings using name-based
  virtual slots.
- **FR-008a**: The capacity override MUST accept only positive integers.
  Zero, negative, non-integer, or otherwise malformed values MUST be
  rejected during configuration validation before block derivation can use
  them.
- **FR-009**: The system MUST NOT require the capacity override to be set
  on any instance for the default case to work: when no instance
  overrides it, every instance shares the default capacity and blocks are
  disjoint with zero configuration.
- **FR-009a**: Capacity is a property of the shared parent lock, not of
  an individual instance. When the override IS used, it MUST be set to
  the same value on every instance sharing that parent lock. A mismatched
  or partially applied override breaks disjointness and re-admits
  cross-instance collisions, which is precisely the failure this feature
  exists to prevent.
- **FR-009b**: The system MUST NOT attempt to detect a capacity mismatch
  by querying other instances, and MUST NOT silently assume agreement.
  Uniform capacity is an operator obligation surfaced through
  documentation and option help text (FR-023), because FR-005 forbids the
  cross-instance visibility that automatic detection would require.
- **FR-010**: The system MUST warn, without failing, when an instance's
  slot range extends beyond the configured capacity, and the warning MUST
  identify the instance and advise raising the override.
- **FR-011**: The system MUST derive each booking's candidate code from
  reservation identity (the reservation UID, with the existing description
  fallback) mapped into the instance's block, and MUST NOT derive it from
  the slot number the booking currently occupies, so that a reconciliation
  slot move never changes a guest's code.
- **FR-011a**: The candidate mapping MUST preserve the existing
  `static_random` seed normalization: use the reservation UID when it is
  present, otherwise use the description text, and fall back to the
  existing date-based generator when neither exists. For a non-empty block
  with size `B`, draw exactly one value from the existing static-random
  deterministic PRNG stream with range `[0, B)`, equivalent to replacing
  the legacy whole-space upper bound with the block size. When `B = 1`,
  the only offset is `0`. Add the drawn offset to the block start offset,
  then add `1` to map the domain offset to the rendered code value. The
  same normalized seed, block start, block size, and configured code
  length MUST always produce the same candidate value.
- **FR-011b**: When a booking has neither reservation UID nor description,
  the system MUST use the existing date-based fallback, MUST warn for that
  booking without logging the generated code, and MUST treat the result as
  a degraded, non-partitioned credential outside the cross-instance
  uniqueness guarantee.

#### Within-instance uniqueness

- **FR-012**: For a non-degraded instance plan with enough free block
  values and no pre-existing duplicate retained PINs, the system MUST
  ensure that newly generated bookings and non-duplicate retained bookings
  within the same plan never receive the same code, by deterministically
  probing for the next free value within the instance's own block when a
  candidate value is already taken.
- **FR-012a**: Coordinator-driven reconciliation and sensor-driven display
  MUST consume the same plan-level allocation contract. The allocation
  population is the current active, future, and otherwise slot-eligible
  bookings for the instance after the integration's normal calendar
  parsing, checkout, cancellation, and maximum-event filtering, plus every
  slot-eligible retained booking whose managed slot still has an observed
  PIN, including future retained bookings and retained ghost placeholders
  that temporarily represent missing feed events with an observed PIN.
  A locally observed PIN always takes precedence over exclusion: retained
  overflow bookings and cancelled or checked-out bookings whose old PIN
  remains readable in a managed slot stay in the occupied-code set until
  the integration confirms that the slot was cleared. Overflow bookings
  without an observed PIN, cancelled bookings with confirmed clears,
  checked-out bookings with confirmed clears, ghost placeholders without
  an observed PIN, and bookings outside the instance's managed slot range
  do not participate. When no current plan exists, such as the first
  refresh, both paths MUST build allocation from that same population
  before displaying or reserving codes, so a sensor cannot show an
  unprobed per-booking candidate that differs from the reconciled code.
- **FR-012b**: Codes retained from the lock for slot-eligible bookings
  MUST be inserted into the plan's occupied-code set before probing newly
  generated candidates. A retained code remains assigned to its booking
  even when it falls outside the new partition block, and new generated
  in-block bookings MUST avoid every retained code they can observe. If
  retained codes consume all available in-block values, later generated
  bookings use the block-exhaustion fallback and warning path; the
  fallback may still duplicate a retained PIN and must warn for that
  booking.
- **FR-012c**: The allocation identity used for plan membership MUST be
  UID- or source-occurrence-aware, not only slot, name, start, and end.
  Two source events with different UIDs or immutable occurrence keys MUST
  remain separate allocation participants even when they share a display
  name and time window. Retention rematching MUST prefer the same
  allocation identity when it exists; if an older retained PIN can only be
  associated with a managed slot or legacy fingerprint, it still occupies
  the plan for probing until the reconciler can match it to one allocation
  identity or the retention window expires.
- **FR-013**: The probe MUST visit candidate values in a fixed order:
  start at the booking's mapped candidate offset, then advance by one
  offset at a time within the block, wrapping to the block start after the
  block end, until a free value is found or the whole block has been
  visited.
- **FR-013a**: Bookings MUST be resolved in a total, stable order whose
  primary key is reservation UID. Missing UIDs sort after present UIDs.
  Equal UIDs are broken only by immutable booking occurrence attributes:
  booking start time, booking end time, source-calendar identifier, and an
  immutable source-occurrence key when the source provides one. For
  UID-less bookings, the description text is part of the ordering key
  because it is also the legacy seed. Mutable description text MUST NOT
  reorder bookings that already have a UID. If two source events remain
  indistinguishable after the applicable keys, the system MUST coalesce
  them into one allocation identity, return the same credential for the
  duplicate source records, and warn without logging the PIN; it MUST NOT
  silently rely on fetch order, entity iteration order, or processing
  order.
- **FR-014**: The probe MUST consider only codes within the instance's own
  plan; it MUST NOT read codes belonging to other instances.
- **FR-015**: When every value in the instance's block is taken, the
  system MUST still return a code for the booking by falling back to the
  legacy `static_random` whole-space value for that booking's normalized
  UID-or-description seed, and MUST record a warning identifying the
  instance and the exhaustion condition.
- **FR-015a**: Partition-degradation warnings, including block exhaustion,
  empty computed blocks, out-of-range slots, no usable seed, opt-out, and
  retained-code exceptions, MUST NOT include raw PIN values or other
  credential material.
- **FR-016**: When the configured capacity and slot range leave the
  instance with an empty computed block for the configured code length,
  the system MUST warn and fall back to unpartitioned generation for that
  instance rather than producing a degenerate block.

#### Determinism

- **FR-017**: Given identical bookings and identical instance
  configuration, and identical observed retained-code state, the system
  MUST produce identical codes across Home Assistant restarts, reloads,
  and reinstalls. The generated candidate values before retention remain
  deterministic from bookings and configuration alone.
- **FR-018**: Code generation MUST NOT mutate or depend on global random
  state, and MUST NOT depend on wall-clock time, entity ordering, or
  process-local memory that does not survive a restart.
- **FR-019**: Generated codes MUST continue to satisfy the existing format
  contract: numeric, of exactly the configured code length, zero-padded.

#### Rollout, control and migration

- **FR-020**: The system MUST enable partitioning by default for
  `static_random` on both new and upgraded installations.
- **FR-021**: The system MUST provide a configuration option that disables
  partitioning per instance, returning that instance to the previous
  whole-space generation behaviour exactly.
- **FR-021a**: Disabling partitioning MUST warn or require confirmation
  that the instance will use whole-space generation and can duplicate
  codes produced by sibling instances, forfeiting the cross-instance
  uniqueness guarantee for that instance.
- **FR-021b**: When partitioning is disabled, the instance MUST bypass the
  new plan-level partition allocation and probing logic for newly
  generated or unretained bookings, so opt-out returns exactly to legacy
  whole-space generation for those bookings. Readable observed PINs are
  still retained under FR-022.
- **FR-022**: The system MUST preserve existing code retention behaviour,
  so that a code already observed on the lock for a slot-eligible booking is
  retained rather than rotated to the newly derived value.
- **FR-022a**: Retained observed codes participate in the same plan-level
  uniqueness calculation as newly generated codes. Retention wins for the
  booking that already owns the observed PIN, and other in-block bookings
  probe rather than being assigned that same observed code. Pre-existing
  duplicate observed PINs are retained for their bookings as an explicit
  exception; new in-block allocations avoid the locally observed PIN, but
  block-exhaustion fallback can still duplicate and must warn. Locally
  detected duplicate retained PINs MUST warn as a uniqueness exception
  without logging the PIN values.
- **FR-022b**: During the first migration reconciliation after enabling
  partitioned generation, any readable observed PIN in this instance's
  managed slots for a slot-eligible booking MUST be retained for that
  booking before comparing against newly generated partitioned or legacy
  values. This includes readable pre-feature PINs that happen to equal the
  old whole-space generator output.
- **FR-022c**: On the migration from pre-partitioned generation to
  partitioned generation, FR-022b overrides any "update generated code"
  setting for readable observed PINs so existing guest credentials do not
  rotate solely because the generator changed. Outside that migration
  window, existing explicit reissue/update semantics continue to control
  intentional code changes.
- **FR-023**: The system MUST document the new capacity and opt-out
  options, the default capacity, and the recommendation to use the next
  supported longer code length, currently 6 digits, on parent locks shared
  by many units.
- **FR-023a**: The capacity option's help text in the configuration UI
  and the user documentation MUST both state that the override describes
  the shared parent lock and MUST be set to the same value on every
  instance sharing that lock, and MUST warn that applying it to only some
  instances can produce overlapping blocks and duplicate codes.
- **FR-023b**: The opt-out option's help text in the configuration UI and
  the user documentation MUST both state that disabling partitioning
  restores legacy whole-space generation, can duplicate codes produced by
  sibling instances, and removes the cross-instance uniqueness guarantee
  for the opted-out instance.
- **FR-024**: Changes MUST be covered by tests that assert cross-instance
  disjointness, within-instance uniqueness, restart determinism, opt-out
  equivalence with the previous behaviour, and the warning paths for
  exhaustion, empty blocks, out-of-range slots, opt-out, and unseeded
  date-based fallback.
- **FR-024a**: Tests MUST include a case demonstrating that instances
  with disjoint slot ranges but differing capacity values can produce
  overlapping blocks, so that the documented precondition is pinned by an
  executable example rather than only by prose.
- **FR-024b**: Tests MUST cover retained observed PINs as occupied values,
  including a retained PIN outside the new block and a pre-existing
  duplicate retained PIN, so the retention exceptions remain explicit.
- **FR-024c**: Tests MUST include a coordinator-versus-sensor parity case
  that exercises plan-level probing on first refresh and retained-code
  occupancy, not only the legacy single-booking helper path.
- **FR-024d**: Tests MUST cover warning behaviour for coalesced
  indistinguishable source events and locally duplicate retained PINs.

### Key Entities

- **Rental Control instance**: one configured unit/calendar. Owns a slot
  range on the parent lock defined by its starting slot and maximum event
  count, a code generator choice, a code length, and — new here — an
  optional parent-lock capacity override and an optional switch disabling
  partitioning.
- **Parent lock capacity**: the number of slot positions the shared parent
  lock is assumed to address. Defaults to 250. Determines how finely the
  code space is divided. It describes the lock, not the instance, so all
  instances sharing a lock must use the same value.
- **Code space**: the set of numeric values expressible at the configured
  code length, from which every door code is drawn.
- **Instance block**: the contiguous, disjoint portion of the code space
  assigned to one instance, derived from its slot range measured against
  the capacity.
- **Reservation**: a booking with a stable identity (UID) used as the
  generation seed, plus the description used as the legacy fallback seed.
- **Door code**: the numeric credential issued to a guest, written to the
  lock and consumed by downstream services as an authentication token.

## Assumptions

- **Slot ranges are already disjoint between instances.** Operators carve
  non-overlapping ranges out of the parent lock today; this is what makes
  coordination-free partitioning possible. Overlapping ranges are a
  misconfiguration and are out of scope for detection here.
- **Probe-order instability is accepted.** Cancelling a booking can change
  the code a *later*, not-yet-issued booking would receive. Codes already
  written to the lock are protected by existing retention, so only
  bookings that have never reached the lock are affected. The alternative
  — persisting an allocation map — is heavier than the problem warrants.
- **Never fail to issue a code.** Every degraded path (block exhausted,
  empty computed block, slot range beyond capacity, no usable seed, or
  explicit opt-out) warns and degrades to the previous whole-space or
  date-based behaviour rather than raising or returning no code.
- **Ordering by reservation identity is stable.** The reservation UID is
  treated as the primary immutable ordering key for the life of a booking,
  consistent with the existing `static_random` seeding, with deterministic
  tie-breakers for missing or repeated UIDs.
- **Capacity default of 250** reflects typical parent-lock addressing.
  Deployments beyond it are expected to use the override.
- **Capacity is uniform across a parent lock.** Blocks are disjoint only
  because every instance divides the code space by the same number, so
  the override — when used at all — must be applied identically to every
  instance sharing the lock. Instances cannot see one another's
  configuration (FR-005), so this cannot be verified at runtime and is
  guaranteed by documentation and operator discipline alone. It is the
  single most likely way this feature fails silently.
- **Space sizing at the default capacity**: at a 4-digit code length the
  per-slot-equivalent space is roughly 39 values; at a supported 6-digit
  code length it is roughly 3,999. A longer supported code length is
  therefore advisable on parent locks shared by many units, but changing
  the default code length or supported code-length set is not part of this
  feature.

## Migration and Live Behaviour

- **Existing guests will not be disrupted when the lock is readable.**
  Reconciliation reads the PIN back from the lock and retains the observed
  value when it differs from the freshly generated one. Codes already
  written for active bookings will therefore *not* rotate when this
  feature ships if Keymaster returns an observed PIN for the slot. The
  change takes effect for codes that have not yet been written.
- **The existing production collision will NOT self-heal.** Both colliding
  codes are already present on the lock, so retention preserves both.
  Clearing the live duplicate requires the force-re-issue capability
  tracked in issue #735; this feature deliberately does not attempt it.
- **Durability caveat.** The generated code is not persisted to Home
  Assistant's own store; the lock is the sole durable record. Determinism
  here is therefore a property of regeneration from booking identity and
  configuration, not of stored state. Persisting the code is tracked
  separately in issue #736 and is out of scope. If the lock is unreadable
  during an upgrade or refresh, there is no observed PIN to retain, so an
  active booking can receive the newly generated value.

## Out of Scope

- The `date_based` and `last_four` generators, including their own
  uniqueness weaknesses. Deliberately excluded and deliberately not
  tracked separately.
- Changing the default code length or supporting additional unsupported
  code lengths — covered by issue #741.
- Healing or re-issuing codes already written to the lock, including the
  live production duplicate — issue #735.
- Persisting the generated code to the Home Assistant store — issue #736.
- Per-slot (rather than per-instance) partitioning.
- Any live coordination mechanism, shared registry, or cross-instance
  discovery.
- Detecting or repairing overlapping slot ranges between instances.
- Detecting or repairing a capacity override applied unevenly across
  instances; FR-005 rules out the visibility this would require, so it is
  addressed by documentation instead.
- Changes to downstream consumers of door codes, including how the
  captive-portal service resolves a code across integrations.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Across a simulated property of 10 partition-enabled
  instances sharing one parent lock with adjacent slot ranges, a uniform
  capacity, no unseeded bookings, no out-of-block retained PINs, and
  no duplicate retained PINs, 100% of newly generated `static_random`
  codes are unique across the whole property, for every tested booking set
  with sufficient free block capacity after retained-code occupancy. This
  holds both at the default capacity and at a raised capacity applied to
  every instance.
- **SC-002**: Within any single instance whose block has enough free
  values for the planned newly generated bookings, whose bookings have
  usable seeds, and whose plan is non-degraded with no duplicate retained
  PINs, 100% of concurrently planned newly generated or non-duplicate
  retained bookings receive distinct codes, including sets contrived to
  force candidate collisions.
- **SC-003**: For a fixed set of bookings and configuration, 100% of
  generated candidate codes are identical across repeated generation runs
  and simulated restarts. Returned codes are identical when the observed
  retained-code state is also identical.
- **SC-004**: With partitioning disabled, 100% of generated codes for new
  or unretained bookings match the values produced by the previous
  release for the same bookings.
- **SC-005**: A deployment with pre-existing disjoint slot ranges, valid
  seeded `static_random` bookings, a common code length, managed ranges
  ending within the default capacity, sufficient block capacity, and the
  new partitioning and capacity options left at defaults obtains
  cross-instance uniqueness for newly generated codes with zero new
  partition configuration entered by the operator.
- **SC-006**: No eligible booking selected for the current plan is ever
  left without a code: every degraded
  condition (block exhausted, empty computed block, slot range beyond
  capacity, no usable seed, opted-out partitioning) still yields a
  correctly formatted code and emits the warning-or-confirmation behaviour
  specified for that condition. Empty-block and slot-range degraded paths
  emit clear, instance-identifying warnings per affected instance;
  block-exhaustion, duplicate-retained, coalesced-event, and unseeded
  fallbacks emit warnings for affected bookings or allocation identities;
  opt-out emits one warning or confirmation per configuration change.
  Runtime warnings are transition- or rate-limited so a persistent
  condition remains visible without flooding logs. All warnings redact raw
  PIN values.
- **SC-007**: Upgrading an existing installation rotates zero codes for
  active bookings whose existing lock PIN is readable during
  reconciliation.
- **SC-008**: Every generated code remains numeric, exactly the
  configured code length, and zero-padded — no change to the format
  contract relied on by downstream consumers.
- **SC-009**: The uniform-capacity requirement is discoverable without
  reading the source: it appears in the capacity option's help text and
  in the user documentation, each stating that the value must match on
  every instance sharing a parent lock and what goes wrong when it does
  not.
- **SC-010**: A test demonstrates the mismatched-capacity overlap
  explicitly, so any future change that alters block derivation must
  confront the precondition rather than silently invalidate it.
