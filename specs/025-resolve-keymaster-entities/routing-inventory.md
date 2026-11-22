<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Routing and Privacy Inventory

Inventory against base `208578ba541f03b6187daf06e643768087b51381`;
line numbers are navigation aids, not a substitute for re-reading callers.
All paths below are under `custom_components/rental_control/`. The source and
call-graph re-scan was repeated during implementation; the table below records
the baseline hazards and the implementation tests that exercise their
replacement.
Do **not** make a zero-`rg` result a gate: a human-readable diagnostic,
legacy selector or RC's own entity name may legitimately contain a lockname
or familiar ID pattern. For every routing/read/write/evidence site below,
tests must prove verified entry, slot and property provenance.

The T003 re-scan at #960 searched entity-ID construction, direct state reads,
service dispatch, state tracking, and Keymaster event subscriptions across
the current source tree. It confirmed the inventory below and specifically
re-checked `state_handlers.py`, `startup_readability.py`,
`coordinator_helpers/coordinator_config_shell.py`,
`keymaster_services.py`, `keymaster_service_helpers.py`,
`event_overrides_helpers/apply_set.py`, `apply_update.py`, `shell_compat.py`,
`sensors/checkin/runtime.py`, `coordinator.py`, and `listeners.py`.
Generic service calls in `helpers.py` are calendar/integration utilities,
not additional Keymaster slot-routing paths. The implementation re-scan must
include any newly added call sites.

| Site | Baseline hazard to replace or audit | Required coverage |
|---|---|---|
| `__init__.py:333-360`, `async_start_listener` | Constructs slot enabled, PIN, name, datetime and reset IDs from lockname; config updates tear down/recreate | Real registry renames, current-address rebinding, one callback, unload and repeated reload |
| `state_handlers.py:71-145`, `handle_state_change`, `_resolve_context`, `_handle_reset_entity` | Parses slot with regex from event ID; `_reset` substring selects reset path | Identity→slot/property mapping, no regex/substring as authorization, stale/reused ID and unrelated reset tests |
| `state_handlers.py:148-227`, suppression and snapshot helpers | Reads constructed enabled/name/PIN/date switch/start/end IDs | Verified feedback snapshot and suppression; manual changes for each property |
| `startup_readability.py:37-52,86-246`, watcher | Constructs managed name/PIN/enabled IDs and subscribes | Registry registration, actual restored placeholder, live late state, unload/rebind |
| `coordinator_helpers/coordinator_setup_shell.py:199-274`, `_register_keymaster_device`, `_find_parent_entry_id`, `_discover_child_locks`, `_read_slot_snapshot` | Guessed reset ID drives version warning; parent first slug match and children by names; guessed snapshot IDs | Loaded version separately, collisions/disabled candidates, verified topology, snapshot identity, no false reset warning |
| `coordinator.py:145,301` | Monitored names and constructed PIN target | Identity-aware observation; preserve names only as display/legacy selector |
| `coordinator_helpers/coordinator_config_shell.py:74,142-181`, `_update_config_impl`, `_async_update_buffer_times` | Options buffer recomputation constructs datetime IDs, suppresses feedback and calls `datetime.set_value` | Verified start/end writes and confirmations; preserve date-only code and suppression on rename |
| `keymaster_services.py:80-120,139-204,275-302` | Set/reset, cleanup, post-reset text evidence and date updates | Preflight and revalidate each target and confirmation; no whole-lock fallback |
| `keymaster_service_helpers.py:207-294`, `disable_slot`, `enable_date_range`, `write_slot_payload`, `enable_slot`, `force_clear_name` | Constructs switch/text/datetime IDs; name cleanup accepts target ID | Per-step verified target, input provenance, in-flight rename and partial effects |
| `event_overrides_helpers/apply_set.py:30-34`, `apply_update.py:14-15`, `shell_compat.py:90-138` | Override set/update and direct name/PIN reads; in-memory clear fence release | Confirm override, reissue and pending-clear paths use original verified slot; never release by another slot |
| `listeners.py:47-84,89-180,232-290` | Global KM event subscription routes by normalized `lockname`, state and range | Match physical `entity_id` to a unique verified source, conflict/topology/inheritance checks and listener cleanup |
| `sensors/checkin/runtime.py:132-165,215-237`, `sensors/checkinsensor.py:259-295,421-428` | Check-in routing and constructed date-range end lookup | Parent/child and lockless transitions, verified check-in/normal/early checkout date reads |
| `coordinator_helpers/reissue.py:109`, `allocator/issuance.py:44`, `allocator/adoption.py:244`, `allocator/reissue_service.py:394` | Lockname-based internal slot keys, owner/adoption equivalence and service target resolution | Compatibility-preserving ledger migration, shared locks, collisions, unambiguous selectors and dry runs; not a blind global string replacement |
| `coordinator_helpers/exclusion_targets.py:35-108`, `allocator/exclusion_service.py:99-175`, `coordinator_helpers/reissue_targets.py:25-51,71-96,127-144` | `set_slot_exclusion` resolves RC sensor registry ownership or the unique attribute fallback; lockname narrows by managed range and, on removal, persisted exclusion ownership with existing fallbacks. Only exclusion creation runs its eligibility guard. | Preserve those unambiguous selector/refusal, out-of-range removal and dry-run behaviors. Resolve the RC entry first, then verify its KM association and same-slot evidence for exclusion creation; aliases or sensor fallback never authorize a hardware target. Removal remains a config cleanup, not a clear or an authorization. |

## Implementation audit

The following legacy-shaped addresses remain only as compatibility helpers.
When the production protected registry is registered, a missing verified
lifecycle now makes these paths inert: set/clear/update services reject the
constructed address; allocator reconciliation refuses unverified writes;
feedback listeners and startup readability do not subscribe to guessed IDs;
state feedback ignores events without a current lifecycle; buffer-date and
early-checkout writes require verified routing; and observed-PIN publication
reads the current lifecycle capability or returns no observation. A lockname
or sensor selector remains useful for display and resolving the RC entry, but
does not authorize a Keymaster target.

Verification is distributed across real-consumer tests, not inferred from
string scans:

- Lifecycle rebinding, rename/reused-address rejection and listener teardown:
  `test_verified_lifecycle_feedback.py`,
  `test_verified_lifecycle_recovery.py`, and
  `test_verified_slot_lifecycle.py`.
- Property snapshots, manual feedback and startup readability:
  `test_state_handlers.py`, `test_startup_readability.py`, and
  `test_verified_lifecycle_feedback.py`.
- Parent/child topology, exact event-source attribution, and check-in date
  writes: `test_verified_checkin.py` and `test_checkin_tracking.py`.
- Set, clear, reset, override, date, cleanup, and missing-identity dispatch:
  `test_verified_consumer_dispatch.py`,
  `test_verified_clear_recovery.py`,
  `test_verified_date_updates.py`, and
  `test_verified_service_helpers.py`.
- Public selector compatibility, reissue, and exclusion safety:
  `test_verified_public_services.py`,
  `test_verified_exclusion_safety.py`,
  `test_exclusion_targets.py`, and `test_allocator_reissue_cycle.py`.
- Sensitive output and notification redaction:
  `test_verified_identity_privacy.py` and
  `test_registry_protective_unknown.py`.

The no-lifecycle path remains available only to isolated legacy compatibility
contexts without a registered protected store; production activation tests
exercise the protected path. This is not a claim of native-Windows validation
or abrupt-process-crash testing. POSIX process-lock unavailability remains a
fail-closed protected-storage state, while lockless calendar tracking is
covered separately by `test_lockless_end_to_end.py`.

`allocator/`, `reconciliation/`, `coordinator_helpers/`, `event_overrides.py`
and helpers also carry observations, owner IDs, pending work and cross-entry
release guards. Trace the full caller graph, not merely the constructors in
this table. Existing RC sensor IDs and user-facing lockname selectors are
not KM routing proof, but they must remain compatible.

## Sensitive output inventory

- `state_handlers.py:107-108` logs an untrusted complete state-change event;
  `:174,204` logs raw State objects; `:352-363` logs name/PIN values.
- `keymaster_services.py:83-85` logs event data and `:190-204` logs lingering
  name/PIN state. Both normal and debug levels require review.
- `keymaster_service_helpers.py:294`, `startup_readability.py:176`, and
  `coordinator_helpers/coordinator_refresh_shell.py:300,355` include exception
  logging/tracebacks potentially echoing service values. Replace unsafe text
  with allowlisted reason codes and context without PIN-bearing exception
  strings. Review calendar and diagnostics paths too, including
  `coordinator_helpers/calendar_parsing.py:169-187,250,397` and `calendar.py`.

Regression tests must inject a synthetic PIN in State values, unexpected event
fields, exception messages and service failures; capture normal/debug logs,
notifications and diagnostics. Do not copy upstream raw event data into
diagnostic records, and distinguish redacted from unavailable information.

## Storage, lifecycle, and writer-quiescence notes (T005)

Verified against the installed Home Assistant 2026.11.0 source and the live
Rental Control symbols at this base:

- `RegistryStore` creates one HA `Store` per shared allocator with the
  registry key/schema. Its loader treats decoded `{}` as absence, returns
  pending same-instance `_data` before disk I/O, adds a missing HA
  `minor_version` default of `1`, and sends malformed JSON through HA's
  quarantine path, which logs storage paths/error text and creates an issue.
  Unsupported envelope versions can raise. Rental Control's current wrapper
  then treats `None` as missing and invalid payloads as reset/re-adoption.
- Current authoritative writes call `Store.async_delay_save` with a one-second
  delay. The call only schedules/replaces pending `_data`; it is not an
  acknowledgement. HA serializes and writes later under the Store instance's
  `_write_lock`; serialization or write failures are logged and swallowed by
  the callback. A pending delayed callback or final-write listener may still
  run during shutdown. There is no public Store API that proves all old
  delayed writers are quiescent.
- The allocator's per-instance `asyncio.Lock` covers mutation of in-memory
  owners, adoption, reissue-cycle staging, and scheduling saves. Its
  singleton uses a separate module-level creation lock and awaits the initial
  store load before publication. The one allocator instance serializes normal
  in-process callers, but HA Store does not coordinate RC processes or other
  Store instances. Migration therefore must stop new old-writer producers,
  account for/drain an already-running Store write, and reconcile the live
  snapshot with disk before switching authority; delayed-save return cannot
  establish quiescence or durability.
- Override reservation/update methods use an `EventOverrides` lock for
  in-memory state. Most apply paths release it before awaiting Keymaster
  services; the legacy greedy `shell_cleanup.async_check_overrides` path
  currently holds it across awaited clear calls. The reissue service's
  per-coordinator pending-state lock covers preview and pending-state mutation;
  its preview can acquire the allocator lock. It releases the reissue lock
  before requesting a coordinator refresh. Preserve the existing
  reissue-lock-to-allocator order and do not introduce the inverse order.
  Likewise never add an allocator transaction while holding the override
  lock. Allocator cycles schedule persistence while holding the allocator
  lock, but do not perform Keymaster service calls there.
- Setup currently registers a config-entry update listener and explicit
  `async_unload_entry` teardown, retaining state-listener unsubscribe
  functions; it does not subscribe to HA's entity-registry
  create/update/remove event. HA 2026.11.0 exposes
  `EVENT_ENTITY_REGISTRY_UPDATED` and `ConfigEntry.async_on_unload` for the
  planned identity lifecycle manager. Registry subscription must be installed
  before taking the initial snapshot and removed on entry unload/rebind.
