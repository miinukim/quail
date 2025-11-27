# QuAIL System Architecture and Control Flow

## System Overview

QuAIL is a Django-based experiment management system for experiments with devices running on QCoDeS drivers. It orchestrates instrument control, experiment execution, data collection, and real-time visualization.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                         FRONTEND CLIENT                             │
│                    (Web Browser / UI)                               │
└──────────────────┬────────────────────────┬─────────────────────────┘
                   │ HTTP/REST              │ WebSocket
                   ↓                        ↓
┌─────────────────────────────────┐  ┌──────────────────────────────┐
│      Django REST Framework      │  │   Django Channels (ASGI)     │
│         (expmanager)            │  │      (visualizer)            │
└────────────┬────────────────────┘  └──────────────────────────────┘
             │                                    ↑
             │ Triggers Celery Task               │ Real-time Updates
             ↓                                    │
┌─────────────────────────────────────────────────┴────────────────────┐
│                         Celery Worker                                │
│                      (Background Task)                               │
│  ┌───────────────────────────────────────────────────────────────┐   │
│  │                    ExperimentEngine                           │   │
│  │  ┌────────────────────────────────────────────────────────┐   │   │
│  │  │             VirtualProcessor                           │   │   │
│  │  │  (Parses & Executes Custom Experiment Script)          │   │   │
│  │  └─────────────┬──────────────────────────────────────────┘   │   │
│  │                │                                              │   │
│  │                ↓                                              │   │
│  │  ┌──────────────────────────┐    ┌──────────────────────┐     │   │
│  │  │   InstrumentClient       │───→│  InstrumentServer    │     │   │
│  │  │   (ZMQ REQ Client)       │    │   (ZMQ REP Server)   │     │   │
│  │  └──────────────────────────┘    └──────────┬───────────┘     │   │
│  │                                              │                │   │
│  │                                              ↓                │   │
│  │                                   ┌────────────────────────┐  │   │
│  │                                   │  QCoDeS Instruments    │  │   │
│  │                                   │  (Keithley, DACs, etc) │  │   │
│  │                                   └────────────────────────┘  │   │
│  └──────────────────────────────────────────────────────────────-┘   │
└──────────────────────────────────────────────────────────────────────┘
                   │
                   ↓
┌─────────────────────────────────────────────────────────────────────┐
│                      Data Persistence Layer                         │
│  ┌──────────────────────┐  ┌──────────────────────────────────────┐ │
│  │  PostgreSQL/SQLite   │  │        HDF5 Files                    │ │
│  │  - ExperimentRun        │  │  - Experiment data (4, N, M)         │ │
│  │  - ExperimentScript  │  │  - Parameter values                  │ │
│  │  - RunLog            │  │  - Metadata                          │ │
│  └──────────────────────┘  └──────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

## Component Breakdown

### 1. Frontend Layer
- **Technology**: Web Browser (React/Vue/vanilla JS)
- **Responsibilities**:
  - User authentication
  - Script creation/editing
  - Experiment execution control (start/abort)
  - Real-time visualization of logs and data

### 2. Django Apps

#### a. `expmanager` (Experiment Manager)
- **Models**:
  - `ExperimentScript`: Stores experiment code and FPGA code
  - `ExperimentRun`: Tracks experiment execution (status, priority, data file)
  - `RunLog`: Stores execution logs
- **Views**:
  - `ExperimentScriptViewSet`: CRUD for scripts
  - `ExperimentRunViewSet`: 
    - `start` action: Queues experiment with priority
    - `abort` action: Cancels running experiment
  - `RunLogViewSet`: Read-only access to logs
- **Tasks**:
  - `run_experiment_task`: Celery task that executes experiment

#### b. `orchestrator` (Execution Engine)
- **Components**:
  - `Parser`: Tokenizes and parses custom experiment syntax
  - `VirtualProcessor`: Executes AST (Abstract Syntax Tree)
  - `ExperimentEngine`: Coordinates execution, logging, data saving
  - `InstrumentClient`: ZMQ client for instrument communication
- **No direct frontend access** - all requests go through `expmanager`

#### c. `visualizer` (Real-time Communication)
- **Consumers**:
  - `ExperimentConsumer`: WebSocket handler for real-time updates
- **Channels**: Django Channels for WebSocket support

### 3. Background Services

#### Celery Worker
- Executes experiments asynchronously
- Allows concurrent experiment queuing
- Priority-based execution
- Handles interruption signals

#### Redis (Message Broker)
- Celery task queue
- Django Channels layer for WebSocket messaging

### 4. External Services

#### InstrumentServer
- Separate process (not part of QuAIL)
- REP socket on port 5555
- Manages QCoDeS instruments
- Handles parameter get/set and method calls

---

## Complete Control Flow

### Phase 1: Experiment Preparation

```
1. User creates/edits ExperimentScript via API
   POST /api/experiment-scripts/
   ├─ Script code (custom syntax)
   ├─ FPGA code (optional)
   └─ Metadata (name, description)

2. Frontend creates ExperimentRun
   POST /api/run-requests/
   ├─ Links to ExperimentScript
   ├─ Sets priority
   └─ Status: QUEUED
```

### Phase 2: Experiment Initiation

```
3. User clicks "Start" in UI
   POST /api/run-requests/{id}/start/

4. ExperimentRunViewSet.start() [expmanager/views.py]
   ├─ Validates status == QUEUED
   ├─ Calls: run_experiment_task.apply_async(args=[run_id], priority=run_request.priority)
   └─ Returns: {status: "Experiment queued", task_id: "..."}

5. Celery Worker picks up task [expmanager/tasks.py]
   └─ run_experiment_task(run_id)
```

### Phase 3: Experiment Execution

```
6. Task updates ExperimentRun
   ├─ status = RUNNING
   ├─ started_at = now()
   └─ save()

7. Creates ExperimentEngine [orchestrator/engine.py]
   ├─ Initializes InstrumentClient (ZMQ connection to InstrumentServer)
   ├─ Sets up WebSocket channel layer
   └─ Prepares context for VirtualProcessor

8. ExperimentEngine.run()
   ├─ Reads script code from ExperimentScript
   ├─ Sets up log directory
   ├─ Creates context:
   │   ├─ sleep: time.sleep
   │   ├─ instruments: InstrumentClient
   │   ├─ fpga_code: script.fpga_code
   │   ├─ save_data: self.save_data
   │   ├─ init_data: self.init_data
   │   └─ check_abort: self.check_abort
   └─ Creates VirtualProcessor(context, log_dir)

9. VirtualProcessor.execute(script_code) [orchestrator/processor.py]
   ├─ Parser creates AST from script
   └─ Executes AST node by node
```

### Phase 4: Script Execution (Example)

```python
# Example script:
init_data(param=("voltage", "current"))
for v in (from 0 to 5 step 0.1):
    set dac.voltage v
    sleep(0.1)
    i = get multimeter.current
    d = run("seq1", fpga_code)
    save(params=(v, i), data=d)
```

**Execution flow:**

```
10. INIT_DATA command
    ├─ VirtualProcessor validates schema
    ├─ Calls context['init_data'](schema)
    └─ ExperimentEngine.init_data()
        ├─ Creates HDF5 file: data/run_{id}.h5
        ├─ Creates datasets:
        │   ├─ data: (4, 0, 0) -> will be (4, N, num_saves)
        │   └─ params: (2, 0) -> will be (2, num_saves)
        └─ Updates ExperimentRun.data_file

11. FOR loop iteration (e.g., v=0.5)
    
    a. SET command
       ├─ context['instruments'].set_parameter("dac", "voltage", 0.5)
       └─ InstrumentClient._send()
           ├─ Creates ServerInstruction JSON
           ├─ Sends via ZMQ REQ socket
           ├─ Waits for response from InstrumentServer
           └─ InstrumentServer executes: dac.voltage(0.5)
    
    b. SLEEP command
       └─ context['sleep'](0.1) → time.sleep(0.1)
    
    c. GET command
       ├─ context['instruments'].get_parameter("multimeter", "current")
       └─ InstrumentClient._send()
           ├─ Sends get_param_dict instruction
           ├─ Receives response with value
           └─ Returns value to VirtualProcessor
       └─ Stores in variable: i = <value>
    
    d. RUN command
       ├─ context['instruments'].run_fpga("sequencer", fpga_code)
       └─ Returns mock data (4, N) array
       └─ Stores in variable: d = [[...], [...], [...], [...]]
    
    e. SAVE command
       ├─ Validates params=(v, i) matches schema
       ├─ Validates data shape is (4, N)
       ├─ Calls context['save_data'](params=(v, i), data=d)
       └─ ExperimentEngine.save_data()
           ├─ Opens HDF5 file
           ├─ First save: Sets N, resizes to (4, N, 0)
           ├─ Subsequent saves: Validates N matches
           ├─ Appends new slice:
           │   ├─ data[:, :, i] = new_data (4, N)
           │   └─ params[:, i] = [v, i]
           └─ Sends to WebSocket:
               channel_layer.group_send("run_{id}", {
                   type: 'new_data',
                   params: (v, i),
                   data: [[...], [...], [...], [...]]
               })

12. WebSocket receives data [visualizer/consumers.py]
    └─ ExperimentConsumer.new_data(event)
        └─ Sends JSON to frontend:
            {
                type: 'data',
                params: [0.5, 0.002],
                data: [[...], [...], [...], [...]]
            }

13. Frontend receives and visualizes
    ├─ Updates live plot
    └─ Shows current parameter values
```

### Phase 5: Periodic Abort Check

```
14. Between each statement execution:
    └─ context['check_abort']()
        ├─ run_request.refresh_from_db()
        └─ if status == 'CANCELLED':
            raise InterruptedError()
```

### Phase 6: Experiment Completion

```
15. Normal completion:
    ├─ VirtualProcessor finishes all commands
    ├─ ExperimentEngine.run() returns
    └─ Task updates ExperimentRun:
        ├─ status = COMPLETED
        ├─ completed_at = now()
        └─ save()

16. Error/Abort:
    └─ Exception caught in task
        ├─ InterruptedError → status = CANCELLED
        └─ Other Exception → status = FAILED

17. Final cleanup:
    └─ Task returns final status
```

---

## User Abort Flow

```
User clicks "Abort" in UI
    ↓
POST /api/run-requests/{id}/abort/
    ↓
ExperimentRunViewSet.abort()
    ├─ Validates status in [QUEUED, RUNNING]
    ├─ Sets status = CANCELLED
    └─ save()
    ↓
Next VirtualProcessor statement
    ↓
check_abort() detects CANCELLED
    ↓
Raises InterruptedError
    ↓
Propagates up to task
    ↓
Task catches InterruptedError
    ├─ Logs "Experiment cancelled"
    └─ Keeps status = CANCELLED
```

---

## Data Flow Summary

### Logs
```
VirtualProcessor → self.logger (Python logging)
                → FileHandler (logs/experiment_{timestamp}.log)
                
ExperimentEngine.log() → RunLog.objects.create()
                       → channel_layer.group_send(type='log_message')
                       → WebSocket → Frontend
```

### Experiment Data
```
save() command → ExperimentEngine.save_data()
              → HDF5 file (data/run_{id}.h5)
                  ├─ data: (4, N, num_saves)
                  └─ params: (num_params, num_saves)
              → channel_layer.group_send(type='new_data')
              → WebSocket → Frontend (live plotting)
```

### Database
```
ExperimentRun: Tracks experiment status, priority, data file path
RunLog: Persistent log storage
ExperimentScript: Script code storage
```

---

## Technology Stack

- **Backend Framework**: Django 4.x
- **API**: Django REST Framework
- **Real-time**: Django Channels (WebSocket)
- **Task Queue**: Celery + Redis
- **Database**: PostgreSQL/SQLite
- **Data Storage**: HDF5 (via h5py)
- **Instrument Communication**: ZMQ (REQ/REP pattern)
- **Instrument Control**: QCoDeS (external)

---

## Key Design Decisions

1. **Separation of Concerns**:
   - `expmanager`: User interface, job scheduling
   - `orchestrator`: Execution engine, no frontend access
   - `visualizer`: Real-time communication only

2. **Asynchronous Execution**:
   - Celery workers prevent blocking web server
   - Priority queue for multi-user support

3. **3D Data Structure**:
   - Each parameter set maps to one (4, N) measurement
   - Efficient storage and indexing
   - Strict dimension validation

4. **Graceful Interruption**:
   - Status-based cancellation
   - Periodic checks during execution
   - Clean state transitions

5. **Real-time Updates**:
   - WebSocket for logs and data
   - Non-blocking data streaming
   - Frontend stays responsive

---

## File Structure

```
quail/
├─ expmanager/          # User-facing experiment management
│  ├─ models.py         # ExperimentScript, ExperimentRun, RunLog
│  ├─ views.py          # REST API endpoints
│  ├─ tasks.py          # Celery tasks
│  └─ serializers.py
├─ orchestrator/        # Execution engine
│  ├─ engine.py         # ExperimentEngine
│  ├─ processor.py      # VirtualProcessor
│  ├─ parser.py         # Lexer & Parser
│  └─ instrument_client.py  # ZMQ client
├─ visualizer/          # Real-time communication
│  └─ consumers.py      # WebSocket consumers
├─ data/                # HDF5 experiment data (gitignored)
├─ logs/                # Experiment logs (gitignored)
└─ quail/               # Django project settings
   ├─ settings.py
   ├─ celery.py
   └─ asgi.py
```
