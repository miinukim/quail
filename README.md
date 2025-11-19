# QuAIL (QuIQCL Automated Instrument Linking system)

QuAIL is a Django-based server designed to manage and execute physics experiments using QCoDeS and `instrumentserver`. It employs a micro-service-like architecture to ensure stability, scalability, and real-time feedback.

## Architecture

The system is divided into four distinct Django apps, each with a specific responsibility:

### 1. `expmanager` (The Manager)
- **Role**: Administrative management.
- **Responsibilities**:
    - Manages the database of Experiment Scripts and Run Requests.
    - Handles the job queue.
    - Exposes the REST API for users to submit jobs.

### 2. `orchestrator` (The Engine)
- **Role**: Execution Engine.
- **Responsibilities**:
    - Runs the actual experiment logic in background processes (Celery Workers).
    - Creates a sandboxed environment for user scripts.
    - Decouples execution from the web server to prevent blocking.

### 3. `driver_proxy` (The Hardware Interface)
- **Role**: Hardware Abstraction Layer.
- **Responsibilities**:
    - Acts as a bridge to the external `instrumentserver`.
    - Handles RPC calls (ZMQ/gRPC) to control physical instruments.
    - Manages FPGA code upload and triggering.

### 4. `visualizer` (The Interface)
- **Role**: Real-time Monitoring.
- **Responsibilities**:
    - Streams live logs and data points to the frontend using WebSockets (Django Channels).
    - Allows users to monitor experiments as they happen.

## Getting Started

### Prerequisites
- Python 3.10+
- Redis (for Celery and Channels)

### Installation

1.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

2.  **Run Migrations**:
    ```bash
    python manage.py migrate
    ```

3.  **Start Redis**:
    ```bash
    redis-server
    ```

### Running the Server

1.  **Start Django Web Server**:
    ```bash
    python manage.py runserver
    ```

2.  **Start Celery Worker**:
    ```bash
    celery -A quail worker -l info
    ```

## Usage

1.  **Create an Experiment Script**: Upload your Python script via the Admin panel or API.
2.  **Queue a Run**: Create a `RunRequest` for your script.
3.  **Start**: Trigger the run via the API.
4.  **Monitor**: Connect to the WebSocket endpoint to see live progress.
