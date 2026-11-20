<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Routing and Privacy Inventory

Inventory against base `72787134bc2a3d46339d82faf10151cfd0b5d50b`;
line numbers are navigation aids, not a substitute for re-reading callers.
All paths below are under `custom_components/rental_control/`. An exact
source scan and call-graph review must be repeated at implementation time.
Do **not** make a zero-`rg` result a gate: a human-readable diagnostic,
legacy selector or RC's own entity name may legitimately contain a lockname
or familiar ID pattern. For every routing/read/write/evidence site below,
tests must prove verified entry, slot and property provenance.

| Site | Current behavior to replace or audit | Required coverage |
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
