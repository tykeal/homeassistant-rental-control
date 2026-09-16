<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research: Shared Door Code Allocator with Persisted Registry

Phase 0 decisions. Each entry records what was chosen, why, and what was
rejected. Source facts were read from `main` at `185ddd9`, not from prior
documents.

## Decision: A dedicated `allocator/` package, not another coordinator mixin

**Rationale**: The allocator outlives every individual coordinator and is shared
by all config entries, so it cannot be a per-entry mixin. The coordinator is
already composed of six shell mixins; a seventh would tie system-wide state to
per-entry lifetime. A package also keeps the collision logic pure and testable
without a Home Assistant fixture: `registry.py`, `candidates.py`, and the plain
models do not import Home Assistant, while store, singleton, services, and
notifications stay at the package boundary.

**Alternatives considered**:

- A `CoordinatorAllocatorMixin`: rejected, wrong lifetime and it would make
  "which coordinator owns the registry" ambiguous across ten entries.
- A module of free functions over a dict in `hass.data`: rejected, serialization
  of concurrent allocation needs an object to own the lock.

## Decision: Splice allocation between reservation building and plan computation

**Rationale**: `CoordinatorRefreshMixin._run_reconciliation` already builds
reservations with their generated and observed codes, then calls
`compute_desired_plan`. Everything the allocator needs — reservations, observed
slots with `actual_code`, and the entry's configuration — exists at exactly that
point, and nothing downstream has yet been written to a lock. It is the only
seam where adoption can precede allocation within one cycle.

**Alternatives considered**:

- Allocate inside `build_reservations`: rejected, that function is documented as
  pure and synchronous, and allocation is async and shared state.
- Allocate after `compute_desired_plan`: rejected, the plan's drift and action
  classification already depend on `slot_code`.
- Allocate lazily in the sensor: rejected outright, this is the divergence
  defect the feature exists to remove.

## Decision: Reuse the reconciliation fingerprint as the allocation key

**Rationale**: The spec assumes it, `make_reservation_fingerprint(entry_id,
slot_name, start, end)` is already stable across refreshes, already entry-scoped,
and already the Store's primary key. It gives idempotency for free (FR-007) and
is a good deterministic seed because it is a hash input with high entropy.

**Alternatives considered**:

- iCal UID: rejected, `plan_models.py` documents UIDs as volatile aliases.
- A new allocator-specific identity: rejected by the spec's assumptions and it
  would need its own rematch logic.

## Decision: Follow rematch by re-keying allocations

**Rationale**: The fingerprint changes when a booking's dates change, which
would otherwise orphan the old allocation and issue a fresh code to a guest
mid-stay. The allocation step must hydrate `Reservation.fingerprint_history`
from the persisted mappings before rekeying so it has the same conservative
rematch input as the planner. Re-keying the allocation record preserves SC-005
across date edits.

**Alternatives considered**:

- Let the old allocation expire and issue a new code: rejected, it rotates a live
  guest's code, the outcome US-3 forbids.
- Key allocations on slot number: rejected, slot assignment is itself an output
  of the plan and changes.

## Decision: Affine walk over the full code space for collision resolution

**Rationale**: FR-011 wants determinism from the reservation's identity plus
non-repetition across the whole space before exhaustion. With `n = 10 **
code_length` and a step coprime to `n`, `(start + i * step) % n` is a full-cycle
permutation: every code appears exactly once, the order depends only on the
seed, and no state is carried between calls. `n = 10_000` for the default
length, so a full walk is trivial even in the pathological case. Leading zeros
are already produced by `generate_date_based_code`'s `zfill`, so the whole space
is legitimate and nothing needs excluding.

**Alternatives considered**:

- `random.Random(identity_key)` sampling: rejected, retry-on-collision has no
  exhaustion guarantee and repeats candidates.
- Linear scan upward from the preferred code: rejected, it clusters allocations
  and makes two reservations with adjacent preferred codes contend repeatedly.
- Hash-then-modulo without stepping: rejected, no non-repeating traversal.

## Decision: One shared store, `rental_control.code_registry`, version 1

**Rationale**: The uniqueness boundary is the Home Assistant system, so the
registry must not be per entry. A single store also means one load at allocator
creation and one delayed save per cycle regardless of entry count. Version 1 is
a fresh namespace; there is no legacy payload to migrate because no prior
release persisted codes.

**Alternatives considered**:

- Extend the per-entry `rental_control.slot_mappings.<entry_id>` store: rejected,
  it is explicitly cache-only, per entry, and documented as never holding raw
  PINs. Cross-entry uniqueness cannot be assembled from per-entry caches at
  startup without a load ordering guarantee that does not exist.
- A registry rebuilt from locks on every start with no persistence: rejected,
  lockless entries have nothing to read back, so FR-017 would fail for them.

## Decision: Persist codes recoverably, obfuscated at rest, masked in logs

**Rationale**: FR-017 and FR-019 together require the exact code to be
recoverable after a restart, for reservations that may have no lock to read it
back from, and adopted or collision-resolved codes cannot be re-derived. So the
value must be stored. It should not be stored as bare digits, though, because
`.storage` files end up in backups and get browsed. Keymaster already solved the
same problem for PINs in `serialization.py` with salted base64 (`encode_pin`
line 101, `decode_pin` line 108), so this feature reuses that scheme with the
first owner's `entry_id` as the salt, fixed for the record's lifetime.

This is obfuscation and nothing more. The salt is in the same file as the value,
so it is reversible by anyone who can read the file. It is worth doing because
casual disclosure through backups and filesystem browsing is the realistic
exposure, and it is worth stating plainly that it is not a security control, so
nobody later mistakes it for one.

Logs and diagnostics are a separate and stricter matter: they never carry a
code in any form, encoded or otherwise, only a salted `code_ref` (FR-025). The
two salts serve different purposes and neither replaces the other.

**Alternatives considered**:

- Bare digits in the store: rejected by the maintainer. Recoverability does not
  require plaintext, and plaintext door codes in `.storage` and backups are an
  avoidable disclosure.
- Store only a hash and re-derive the code: impossible for collision-resolved
  and adopted codes, which are not derivable.
- Real encryption with a key kept elsewhere: rejected. It would imply a security
  guarantee this integration cannot honour — Home Assistant has no secret store
  that survives a backup restore without the same file access — and it adds a
  key lifecycle nobody asked for. Better an honest obfuscation than a misleading
  cipher.

## Decision: Make `Reservation.slot_code` optional and guard the planner

**Rationale**: FR-018's fail-closed state has to be representable. The existing
`slot_code: str` cannot express "no code", and substituting a placeholder string
would be far worse: `classify_matched_desired_slot` compares the observed code
against it and would classify drift, producing `OVERWRITE_MANUAL_CHANGE`. An
explicit `None`, plus guards that treat `None` as "hold this slot", is the only
representation that fails safe.

**Alternatives considered**:

- An empty string sentinel: rejected, it is truthy-adjacent and would silently
  compare unequal to every observed code, which is the lockout path.
- A parallel `code_pending: bool` alongside a stale `slot_code`: rejected, two
  sources of truth and every consumer would have to remember to check both.

## Decision: A scoped adoption gate rather than a startup barrier

**Rationale**: FR-020 requires adoption for all loaded entries to finish before
new codes are issued, but a blanket barrier would leave every sensor without a
code until the slowest entry finished loading, which is precisely hazard 2. The
gate is scoped to *new issuance only*: adoption, rekeying, and registry lookups
are never delayed, so every reservation that already had a code keeps publishing
it immediately. The pending set is seeded from
`hass.config_entries.async_entries(DOMAIN)` so it is correct even though entries
set up concurrently, and entries leave it on a completed pass, on setup failure,
on disable, and on unload or removal while still pending — so a broken entry
drains out rather than holding the gate shut. A wall-clock deadline warns and
notifies about entries that never reported, but never opens the gate: new
issuance stays fail-closed, because opening it could issue a replacement code
for an unadopted lockless reservation, which is precisely what FR-018 forbids.

**Alternatives considered**:

- No gate: rejected, it reintroduces the exact race FR-020 names — a code
  physically present on a lock belonging to a not-yet-loaded entry could be
  issued to somebody else.
- Block all code publication until every entry has adopted: rejected, it
  maximises the downstream no-code window for no correctness gain.
- Gate on `EVENT_HOMEASSISTANT_STARTED`: rejected as the sole mechanism, it does
  not cover an entry added or reloaded later, though the deadline plays a similar
  role as a backstop.

## Decision: A narrow service for FR-004's operator recovery path

**Rationale**: Orphaned allocations — records whose owning config entry is gone
while their code may still sit on a lock — need an operator action, and nothing
else in the feature provides one. The maintainer settled the scope question: a
Home Assistant service is not "operator-supplied configuration" under FR-024,
because that requirement targets values that must be kept consistent across
config entries, which is the failure mode that sank the rejected partitioning
design. A manual action with no persisted setting does not create that hazard.

`rental_control.clear_orphaned_codes` reuses the same FR-014 guard helper as the
sweep rather than reimplementing it, reports cleared and retained records with
reasons, supports `dry_run`, and takes the allocator lock so it cannot race a
refresh.

**Alternatives considered**:

- Diagnostics-only reporting with no action: rejected, it leaves the operator
  able to see the leak but not fix it until #735 lands.
- Automatic cleanup on entry removal with no guard: rejected outright, it is the
  hazard-1 failure in another costume — a still-programmed code would be handed
  to somebody else.
- Folding it into #735's force re-issue: rejected, that couples an operational
  cleanup to a larger unshipped feature. Clearing orphans is narrow and must
  stay narrow.
- An entity service like `checkout` and `set_state`: rejected, this operates on
  system-wide registry state and has no entity target.

## Decision: Verify captive-portal instead of assuming

**Rationale**: The issue comment asked for verification and it was cheap. Read
at `captive-portal/addon/src/captive_portal/`:
`integrations/rental_control_service.py` skips an event only when `slot_name`,
`slot_code`, and `last_four` are all absent (line 270) and falls back
`configured attribute -> slot_code -> slot_name` (lines 320-339);
`persistence/rental_control_event_repository.py` upserts in place and keeps the
booking row; grants live in a separate table keyed on `booking_ref`. A missing
`slot_code` therefore degrades to a retryable "booking not found" for new
authorizations and does not revoke existing access. No captive-portal change is
required.

**Alternatives considered**:

- Changing captive-portal to retain a previous `slot_code`: rejected as
  unnecessary given the ordering guarantee, and it would mask a genuine
  fail-closed condition downstream.

## Open items

None. Both points that were open at first draft — FR-004's operator recovery
path and the at-rest representation of persisted codes — have been decided by
the maintainer and are recorded above.
