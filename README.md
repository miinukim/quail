# QuAIL (QuIQCL Automated Instrument Linking system)

QuAIL is a Django-based server designed to manage and execute physics experiments using QCoDeS and `instrumentserver`. It features a custom domain-specific language for experiment scripts, priority-based job scheduling, and real-time data streaming.

## Documentation

- **[Syntax Guide](docs/syntax_guide.md)** - Complete reference for the QuAIL experiment scripting language
- **[System Architecture](docs/SYSTEM_ARCHITECTURE.md)** - Detailed system design and control flow documentation

## Architecture

The system is divided into three Django apps, each with a specific responsibility:

### 1. `expmanager` (Experiment Manager)
- **Role**: User-facing experiment management and job scheduling.
- **Responsibilities**:
    - Manages the database of Experiment Scripts and Run Requests.
    - Handles priority-based job queue via Celery.
    - Exposes the REST API for experiment submission and control.
    - Manages experiment lifecycle (start, abort, status tracking).

### 2. `orchestrator` (Execution Engine)
- **Role**: Experiment execution engine.
- **Responsibilities**:
    - Parses and executes custom experiment syntax.
    - Runs experiments in background Celery workers.
    - Manages instrument communication via ZMQ to InstrumentServer.
    - Handles data collection and persistence (HDF5).
    - Provides abort/interrupt capability.

### 3. `visualizer` (Real-time Interface)
- **Role**: Real-time monitoring and data streaming.
- **Responsibilities**:
    - Streams live logs and data points to frontend using WebSockets (Django Channels).
    - Allows users to monitor experiments as they happen.
    - Provides real-time visualization updates.

## Key Features

- **Custom Experiment Syntax**: Domain-specific language for experiment control
- **Priority Queue**: Multi-user support with priority-based scheduling
- **Real-time Updates**: WebSocket-based live logging and data streaming
- **Graceful Interruption**: User-initiated experiment cancellation
- **3D Data Storage**: Efficient HDF5-based storage (4, N, M) for parameter-indexed measurements
- **Instrument Abstraction**: ZMQ communication with QCoDeS InstrumentServer

## Getting Started

### Prerequisites
- Python 3.10+
- Redis (for Celery and Channels)
- Conda environment (recommended)

### Installation

1.  **Create and activate conda environment**:
    ```bash
    conda create -n ent python=3.10
    conda activate ent
    ```

2.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

3.  **Run Migrations**:
    ```bash
    python manage.py migrate
    ```

4.  **Start Redis**:
    ```bash
    redis-server
    ```

### Running the Server

1.  **Start Django Web Server**:
    ```bash
    python manage.py runserver
    ```

2.  **Start Celery Worker** (in separate terminal):
    ```bash
    celery -A quail worker -l info -c 1
    ```
    Note: `-c 1` ensures serial execution for hardware access

3.  **Start InstrumentServer** (in separate terminal, external dependency):
    ```bash
    # See instrumentserver documentation
    instrumentserver-server
    ```

## Usage

1.  **Create an Experiment Script**: Define your experiment using the QuAIL syntax
2.  **Queue a Run**: Create a `RunRequest` via the API
3.  **Start**: Trigger execution via POST to `/api/run-requests/{id}/start/`
4.  **Monitor**: Connect to WebSocket at `/ws/experiment/{run_id}/` for live updates
5.  **Abort** (optional): Cancel via POST to `/api/run-requests/{id}/abort/`

### Example Script

```python
# Initialize data schema
init_data(param=("voltage", "current"))

# Sweep voltage and collect data
for v in (from 0 to 5 step 0.1):
    set dac.voltage v
    sleep(0.1)
    i = get multimeter.current
    d = run("sequencer", fpga_code)
    save(params=(v, i), data=d)
```

See [Syntax Guide](docs/syntax_guide.md) for complete language reference.

## Data Structure

Experiment data is stored in HDF5 files with the following structure:
- **data**: `(4, N, num_saves)` - 4 channels × N points × M parameter combinations
- **params**: `(num_params, num_saves)` - Parameter values for each measurement

## Development

- **Database**: SQLite (development) / PostgreSQL (production)
- **Task Queue**: Celery with Redis broker
- **WebSocket**: Django Channels with Redis channel layer
- **Data Format**: HDF5 via h5py
- **Instrument Protocol**: ZMQ REQ/REP pattern
