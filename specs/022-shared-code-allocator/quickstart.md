<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: Implementing the Shared Door Code Allocator

For the later IMPLEMENT stage. The PLAN stage is docs-only and changes no
production code.

## 0. Ground truth first

Read these before writing anything. Where they disagree with the plan, the code
wins and the plan gets corrected.

```bash
sed -n '190,260p' custom_components/rental_control/coordinator_helpers/coordinator_refresh_shell.py
sed -n '120,200p' custom_components/rental_control/coordinator_helpers/reservations.py
sed -n '28,55p'  custom_components/rental_control/reconciliation/actions.py
sed -n '246,285p' custom_components/rental_control/reconciliation/desired.py
sed -n '215,235p' custom_components/rental_control/sensors/calsensor.py
sed -n '14,36p'  custom_components/rental_control/sensors/calsensor_helpers/slots.py
```

Establish the behavior oracle before touching anything:

```bash
uv run pytest tests/ -q -p no:randomly
```

Known flake, re-run in isolation before treating it as a regression:
`tests/integration/test_refresh_cycle.py::test_missing_store_adopts_coded_slots_when_unavailable_at_setup`

## 1. Build the pure core

Create `allocator/models.py`, `allocator/registry.py`, and
`allocator/candidates.py` with no Home Assistant imports, plus their unit tests.

Verify the candidate sequence property directly, because everything else rests
on it:

```python
seen = set()
for i in range(10_000):
    seen.add(next_candidate(identity_key, 4, i))
assert len(seen) == 10_000
```

Same identity plus same length must produce the same ordering on every run and
after a restart.

## 2. Add persistence and the singleton

`allocator/store.py` and `allocator/singleton.py`. Add `ALLOCATOR`,
`STORE_CODE_REGISTRY_KEY = "rental_control.code_registry"`, and
`CODE_REGISTRY_SCHEMA_VERSION = 1` to `const.py`.

Codes are obfuscated at rest with Keymaster's salted-base64 scheme, salting
with the record's captured first-owner `entry_id`:

```python
encoded = base64.b64encode(salt.encode("utf-8") + code.encode("utf-8")).decode("utf-8")
code = base64.b64decode(encoded)[len(salt.encode("utf-8")):].decode("utf-8")
```

Keep this inside `store.py` only. The registry works in plain values; do not
leak encoded strings into comparison, candidate rejection, or any API. And do
not describe it as encryption in a docstring — it is obfuscation that keeps
codes out of plaintext backups, nothing more.

Mirror Keymaster's accessor shape, including the cleanup on failure. The
creation lock is a module-level `asyncio.Lock` in `singleton.py`, not a
`hass.data` entry — a lock kept in `hass.data` would itself need an unguarded
first write, which is the race it is meant to prevent:

```python
_CREATE_LOCK = asyncio.Lock()  # module level in allocator/singleton.py

async with _CREATE_LOCK:
    if ALLOCATOR in hass.data[DOMAIN]:
        return hass.data[DOMAIN][ALLOCATOR]
    try:
        allocator = DoorCodeAllocator(hass)
        await allocator.async_load()
    except Exception:
        hass.data[DOMAIN].pop(ALLOCATOR, None)
        raise
    hass.data[DOMAIN][ALLOCATOR] = allocator
return allocator
```

Wire it into `async_setup_entry` before `coordinator.async_load_slot_store()`,
and call `await allocator.async_register_entry(config_entry.entry_id)` there.
Every path that leaves setup before the first allocation pass — the
`ConfigEntryNotReady` raise, any later exception in `async_setup_entry`, and
`async_unload_entry` while the entry is still pending — must first call
`await allocator.async_unregister_entry(entry_id)`, or that entry holds the
adoption gate shut for everyone. The call is idempotent.

Checkpoint: a second entry must reuse the first allocator, an entry that fails
setup must not remain in `_pending_adoption`, unloading one entry of two must
leave the registry and the other entry's codes untouched, and a saved record
must carry no plaintext code field while its `encoded_code` round-trips.

## 2a. Add the orphan cleanup service

`allocator/services.py`, registered from `async_get_or_create_allocator` behind
`hass.services.has_service(DOMAIN, "clear_orphaned_codes")` so two entries do
not double-register. Declare it in `services.yaml`, `strings.json` under
`services`, and both `translations/en.json` and `translations/fr.json`, copying
the structure the existing `checkout` and `set_state` entries use.

The handler calls `allocator.async_clear_orphans(...)` and returns its report
with `SupportsResponse.OPTIONAL`. Reuse the sweep's FR-014 guard helper; do not
write a second copy of "is this code still on a lock". Keep the service to
clearing orphans — no conflict resolution, no re-issue, that is #735.

Checkpoint: with a removed entry whose code is still programmed, the service
clears nothing and reports `code_still_programmed`; once the slot is cleared, a
second call releases the record; a third call is a no-op.

## 3. Make `slot_code` optional and add the guards

Do this *before* the allocation step, so the fail-closed path is safe the moment
codes can go missing.

1. `Reservation.slot_code` becomes `str | None`; update its docstring, including
   the superseded #736 note, and change ghost reservations from `""` to `None`.
2. `classify_matched_desired_slot`: return `(ActionKind.NOOP,
   "code_unavailable")` when `desired_res.slot_code is None`, **before** the
   `code_drift` computation on line 35. This is the lockout guard; without it an
   absent code reads as drift and triggers `OVERWRITE_MANUAL_CHANGE`.
3. `assign_unmatched_reservations`: never assign a codeless reservation to a
   `FREE` slot; record `plan.overflow[key] = "code_unavailable"`.
4. `_classify_slot`: assert that a slot with a non-`None` `desired_identity_key`
   is never classified `stale`, `phantom`, `mis_assigned`, or
   `duplicate_non_canonical`.
5. `DesiredPlan.validate`: reject `SET`, `OVERWRITE_MANUAL_CHANGE`, and
   `UPDATE_TIMES` for a codeless reservation; make `async_apply_plan` skip such
   an action defensively.

Write the hazard-1 regression test now, and watch it fail before the guards and
pass after:

> Given an occupied slot holding a working code and a matched reservation with
> `slot_code is None`, one full cycle produces no `CLEAR`, `RESET`,
> `OVERWRITE_MANUAL_CHANGE`, or `SET` for that slot, and the physical code is
> unchanged at the end.

## 4. Add the refresh-cycle allocation step

`coordinator_helpers/code_allocation.py`, called from `_run_reconciliation`
between `_apply_checkin_protection` and `compute_desired_plan`. Keep the four
phases in order: adopt, rekey, allocate, sweep. Sort reservations by
`identity_key` before allocating so the result does not depend on feed order.

Then add the lockless branch in `_async_update_data` for
`event_overrides is None`: build reservations with `managed_slots=None`, run
phases 2 to 4, set `_latest_res_by_key`, compute no plan, call no services.

Checkpoint: two entries with identical `date_based` dates now get different
codes, and re-running the cycle changes nothing.

## 5. Switch the sensor to display-only

Delete `_generate_door_code` and the `slot_code is None` backfill in
`_handle_event_update`. Replace the `event_overrides_present` gate in
`slots.read_slot` with an unconditional coordinator lookup. Leave `last_four`
alone.

Update, do not delete, the sensor tests that assert regeneration; they become
parity assertions against `coordinator.get_slot_code`.

Checkpoint: force a collision so a reservation's allocated code differs from its
preferred code, and confirm the sensor shows the allocated one.

## 6. Validate

```bash
uv run ruff check custom_components/ tests/
uv run pytest tests/ -q -p no:randomly
pre-commit run --all-files
```

Coverage floor is 95%; new modules need tests in the same commit. Confirm
before opening the implementation PR:

- No log line, diagnostics field, or service response contains a code. Grep the
  new modules for `%s` formatting of `code` and confirm each uses `code_ref`.
- No persisted record carries a plaintext code. Assert it precisely in
  `test_allocator_store.py`: the record has no plain-code field, no field in the
  serialized record *equals* the code, and `decode_code(record["encoded_code"],
  record["encoding_salt_value"])` returns the expected code. Do not substring
  search the whole payload for the digits — timestamps, entry IDs, and identity
  keys can contain the same sequence, so that test both passes when it should
  fail and fails when it should pass.
- No file exceeds 400 lines, no function exceeds 80 lines, no signature exceeds
  six parameters, and no aislop suppression was added.
- The hazard-1 regression test exists and passes.
- A restart test shows identical codes before and after, including at least one
  collision-resolved code.

## Scope discipline

Out of scope here and not to be drifted into: code length (#741), force re-issue
(#735), per-instance partitioning (rejected in PR #742), and any new config flow
or options field. If an adjacent defect turns up, open an issue rather than
widening this one.
