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

This feature makes cross-instance collisions structurally impossible for
the `static_random` generator by partitioning the code space into disjoint
per-instance blocks derived from configuration each instance already has,
with no live coordination between instances, and resolves the remaining
within-instance collisions with a deterministic probe.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - No Two Units Share a Door Code (Priority: P1)

As the operator of a multi-unit property whose units are separate Rental
Control instances writing into one parent lock, I want every generated
door code to be unique across all of my units, so that the parent lock
programs correctly and so that code-based guest authentication can never
resolve to the wrong guest.

**Why this priority**: This is the reported production defect and the
security exposure. Everything else in this feature exists to support it.

**Independent Test**: Configure several instances with adjacent,
non-overlapping slot ranges and the `static_random` generator, generate
codes for a full set of bookings in each, and verify that the union of all
generated codes contains no duplicates.

**Acceptance Scenarios**:

1. **Given** two instances sharing a parent lock with non-overlapping slot
   ranges and the `static_random` generator, **When** each generates codes
   for its bookings, **Then** no code produced by one instance equals any
   code produced by the other, for any combination of reservation UIDs.
2. **Given** an instance configured with `static_random`, **When** it
   generates a code, **Then** the code falls inside the block of the code
   space derived from that instance's own slot range and no other
   instance's block includes that value.
3. **Given** instances that cannot communicate with one another, **When**
   codes are generated, **Then** uniqueness is achieved without any
   instance querying another instance, a shared registry, or the parent
   lock.

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

**Independent Test**: Generate codes for a fixed set of bookings, discard
all in-memory state, regenerate from the same inputs, and verify the
mapping from booking to code is byte-identical.

**Acceptance Scenarios**:

1. **Given** an unchanged set of bookings and unchanged instance
   configuration, **When** codes are generated repeatedly across restarts,
   **Then** each booking receives the identical code every time.
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

1. **Given** two bookings in one instance whose candidate codes are equal,
   **When** codes are generated, **Then** the later booking in the fixed
   resolution order receives the next free value in its instance's block
   and the two codes differ.
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

1. **Given** a deployment left entirely at defaults, **When** codes are
   generated, **Then** partitioning works with no capacity configuration
   on any instance.
2. **Given** an operator who raises the capacity override on the instances
   of a large building, **When** codes are generated, **Then** blocks are
   sized against the raised capacity and remain disjoint between those
   instances.
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
   codes are generated for that instance, **Then** the codes are exactly
   those the previous whole-space generator produced for the same
   bookings.
3. **Given** the option is disabled on one instance only, **When** codes
   are generated, **Then** other instances continue to partition normally.

---

### Edge Cases

- **Block too small to hold the instance's bookings, or exhausted by
  probing**: the instance must still issue a code for every booking. When
  no free value remains in the block, the generator falls back to the
  unpartitioned whole-space value for that booking and records a clear
  warning naming the instance and advising a longer code length or a lower
  capacity. Issuing a possibly-duplicate code is preferred over issuing
  none, because a missing code locks a guest out immediately whereas a
  duplicate is rare and detectable.
- **Configured capacity larger than the usable code space** (for example a
  high override with a 4-digit code length, which would make each block
  smaller than one value): partitioning cannot be expressed. The
  integration warns and falls back to whole-space generation for that
  instance rather than producing degenerate blocks.
- **Slot range extending past the configured capacity**: blocks can no
  longer be guaranteed disjoint for that instance. The integration warns
  and recommends raising the capacity override.
- **Capacity override applied unevenly across instances**
  (misconfiguration, and the most likely way this feature silently
  fails): capacity is the divisor that defines block boundaries, so
  instances that disagree about it partition the same code space into
  different-sized blocks, and those blocks can overlap even though the
  slot ranges remain disjoint. Worked example in a 4-digit space of 9998
  usable values — instance A on slots 10–30 with capacity 250 derives
  roughly (399, 1199), while instance B on slots 40–60 with capacity 500
  derives roughly (799, 1199): about 400 code values are shared and
  cross-instance collisions return. This is a realistic path, not a
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
  resulting code is not partitioned.
- **Instance reconfigured** (slot range, maximum events, capacity, or code
  length changed): its block changes, so codes for bookings not yet
  written to the lock change. Codes already on the lock are retained by
  existing retention behaviour.

## Requirements *(mandatory)*

### Functional Requirements

#### Scope

- **FR-001**: The system MUST apply code-space partitioning only to the
  `static_random` generator. The `date_based` and `last_four` generators
  MUST be left byte-for-byte unchanged.
- **FR-002**: The system MUST implement partitioning exactly once, in the
  shared door-code generation module, so that coordinator-driven and
  sensor-driven generation produce identical codes for the same booking.
- **FR-003**: The system MUST NOT change the configured or default code
  length as part of this feature.

#### Block derivation

- **FR-004**: The system MUST derive each instance's block of the code
  space solely from that instance's own existing configuration — its
  starting slot, its maximum event count, its code length — measured
  against a parent-lock capacity value.
- **FR-005**: The system MUST NOT require any live coordination between
  instances: no instance may query another instance, a shared registry, a
  shared file, or the parent lock in order to generate a code.
- **FR-006**: Given instances with non-overlapping slot ranges, the same
  effective capacity, and the same code length, the system MUST produce
  blocks that are pairwise disjoint, such that no code from one instance
  can equal a code from another. Equal effective capacity is a
  precondition of disjointness, not an incidental detail: capacity is the
  divisor that defines block boundaries, so instances that disagree about
  it carve the code space differently and their blocks can overlap even
  when their slot ranges do not.
- **FR-007**: The system MUST treat the parent-lock capacity as a constant
  defaulting to 250, so that an ordinary deployment requires no new
  configuration on any instance.
- **FR-008**: The system MUST offer an advanced configuration option that
  overrides the capacity for deployments whose parent lock is addressed
  beyond the default — for example large buildings using name-based
  virtual slots.
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

#### Within-instance uniqueness

- **FR-012**: The system MUST ensure that two bookings within the same
  instance's plan never receive the same code, by deterministically
  probing for the next free value within the instance's own block when a
  candidate value is already taken.
- **FR-013**: The probe MUST visit candidate values in a fixed, defined
  order, and bookings MUST be resolved in a fixed order determined by
  reservation UID, so that the outcome does not depend on calendar order,
  fetch order, or processing order.
- **FR-014**: The probe MUST consider only codes within the instance's own
  plan; it MUST NOT read codes belonging to other instances.
- **FR-015**: When every value in the instance's block is taken, the
  system MUST still return a code for the booking by falling back to the
  unpartitioned whole-space value, and MUST record a warning identifying
  the instance and the exhaustion condition.
- **FR-016**: When the configured capacity leaves a block smaller than one
  value for the configured code length, the system MUST warn and fall back
  to unpartitioned generation for that instance rather than producing a
  degenerate block.

#### Determinism

- **FR-017**: Given identical bookings and identical instance
  configuration, the system MUST produce identical codes across Home
  Assistant restarts, reloads, and reinstalls.
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
- **FR-022**: The system MUST preserve existing code retention behaviour,
  so that a code already observed on the lock for an active booking is
  retained rather than rotated to the newly derived value.
- **FR-023**: The system MUST document the new capacity and opt-out
  options, the default capacity, and the recommendation to use a longer
  code length on parent locks shared by many units.
- **FR-023a**: The capacity option's help text in the configuration UI
  and the user documentation MUST both state that the override describes
  the shared parent lock and MUST be set to the same value on every
  instance sharing that lock, and MUST warn that applying it to only some
  instances can produce overlapping blocks and duplicate codes.
- **FR-024**: Changes MUST be covered by tests that assert cross-instance
  disjointness, within-instance uniqueness, restart determinism, opt-out
  equivalence with the previous behaviour, and the warning paths for
  exhaustion and undersized capacity.
- **FR-024a**: Tests MUST include a case demonstrating that instances
  with disjoint slot ranges but differing capacity values can produce
  overlapping blocks, so that the documented precondition is pinned by an
  executable example rather than only by prose.

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
  capacity larger than the code space, slot range beyond capacity) warns
  and degrades to the previous whole-space behaviour rather than raising
  or returning no code.
- **Ordering by reservation UID is stable.** The reservation UID is
  treated as immutable for the life of a booking, consistent with the
  existing `static_random` seeding.
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
  per-slot-equivalent space is roughly 39 values; at 5 digits roughly 399.
  A longer code length is therefore advisable on parent locks shared by
  many units, but changing the default code length is not part of this
  feature.

## Migration and Live Behaviour

- **Existing guests will not be disrupted.** Reconciliation reads the PIN
  back from the lock and retains the observed value when it differs from
  the freshly generated one. Codes already written for active bookings
  will therefore *not* rotate when this feature ships. The change takes
  effect for codes that have not yet been written.
- **The existing production collision will NOT self-heal.** Both colliding
  codes are already present on the lock, so retention preserves both.
  Clearing the live duplicate requires the force-re-issue capability
  tracked in issue #735; this feature deliberately does not attempt it.
- **Durability caveat.** The generated code is not persisted to Home
  Assistant's own store; the lock is the sole durable record. Determinism
  here is therefore a property of regeneration from booking identity and
  configuration, not of stored state. Persisting the code is tracked
  separately in issue #736 and is out of scope.

## Out of Scope

- The `date_based` and `last_four` generators, including their own
  uniqueness weaknesses. Deliberately excluded and deliberately not
  tracked separately.
- Changing the default code length from 4 to 5 digits — covered by a
  separate issue.
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

- **SC-001**: Across a simulated property of 10 instances sharing one
  parent lock with adjacent slot ranges and a uniform capacity, 100% of
  generated `static_random` codes are unique across the whole property,
  for every tested booking set. This holds both at the default capacity
  and at a raised capacity applied to every instance.
- **SC-002**: Within any single instance, 100% of concurrently planned
  bookings receive distinct codes, including sets contrived to force
  candidate collisions.
- **SC-003**: For a fixed set of bookings and configuration, 100% of
  generated codes are identical across repeated generation runs and
  simulated restarts.
- **SC-004**: With partitioning disabled, 100% of generated codes match
  the values produced by the previous release for the same bookings.
- **SC-005**: A deployment left entirely at defaults obtains cross-
  instance uniqueness with zero new configuration entered by the operator.
- **SC-006**: No booking is ever left without a code: every degraded
  condition (block exhausted, capacity larger than the code space, slot
  range beyond capacity) still yields a correctly formatted code and emits
  exactly one clear, instance-identifying warning.
- **SC-007**: Upgrading an existing installation rotates zero codes for
  bookings already written to the lock.
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
