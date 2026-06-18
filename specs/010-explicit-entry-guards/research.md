<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research: Explicit Entry Data Guards

**Feature Branch**: `010-explicit-entry-guards`
**Date**: 2026-06-18

## Research Task 1: Shared Helper vs Inline Guards

**Question**: Should the six issue-reported access paths use inline missing-data
guards at each call site, or a shared helper such as
`get_entry_data(hass, entry_id) -> dict[str, Any] | None`?

### Findings

The live source confirms that all six reported sites read the same runtime entry
storage shape: `hass.data[DOMAIN][entry_id]`. They differ only in the local
`HomeAssistant` reference (`hass`, `self._hass`, or `self.hass`) and the source
of the entry id (`config_entry.entry_id` or `self._config_entry.entry_id`).

1. `custom_components/rental_control/__init__.py:update_listener`, line ~309:
   the first lookup currently uses
   `hass.data.get(DOMAIN, {}).get(config_entry.entry_id)`. When entry data is
   present, it reads the coordinator, writes merged config-entry data, and calls
   `coordinator.update_config(new_data)`. When entry data is absent, the current
   code already returns early, but the missing integration domain is hidden by
   an anonymous `{}` default.

2. `custom_components/rental_control/__init__.py:update_listener`, line ~331:
   the second lookup repeats the same expression after `update_config()`. When
   entry data is present, it unsubscribes and clears listeners, then restarts
   listeners if a lock is configured. When data disappeared during the update,
   the current code returns early. The missing-domain path should be equally
   explicit.

3. `custom_components/rental_control/__init__.py:_handle_keymaster_event`, line
   ~484: the event listener uses
   `hass.data.get(DOMAIN, {}).get(config_entry.entry_id, {})`, then reads the
   check-in sensor and monitoring switch. Present data preserves event
   evaluation and forwarding. Missing domain or entry data should reject the
   event and return without reporting it as accepted or forwarded.

4. `custom_components/rental_control/sensors/checkinsensor.py:_is_keymaster_monitoring_enabled`,
   line ~464: the sensor uses
   `self._hass.data.get(DOMAIN, {}).get(self._config_entry.entry_id, {})`, then
   reads the monitoring switch. If the switch exists, its `is_on` state wins.
   If the switch is absent, the method intentionally falls back to
   `self.coordinator.lockname is not None` to avoid premature auto check-in
   during setup races. Missing domain or entry data should use that same
   fallback rather than returning false.

5. `custom_components/rental_control/sensors/checkinsensor.py:async_checkout`,
   line ~1200: checkout reads entry data to find the early-checkout-expiry
   switch. If the switch exists and is on, checkout shortens the lock-code end
   time before transitioning. If the switch is absent, checkout continues
   without early expiry. Missing domain or entry data should follow that same
   skip-and-continue behavior.

6. `custom_components/rental_control/switch.py:KeymasterMonitoringSwitch.async_added_to_hass`,
   line ~132: after restoring the switch state, the entity stores itself in
   entry data for later event forwarding and monitoring checks. With missing
   domain or entry data, the current code mutates a throwaway `{}` and silently
   loses the registration. The new behavior should return without mutating
   phantom state.

All six sites therefore share the same missing-domain and missing-entry lookup
semantics. Supporting component absence remains call-site-specific and should
stay local: a present entry may still lack `CHECKIN_SENSOR`,
`KEYMASTER_MONITORING_SWITCH`, or `EARLY_CHECKOUT_EXPIRY_SWITCH` during setup or
unload ordering.

### Decision

Introduce a shared helper in `custom_components/rental_control/util.py`:

```python
def get_entry_data(hass: HomeAssistant, entry_id: str) -> dict[str, Any] | None:
    """Return Rental Control entry data when domain and entry data exist."""
```

The helper should:

1. read `domain_data = hass.data.get(DOMAIN)`;
2. return `None` when the integration domain is absent;
3. read `entry_data = domain_data.get(entry_id)`;
4. return `None` when the entry is absent; and
5. return the existing entry dictionary without creating or mutating defaults.

### Rationale

A helper is the best fit because every reported path reads the same entry-scoped
runtime store and needs identical absent-domain and absent-entry semantics. It
keeps the implementation DRY, makes the intended contract searchable, and
prevents future reintroduction of `hass.data.get(DOMAIN, {})` defaults in these
paths. `util.py` is already imported by `__init__.py`, `switch.py`, and
`checkinsensor.py`, imports `HomeAssistant`, `Any`, and `DOMAIN`, and does not
need new dependencies or an import cycle for this helper.

The helper should only guard entry-data retrieval. It should not fetch specific
supporting components, because each call site has a different existing fallback:
return early for event forwarding, configured-lock fallback for monitoring,
skip early expiry for checkout, and skip registration for the switch if entry
state is absent.

### Alternatives considered

- **Inline domain and entry guards at all six sites**: This is straightforward
  and mirrors the issue's suggested after-code. It was rejected because it
  duplicates identical guard semantics in three modules, making future drift
  likely and obscuring the common contract.
- **A helper that accepts `ConfigEntry` instead of `entry_id`**: This would save
  a single attribute access in some sites but is less flexible for entity
  methods that already store the entry id and would unnecessarily couple the
  helper to config-entry objects.
- **Component-specific helpers such as `get_monitoring_switch(...)`**: Rejected
  as too broad for this quality refactor. Supporting component absence has
  distinct behavior at each call site and should remain explicit locally.
- **Leaving the existing chained defaults**: Rejected because it violates issue
  #571 and the spec requirements by hiding missing domain/entry data and, in
  the switch registration path, mutating throwaway state.

## Summary

The implementation stage should add the shared `get_entry_data()` helper and use
it at the six issue-reported access paths. Missing domain or missing entry data
returns `None`; each caller then preserves its established safe outcome for that
operation while loaded entries continue to use the same entry dictionary as
before.
