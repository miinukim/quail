#!/usr/bin/env bash
# QuAIL + MAQPIE dev stack helper for Linux/macOS:
# - Django backend (8000)
# - MAQPIE Vite frontend (default 5173)
#
# Usage:
#   ./command.sh setup
#   ./command.sh migrate
#   ./command.sh superuser
#   ./command.sh start
#   ./command.sh stop
#   ./command.sh status
#   ./command.sh clean-logs
#
# Env vars:
#   CONDA_ENV        conda env name (default ent)
#   PYTHON_VERSION   python version when creating env (default 3.10)
#   FRONTEND_PORT    vite port (default 5173)
#   VITE_API_BASE    frontend API base (default http://localhost:8000/api)
#   DJANGO_HOST      django bind host (default 0.0.0.0)
#   DJANGO_PORT      django port (default 8000)
#   INSTRUMENTSERVER_PORT instrumentserver port (default 5555)
#   INSTRUMENTSERVER_CONFIG instrumentserver config path
#   REDIS_PORT       redis port (default 6379)

set -euo pipefail
shopt -s nullglob

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$PROJECT_ROOT/quail"
FRONTEND_DIR="$PROJECT_ROOT/maqpie"
PID_DIR="$PROJECT_ROOT/.pids"
LOG_DIR="$PROJECT_ROOT/.logs"

CONDA_ENV="${CONDA_ENV:-ent}"
PYTHON_VERSION="${PYTHON_VERSION:-3.10}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
VITE_API_BASE="${VITE_API_BASE:-http://localhost:8000/api}"
DJANGO_HOST="${DJANGO_HOST:-0.0.0.0}"
DJANGO_PORT="${DJANGO_PORT:-8000}"
INSTRUMENTSERVER_PORT="${INSTRUMENTSERVER_PORT:-5555}"
INSTRUMENTSERVER_CONFIG="${INSTRUMENTSERVER_CONFIG:-$PROJECT_ROOT/instrumentserver/instrumentserver/config/serverConfig.yml}"
REDIS_PORT="${REDIS_PORT:-6379}"

mkdir -p "$PID_DIR" "$LOG_DIR"

print() { printf '[start] %s\n' "$*"; }

die() { print "error: $*"; exit 1; }

require_dir() {
  [[ -d "$1" ]] || die "missing directory: $1"
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "$1 not found in PATH"
}

# --- Node/Vite helpers -------------------------------------------------------

ensure_node_for_vite() {
  # Prefer nvm-managed Node if available, since system Node may be too old.
  local nvm_sh="${NVM_DIR:-$HOME/.nvm}/nvm.sh"
  if [[ -s "$nvm_sh" ]]; then
    export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
    # shellcheck disable=SC1090
    . "$nvm_sh"
    nvm use --silent default >/dev/null 2>&1 || true
  fi

  require_cmd node
  local node_version node_major node_minor
  node_version="$(node -p 'process.versions.node')"
  IFS='.' read -r node_major node_minor _ <<<"$node_version"

  if ! { (( node_major > 22 )) || (( node_major == 22 && node_minor >= 12 )) || (( node_major == 20 && node_minor >= 19 )); }; then
    die "Node.js $node_version is too old for Vite (need >=20.19 or >=22.12). Install with: nvm install 22 && nvm alias default 22"
  fi
}

# --- Conda helpers ------------------------------------------------------------

# In non-interactive shells, conda may not be initialized. Try common init hooks.
ensure_conda() {
  if command -v conda >/dev/null 2>&1; then
    return 0
  fi

  # Try to source conda.sh from common locations
  local candidates=(
    "$HOME/miniconda3/etc/profile.d/conda.sh"
    "$HOME/anaconda3/etc/profile.d/conda.sh"
    "/opt/conda/etc/profile.d/conda.sh"
  )
  for f in "${candidates[@]}"; do
    if [[ -f "$f" ]]; then
      # shellcheck disable=SC1090
      source "$f"
      break
    fi
  done

  command -v conda >/dev/null 2>&1 || die "conda not found. Install Miniconda/Anaconda or ensure conda is on PATH."
}

conda_run() {
  conda run -n "$CONDA_ENV" "$@"
}

ensure_conda_env() {
  ensure_conda
  if ! conda env list | awk '{print $1}' | grep -qx "$CONDA_ENV"; then
    print "creating conda env '$CONDA_ENV' (python $PYTHON_VERSION)"
    conda create -y -n "$CONDA_ENV" "python=$PYTHON_VERSION"
  fi
  # sanity check
  conda_run python -V >/dev/null
}

# --- Redis check (warn only) --------------------------------------------------

# --- PID helpers --------------------------------------------------------------

pidfile() { echo "$PID_DIR/$1.pid"; }
logfile() { echo "$LOG_DIR/$1.log"; }

is_running_pid() {
  local pid="$1"
  [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1
}

read_pid() {
  local f="$1"
  [[ -f "$f" ]] || return 1
  cat "$f"
}

write_pid() {
  local name="$1" pid="$2"
  echo "$pid" > "$(pidfile "$name")"
}

cleanup_stale_pidfile() {
  local name="$1"
  local f; f="$(pidfile "$name")"
  if [[ -f "$f" ]]; then
    local pid; pid="$(cat "$f" || true)"
    if [[ -z "$pid" ]] || ! is_running_pid "$pid"; then
      rm -f "$f"
    fi
  fi
}

start_bg() {
  # start_bg <name> <workdir> <command_string>
  local name="$1"
  local workdir="$2"
  local cmd="$3"

  cleanup_stale_pidfile "$name"
  local f; f="$(pidfile "$name")"
  if [[ -f "$f" ]]; then
    local pid; pid="$(cat "$f")"
    if is_running_pid "$pid"; then
      print "$name: already running (pid $pid)"
      return 0
    fi
  fi

  local out; out="$(logfile "$name")"
  print "starting $name (log: $out)"
  (
    cd "$workdir"
    # Use a login-ish shell so conda + PATH behave consistently
    nohup bash -lc "$cmd" >"$out" 2>&1 &
    echo $! >"$f"
  )

  local pid; pid="$(cat "$f")"
  if is_running_pid "$pid"; then
    print "$name: started (pid $pid)"
  else
    die "$name failed to start; check $out"
  fi
}


stop_name() {
  local name="$1"
  local f; f="$(pidfile "$name")"
  if [[ ! -f "$f" ]]; then
    print "$name: no pid file"
    return 0
  fi

  local pid; pid="$(cat "$f" || true)"
  if [[ -z "$pid" ]]; then
    rm -f "$f"
    print "$name: empty pid file removed"
    return 0
  fi

  if ! is_running_pid "$pid"; then
    rm -f "$f"
    print "$name: not running (stale pid $pid)"
    return 0
  fi

  print "stopping $name (pid $pid)"
  # Some launchers (e.g., npm/vite) spawn child processes; stop children first.
  if command -v pkill >/dev/null 2>&1; then
    pkill -TERM -P "$pid" >/dev/null 2>&1 || true
  fi
  kill "$pid" >/dev/null 2>&1 || true

  # wait up to 5 seconds
  for _ in {1..50}; do
    if ! is_running_pid "$pid"; then
      rm -f "$f"
      print "$name: stopped"
      return 0
    fi
    sleep 0.1
  done

  print "$name: still running; sending SIGKILL"
  if command -v pkill >/dev/null 2>&1; then
    pkill -KILL -P "$pid" >/dev/null 2>&1 || true
  fi
  kill -9 "$pid" >/dev/null 2>&1 || true
  rm -f "$f"
  print "$name: killed"
}

status_name() {
  local name="$1"
  local f; f="$(pidfile "$name")"
  if [[ ! -f "$f" ]]; then
    print "$name: not tracked"
    return 0
  fi
  local pid; pid="$(cat "$f" || true)"
  if [[ -z "$pid" ]]; then
    print "$name: stale (empty pid file)"
    return 0
  fi
  if is_running_pid "$pid"; then
    print "$name: running (pid $pid)"
  else
    print "$name: not running (stale pid $pid)"
  fi
}

stop_port_listeners() {
  # stop_port_listeners <port> <label>
  local port="$1"
  local label="$2"
  local pids=""

  if command -v lsof >/dev/null 2>&1; then
    pids="$(lsof -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | tr '\n' ' ')"
  fi

  if [[ -z "${pids// }" ]]; then
    print "$label: no listener on port $port"
    return 0
  fi

  print "$label: stopping listener(s) on port $port: ${pids}"
  # shellcheck disable=SC2086
  kill $pids >/dev/null 2>&1 || true
  sleep 0.3

  local still=""
  if command -v lsof >/dev/null 2>&1; then
    still="$(lsof -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | tr '\n' ' ')"
  fi
  if [[ -n "${still// }" ]]; then
    print "$label: force-killing listener(s) on port $port: ${still}"
    # shellcheck disable=SC2086
    kill -9 $still >/dev/null 2>&1 || true
  fi
}

check_port_service() {
  # check_port_service <port> <label>
  local port="$1"
  local label="$2"
  if command -v nc >/dev/null 2>&1; then
    if nc -z 127.0.0.1 "$port" >/dev/null 2>&1; then
      print "$label: reachable on 127.0.0.1:$port"
    else
      print "$label: not reachable on 127.0.0.1:$port"
    fi
  elif command -v lsof >/dev/null 2>&1; then
    if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
      print "$label: listening on port $port"
    else
      print "$label: not listening on port $port"
    fi
  else
    print "$label: unable to check (need nc or lsof)"
  fi
}

port_listener_pids() {
  # port_listener_pids <port>
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | tr '\n' ' ' || true
  fi
}

# --- Commands ----------------------------------------------------------------

setup() {
  require_dir "$BACKEND_DIR"
  require_dir "$FRONTEND_DIR"
  ensure_conda_env
  ensure_node_for_vite
  require_cmd npm

  print "backend: upgrading pip and installing deps in conda env '$CONDA_ENV'"
  conda_run python -m pip install --upgrade pip
  if [[ -f "$BACKEND_DIR/requirements.txt" ]]; then
    conda_run python -m pip install -r "$BACKEND_DIR/requirements.txt"
  fi
  # editable install if backend dir is a python package
  conda_run python -m pip install -e "$BACKEND_DIR"

  print "frontend: npm install"
  (cd "$FRONTEND_DIR" && npm install)

  print "database: creating/updating migrations and applying them"
  (cd "$BACKEND_DIR" && conda_run python manage.py makemigrations)
  (cd "$BACKEND_DIR" && conda_run python manage.py migrate)

  print "database: ensuring Django superuser exists (user / userpw0000)"
  (cd "$BACKEND_DIR" && conda_run python manage.py shell -c "
from django.contrib.auth import get_user_model
User = get_user_model()
u, p, e = 'user', 'userpw0000', 'user@snu.ac.kr'
if not User.objects.filter(username=u).exists():
    User.objects.create_superuser(u, e, p)
print('SUPERUSER_OK')
")
  print "setup complete"
}

migrate() {
  require_dir "$BACKEND_DIR"
  ensure_conda_env
  print "running Django migrations"
  (cd "$BACKEND_DIR" && conda_run python manage.py migrate)
}

superuser() {
  require_dir "$BACKEND_DIR"
  ensure_conda_env
  print "ensuring Django superuser exists (user / userpw0000)"
  (cd "$BACKEND_DIR" && conda_run python manage.py shell -c "
from django.contrib.auth import get_user_model
User = get_user_model()
u, p, e = 'user', 'userpw0000', 'user@snu.ac.kr'
if not User.objects.filter(username=u).exists():
    User.objects.create_superuser(u, e, p)
print('SUPERUSER_OK')
")
}

start_backend() {
  print "starting Django dev server on ${DJANGO_HOST:-0.0.0.0}:${DJANGO_PORT:-8000}"
  start_bg django "$BACKEND_DIR" \
    "conda run -n '$CONDA_ENV' python manage.py runserver --noreload ${DJANGO_HOST:-0.0.0.0}:${DJANGO_PORT:-8000}"
}

start_instrumentserver() {
  ensure_conda_env
  [[ -f "$INSTRUMENTSERVER_CONFIG" ]] || die "instrumentserver config not found: $INSTRUMENTSERVER_CONFIG"
  local listeners=""
  listeners="$(port_listener_pids "${INSTRUMENTSERVER_PORT:-5555}")"
  if [[ -n "${listeners// }" ]]; then
    print "instrumentserver: port ${INSTRUMENTSERVER_PORT:-5555} already in use (pid: ${listeners}); skipping launch"
    return 0
  fi
  print "starting instrumentserver on ${INSTRUMENTSERVER_PORT:-5555} with config ${INSTRUMENTSERVER_CONFIG}"
  start_bg instrumentserver "$PROJECT_ROOT" \
    "conda run -n '$CONDA_ENV' instrumentserver -p '${INSTRUMENTSERVER_PORT:-5555}' -c '${INSTRUMENTSERVER_CONFIG}'"
}

start_celery() {
  require_dir "$BACKEND_DIR"
  ensure_conda_env
  print "starting Celery worker (log level info, concurrency 1)"
  start_bg celery "$BACKEND_DIR" \
    "conda run -n '$CONDA_ENV' celery -A quail worker -l info -c 1 --logfile '${LOG_DIR}/celery.log'"
}

start_frontend() {
  local port="${FRONTEND_PORT:-5173}"
  local api_base="${VITE_API_BASE:-http://localhost:8000/api}"
  ensure_node_for_vite
  cleanup_stale_pidfile frontend
  local listeners=""
  listeners="$(port_listener_pids "$port")"

  if [[ -n "${listeners// }" ]]; then
    print "frontend: port ${port} already in use (pid: ${listeners}); skipping launch"
    return 0
  fi

  print "starting MAQPIE dev server on 0.0.0.0:${port} (API base ${api_base})"
  start_bg frontend "$FRONTEND_DIR" \
    "if [ -s \"\$HOME/.nvm/nvm.sh\" ]; then export NVM_DIR=\"\${NVM_DIR:-\$HOME/.nvm}\"; . \"\$NVM_DIR/nvm.sh\"; nvm use --silent default >/dev/null 2>&1 || true; fi; export VITE_API_BASE='${api_base}'; npm run dev -- --host 0.0.0.0 --port '${port}' --strictPort"
}

start_all() {
  start_backend
  start_instrumentserver
  start_celery
  start_frontend

  print "services started; logs in $LOG_DIR, pids in $PID_DIR"
}

stop_all() {
  stop_name frontend
  stop_name instrumentserver
  stop_name celery
  stop_name django
  # Celery does not bind a fixed service port, so also clean up any stray
  # QuAIL workers that may have survived older launches or stale pid tracking.
  if command -v pkill >/dev/null 2>&1; then
    pkill -f "celery -A quail worker" >/dev/null 2>&1 || true
  fi
  # Fallback for stale pid files or manually started servers.
  stop_port_listeners "${DJANGO_PORT:-8000}" "django"
  stop_port_listeners "${FRONTEND_PORT:-5173}" "frontend"
  stop_port_listeners "${INSTRUMENTSERVER_PORT:-5555}" "instrumentserver"
  if [[ "${FRONTEND_PORT:-5173}" != "5173" ]]; then
    stop_port_listeners "5173" "frontend(default)"
  fi
  print "stopped"
}

status_all() {
  status_name django
  status_name instrumentserver
  status_name celery
  status_name frontend
  check_port_service "${INSTRUMENTSERVER_PORT:-5555}" "instrumentserver"
  check_port_service "${REDIS_PORT:-6379}" "redis"
  print "logs: $LOG_DIR"
}

clean_logs() {
  local backend_log_dir="$BACKEND_DIR/logs"
  mkdir -p "$LOG_DIR" "$backend_log_dir"
  local count=0
  local f
  for f in "$LOG_DIR"/*.log "$backend_log_dir"/*.log; do
    [[ -f "$f" ]] || continue
    : > "$f"
    count=$((count + 1))
  done
  print "log cleanup complete; truncated ${count} file(s)"
}

usage() {
  cat <<USAGE
Usage: ./command.sh [setup|migrate|superuser|start|stop|status|clean-logs]
  setup        create conda env, install deps, and initialize DB/superuser
  migrate      run Django migrations
  superuser    ensure default Django superuser exists
  start        launch Django + frontend
  stop         stop tracked processes
  status       show tracked process state
  clean-logs   truncate runtime logs in .logs and quail/logs

Env vars:
  CONDA_ENV        conda env name (default ent)
  PYTHON_VERSION   python version when creating env (default 3.10)
  FRONTEND_PORT    Vite port (default 5173)
  VITE_API_BASE    API base URL (default http://localhost:8000/api)
  DJANGO_HOST      Django bind host (default 0.0.0.0)
  DJANGO_PORT      Django port (default 8000)
  INSTRUMENTSERVER_PORT instrumentserver port (default 5555)
  INSTRUMENTSERVER_CONFIG instrumentserver config path
  REDIS_PORT       redis port (default 6379)
USAGE
}

# --- CLI ---------------------------------------------------------------------

cmd="${1:-help}"
shift || true

case "$cmd" in
  setup) setup ;;
  migrate) migrate ;;
  superuser) superuser ;;
  start) start_all ;;
  stop) stop_all ;;
  status) status_all ;;
  clean-logs) clean_logs ;;
  *) usage ;;
esac
