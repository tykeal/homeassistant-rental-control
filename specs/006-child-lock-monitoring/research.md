<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research: Child Lock Monitoring

**Feature**: 006-child-lock-monitoring
**Date**: 2025-07-18
**Status**: Complete

## R-001: Keymaster Parent/Child Discovery Mechanism

**Question**: How does keymaster expose the parent/child lock relationship, and what is the most reliable way to discover child locks at runtime?

**Decision**: Discover child locks by iterating `hass.config_entries.async_entries("keymaster")` and checking each entry's `data.get("parent_entry_id")` against the parent lock's `entry_id`.

**Rationale**: Keymaster stores parent/child relationships in config entry data. Each child entry has a `parent_entry_id` field pointing to the parent's `entry_id`. This is the same mechanism keymaster itself uses internally. Iterating config entries is an O(n) operation where n is the number of keymaster entries (typically <10), making it negligible in the coordinator refresh cycle.

**Alternatives Considered**:
- **Device registry relationships**: Keymaster devices don't expose parent/child via HA device registry hierarchies. Not viable.
- **Parent's `child_locks` data field**: Some keymaster versions store a `child_locks` list on the parent entry's data. However, this is a denormalized copy and may not be present in all versions. The child's `parent_entry_id` is the canonical source.
- **Listening for config entry changes**: Could use `hass.bus.async_listen("config_entry_updated")` for real-time discovery. Rejected because piggybacking on the existing coordinator refresh cycle is simpler, avoids extra event listeners, and the spec allows discovery latency up to one refresh cycle (FR-006).

## R-002: Event Bus Listener Lockname Matching Strategy

**Question**: How should the event bus listener match multiple locknames efficiently while remaining maintainable?

**Decision**: The coordinator exposes a `monitored_locknames` property returning a `frozenset[str]` containing the parent lockname plus all discovered child locknames. The event listener uses `in` membership testing against this set.

**Rationale**: Set membership testing is O(1). The set is rebuilt on each coordinator refresh (when child locks may have changed). Using `frozenset` makes it immutable and thread-safe for concurrent access from the event bus callback. The property is computed lazily and cached until the next refresh.

**Alternatives Considered**:
- **Multiple event listeners (one per lockname)**: Would require managing listener lifecycle as children are added/removed. More complex, more unsubscribe tracking, no performance benefit.
- **Regex matching on lockname**: Over-engineered for a small set of known strings. Harder to debug.
- **Closure capture of lockname list**: Mutable state in closure is error-prone. The coordinator property approach is cleaner.

## R-003: Lock Identity Propagation for FR-009

**Question**: How should the identity of the triggering lock be propagated through to the check-in event data?

**Decision**: Add a `lock_name` parameter to `async_handle_keymaster_unlock()`. The event bus listener extracts the `lockname` from the event data and passes it through. The check-in sensor includes `lock_name` in the `rental_control_checkin` event payload and stores it as the `_checkin_lock_name` attribute.

**Rationale**: This is the minimal change path. The lockname is already present in the keymaster event data. We simply thread it through the existing call chain. Adding it to the check-in event payload fulfills FR-009 without requiring schema changes to the state machine or stored data.

**Alternatives Considered**:
- **Separate attribute entity**: Over-engineered. A single field in the event payload and sensor attributes suffices.
- **Log-only approach**: Would not satisfy FR-009's requirement that property managers can determine the entrance from event data programmatically.

## R-004: Coordinator Refresh vs. Config Entry Listener for Dynamic Discovery

**Question**: Should child lock discovery happen on coordinator refresh or via a dedicated config entry change listener?

**Decision**: Piggyback on the coordinator's existing `_async_update_data()` refresh cycle. Call a new `_discover_child_locks()` method at the start of each refresh.

**Rationale**: The coordinator already runs on a configurable interval (default 2 minutes). This provides acceptable latency for child lock discovery (FR-005, FR-006 require no restart/reconfiguration, but don't mandate instant discovery). The approach:
- Avoids adding new event listeners
- Keeps discovery logic centralized in the coordinator
- Naturally handles the case where keymaster is reloaded (entries re-appear on next refresh)
- Is testable via the existing coordinator test infrastructure

**Alternatives Considered**:
- **Config entry change listener**: Would provide near-instant discovery but adds complexity (new listener lifecycle, edge cases around keymaster reload ordering). The 2-minute default latency is acceptable per the spec.
- **On-demand discovery in event listener**: Would run discovery on every event, wasteful when events are frequent and children rarely change.

## R-005: Backward Compatibility Verification

**Question**: How do we ensure zero behavioral changes for installations with no child locks (FR-007)?

**Decision**: The `monitored_locknames` property returns `frozenset({self.lockname})` when no children are found (or when lockname is None, returns empty frozenset). The event listener's `in` check is functionally equivalent to the current `==` check when the set contains exactly one element.

**Rationale**: The only behavioral change is the comparison operator (`in frozenset` vs `== str`). For a single-element frozenset, `x in frozenset({x})` is identical to `x == x`. No new code paths are activated when no children exist.

**Alternatives Considered**:
- **Feature flag**: Over-engineered. The frozenset approach is inherently backward compatible.
- **Conditional logic (if has_children: use set, else: use string)**: Adds branching without benefit. The set approach handles both cases uniformly.

## R-006: Duplicate Check-in Prevention for Simultaneous Events

**Question**: What happens if parent and child locks fire unlock events simultaneously for the same code slot?

**Decision**: Rely on the existing state machine. The first event transitions the sensor from `awaiting_checkin` to `checked_in`. The second event arrives at a sensor already in `checked_in` state and is ignored by the existing guard at line 1386 of checkinsensor.py (`if self._state == CHECKIN_STATE_CHECKED_IN: return`).

**Rationale**: The HA event loop is single-threaded (asyncio). Events are processed sequentially. There is no race condition. The first valid event wins, and subsequent events for the same reservation are no-ops. No additional deduplication logic is needed.

**Alternatives Considered**:
- **Debounce/dedup timer**: Unnecessary given single-threaded event processing. Would add complexity and latency.
- **Event ID tracking**: Over-engineered. The state machine already prevents duplicate transitions.

## R-007: Monitoring Switch Behavior with Child Locks

**Question**: Does the existing monitoring switch need any changes to control child lock monitoring?

**Decision**: No changes needed. The monitoring switch check happens in the event bus listener (line 379 of `__init__.py`: `if not monitoring_switch.is_on: return`). This check runs *before* lockname matching, so it already gates all events regardless of whether they come from parent or child locks.

**Rationale**: The monitoring switch is a gate on the event processing pipeline. Since both parent and child lock events flow through the same pipeline, the switch naturally controls all of them. FR-004 is satisfied without any switch modifications.

**Alternatives Considered**:
- **Per-lock monitoring switches**: Contradicts FR-004 which requires a single switch for all locks. Rejected.
