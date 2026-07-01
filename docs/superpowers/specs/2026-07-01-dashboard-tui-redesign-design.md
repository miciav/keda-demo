# Dashboard TUI Redesign

Date: 2026-07-01
Status: Approved design
Scope: Compact redesign of `dashboard.py`

## Goal

Make the TUI usable during a live KEDA demo by giving clear visual priority to the queue state and the operator actions. The current dashboard spends too much space on parallel panels with equal visual weight, so the eye does not know where to land first.

The redesign keeps the current runtime model, keyboard shortcuts, data sources, and Rich-only implementation. This is a layout and hierarchy change, not a rewrite.

## Chosen Direction

Chosen layout: `Operations first` (option A)
Density: `Compact`

Why this direction:

- Queue depth and queue actions are the main task in the demo and must dominate the layout.
- Worker state is important, but secondary to the queue and scaling story.
- Diagnostics must remain visible without consuming a top-level panel.
- The shortest safe implementation is to reuse the existing render pipeline and replace the panel composition.

## Layout

The redesigned dashboard has four visual bands.

### 1. Header

A single compact line with no dedicated status panel.

Content:

- connection state
- `queue=<depth>`
- `pods=<count>`
- `hpa=<current->desired>` when available
- `ns=<namespace>`
- `updated=<hh:mm:ss>`

Rules:

- Keep this to one line.
- Prefer terse labels over prose.
- If HPA data is missing, show a short fallback such as `hpa=na`.

### 2. Main row

Two panels only.

Left panel, wide: `Queue + Actions`

- queue bar
- numeric queue depth
- last scale event banner when present
- keyboard shortcuts: `1 +10`, `2 +100`, `3 drain`, `q quit`
- low-priority metadata in dim style:
  - `key=<queue>`
  - `redis=deploy/<name>`

Right panel, narrow: `Workers`

- compact pod table
- columns: `Pod`, `Phase`, `Ready`, `Age`
- no `Restarts` column in the default compact layout
- if rows exceed the configured limit, keep the existing hidden-row summary

### 3. Activity row

One full-width panel: `Activity`

This panel absorbs:

- normal operator actions
- scale events
- collection failures
- transient cluster diagnostics that used to appear in `Diagnostics`

Rules:

- reuse the rolling log model
- preserve colored entries
- append collection errors as red log lines
- keep fixed height

### 4. Footer

Delete the dedicated footer controls panel.

Reason:

- the shortcuts already live in `Queue + Actions`
- removing the footer makes the compact layout visibly tighter
- this deletes one more low-value band from the screen

## Component Changes

The redesign should stay inside `dashboard.py` and touch as few functions as possible.

Expected changes:

- replace `_build_status_panel()` with a compact header-only summary
- merge queue information and controls into a stronger `Queue + Actions` panel
- simplify `_build_pod_panel()` by dropping the `Restarts` column from the default view
- remove `_build_error_panel()` from the rendered layout
- route errors to the activity panel and header state instead of a dedicated panel
- simplify `render()` from a three-panel top row to a two-panel main row

Expected non-changes:

- `collect_snapshot()`
- background worker model
- keyboard reader model
- kubectl command wrappers
- queue mutation commands

## Error Handling

The redesign must not hide failures.

Behavior:

- connection or collection failures still affect the top status indicator
- the latest failures appear in `Activity` in red
- `Activity` renders normal log entries first and then appends current collection errors from `state["errors"]` in red
- repeated failures should continue to avoid noisy duplicate spam as much as the current logic already allows
- missing HPA data should degrade gracefully and never break rendering

## Testing

Add only the smallest checks that prove the layout changed in the intended way.

Minimum tests:

- `render()` output no longer includes separate `Status`, `Diagnostics`, or footer controls panels
- activity rendering includes error lines from `state["errors"]`
- existing layout-independent tests continue to pass

No new framework or screenshot testing is needed.

## Out of Scope

Do not add:

- alternate views
- modal or drilldown behavior
- new keyboard shortcuts
- richer pod diagnostics
- changes to polling, threading, or kubectl execution flow
- new dependencies

## Implementation Notes

- Reuse existing helper functions where possible.
- Prefer deleting panel builders over introducing new abstractions.
- Keep the dashboard bounded in height.
- Keep ASCII-safe rendering as the default.
