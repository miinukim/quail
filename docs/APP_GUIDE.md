# QuAIL Django App Guide

This guide maps the QuAIL codebase by Django app, with the current runtime flow and key files.

## Project-Level (`quail` package)

- Role: project configuration, URL routing, ASGI/WSGI, Celery app bootstrap.
- Key files:
  - `quail/settings.py`: app registration, DB, Channels, Celery, logging, local data paths.
  - `quail/urls.py`: REST router + auth + TTL endpoints.
  - `quail/asgi.py`: HTTP + WebSocket protocol router.
  - `quail/celery.py`: Celery app and task autodiscovery.
- Notes:
  - `settings.py` creates filesystem dirs (`user_scripts`, `user_fpga`, `logs`, `data`) at startup.
  - Logging includes `quail.inspect` and `quail.engine` streams.

## `core` app

- Role: authentication and TTL control state/API.
- Key files:
  - `core/auth_views.py`
  - `core/ttl_views.py`
  - `core/ttl_state.py`
- Main features:
  - Token login/logout (`/api/login/`, `/api/logout/`).
  - TTL list/set/override REST API using in-memory state:
    `/api/devices/ttl/devices/`, `/api/devices/ttl/level/`, `/api/devices/ttl/override/`.
- Important behavior:
  - TTL state is process-memory only (`TTL_STATE`), not persisted to DB.

## `expmanager` app

- Role: canonical experiment/run/dataset/archive domain and primary REST APIs.
- Key files:
  - `expmanager/models.py`
  - `expmanager/views.py`
  - `expmanager/serializers.py`
  - `expmanager/tasks.py`
- Main models:
  - `ExperimentScript`: script registry (path-based metadata).
  - `ExperimentRun`: run lifecycle + schedule/priority + args.
  - `Dataset`: run-produced HDF5 artifacts.
  - `RunLog`: persistent run logs.
  - `Archive`: snapshots for post-run data access.
- Main endpoints:
  - `/api/runs/` list/create/retrieve/delete.
  - `/api/runs/{id}/abort/`.
  - `/api/runs/{id}/logs/`.
  - `/api/runs/{id}/datasets/`.
  - `/api/runs/{id}/datasets/{dataset_name}/meta/`.
  - `/api/runs/{id}/datasets/{dataset_name}/data/`.
  - `/api/runs/{id}/datasets/{dataset_name}/query/`.
  - `/api/runs/{id}/datasets/{dataset_name}/download/`.
  - `/api/files/?type=script|fpga&path=...` and `/api/files/read/?type=script|fpga&path=...`.
  - `/api/archives/` CRUD plus archive dataset data/query/download endpoints.
- Worker flow:
  - `run_experiment_task`:
    1. marks run `RUNNING`
    2. optional simulate mode via `QUAIL_SIMULATE_RUNS`
    3. executes `ExperimentEngine`
    4. sets final status (`COMPLETED`, `FAILED`, `CANCELLED`, `ABORTED`)
- Important behavior:
  - Runs can be created with `script_path` only (no `ExperimentScript` FK).
  - Error/traceback is pushed to `RunLog` on task failure.

## `orchestrator` app

- Role: script parsing/execution engine and instrumentserver communication.
- Key files:
  - `orchestrator/engine.py`
  - `orchestrator/processor.py`
  - `orchestrator/parser.py`
  - `orchestrator/instrument_client.py`
  - `orchestrator/inspector.py`
- Runtime flow:
  1. `ExperimentEngine` resolves script source.
  2. Performs active instrumentserver health check at init.
  3. Builds execution context (`sleep`, `instruments`, `save_data`, `init_data`, `check_abort`).
  4. `VirtualProcessor` parses AST and executes statement-by-statement.
  5. `InstrumentClient` sends `get_param_dict`, `set_params`, `call` over ZMQ REQ/REP.
  6. Data writes into HDF5 + `Dataset` rows.
- Data path:
  - `init_data` allocates HDF5 datasets `data` and `params`.
  - `save_data` appends measurement blocks and broadcasts dataset updates.
- Important behavior:
  - Health check fails fast when instrumentserver is unreachable.
  - `check_abort` stops on both `CANCELLED` and `ABORTED`.
  - `InstrumentClient.close()` is called in engine `finally`.
  - Processor currently enforces strict `save()` data shape expectations.

## `panels` app

- Role: reusable panel definitions/config snapshots and panel-driven run creation.
- Key files:
  - `panels/models.py`
  - `panels/views.py`
  - `panels/tasks.py`
  - `panels/serializers.py`
- Main models:
  - `Panel`: script-backed UI config with schema, defaults, schedule defaults.
  - `PanelConfig`: saved snapshot/version metadata.
- Main endpoints:
  - `/api/panels/` CRUD.
  - `/api/panels/inspect-classes/`.
  - `/api/panels/from-config/`.
  - `/api/panel-configs/` CRUD.
  - `/api/panel-configs/from-panel/`.
  - `/api/panel-configs/{config_id}/apply-panel/`.
- Scheduler flow:
  - `PanelViewSet.run()` creates immediate run (`NOW`) or enqueues `process_panel_run`.
  - `process_panel_run` supports `NOW`, `TIMED`, `RECURRING`.
  - MAQPIE currently creates panel-backed runs through `/api/runs/` with `panel_id`;
    the direct panel run endpoint remains available for compatibility.

## `datahandler` app

- Role: WebSocket consumers for run list/status/log/dataset and TTL status streams.
- Key files:
  - `datahandler/consumers.py`
  - `datahandler/routing.py`
- Main WS routes:
  - `/runs/stream/` run list snapshots with filters.
  - `/runs/{run_id}/status/` legacy per-run status stream.
  - `/runs/{run_id}/logs/` legacy per-run log stream.
  - `/runs/{run_id}/datasets/{dataset_id_or_name}/stream/` dataset snapshot/append stream.
  - `/devices/ttl/status/` TTL status stream.
- Important behavior:
  - Run list stream is real (DB-backed).
  - Dataset streams use the same logical row-table shape as the REST data endpoint.
  - The per-run status/log streams currently emit simulated compatibility data and
    are not part of the main MAQPIE path.

## End-to-End Execution Map

1. Frontend creates run with `/api/runs/` (`panel_id` may be supplied for panel-backed runs).
2. `expmanager` creates `ExperimentRun` as `QUEUED`.
3. Celery executes `run_experiment_task`.
4. `orchestrator` engine runs script and talks to instrumentserver.
5. `expmanager` stores datasets/logs/status transitions.
6. Frontend receives list updates via `/runs/stream/` and fetches datasets/logs over REST.

## Operational Checklist Before Hardware Tests

1. Confirm `instrumentserver` is reachable at configured host/port.
2. Confirm script instrument names match station instrument names.
3. Confirm Celery worker and Django point to the same DB/env.
4. Confirm abort behavior by testing `/api/runs/{id}/abort/` during a running loop.
5. Tail logs:
  - `quail/logs/engine.log`
  - `quail/logs/important.log`
  - `.logs/celery.log`
  - `.logs/instrumentserver.log`
