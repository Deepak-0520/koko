#!/usr/bin/env bash
# KOKO watchdog: keeps the national data build running until it is complete.
#
# Every CHECK_SECS it checks Docker, the database, the API, the PLATEAU download and disk space,
# repairs what it can (restart Docker Desktop / the database container / a crashed download),
# and loads newly staged files every LOAD_EVERY_SECS. It exits:
#   0  when every PLATEAU city is staged and loaded           ("ALL DONE")
#   10 when something needs a human (API down, disk nearly full, repeated failures)
# Log: data/logs/watchdog.log
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1
export PATH="$PATH:/c/Users/OosinPRO/AppData/Local/Programs/DockerDesktop/resources/bin" MSYS_NO_PATHCONV=1
PY="$ROOT/.venv/Scripts/python"
LOG="$ROOT/data/logs/watchdog.log"
CHECK_SECS=${CHECK_SECS:-120}
LOAD_EVERY_SECS=${LOAD_EVERY_SECS:-900}
MIN_FREE_GB=${MIN_FREE_GB:-15}
API_URL=${API_URL:-http://127.0.0.1:8000/health}

log() { echo "$(date '+%m-%d %H:%M:%S') $*" >> "$LOG"; }
alert() { log "ALERT: $*"; echo "ALERT: $*"; exit 10; }

pipeline_running() {  # is a koko_pipeline process with these args running?
  powershell -NoProfile -Command "@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -match 'koko_pipeline $1' }).Count" 2>/dev/null | tr -d '\r'
}

docker_ok() { timeout 30 docker version --format '{{.Server.Version}}' >/dev/null 2>&1; }
db_ok() { timeout 30 docker exec koko-db psql -U koko -d koko -tAc "select 1" 2>/dev/null | grep -q 1; }

ensure_docker_and_db() {
  if ! docker_ok; then
    log "docker engine not responding: restarting Docker Desktop"
    timeout 400 docker desktop restart --timeout 300 >> "$LOG" 2>&1
    sleep 20
    docker_ok || return 1
  fi
  if ! db_ok; then
    log "database not answering: docker compose up"
    timeout 300 docker compose up -d --wait >> "$LOG" 2>&1
    sleep 5
    db_ok || return 1
  fi
  return 0
}

staged_count() { ls "$ROOT/data/staged/buildings" 2>/dev/null | wc -l; }

log "watchdog started (check every ${CHECK_SECS}s, load every ${LOAD_EVERY_SECS}s)"
last_load=0
fail_streak=0
api_fail=0
retry_done=0
plateau_restarts=0

while true; do
  # 1. Docker + database (self-heal; give up after 3 failed rounds in a row)
  if ensure_docker_and_db; then fail_streak=0; else
    fail_streak=$((fail_streak + 1)); log "docker/database repair failed (${fail_streak}/3)"
    [ $fail_streak -ge 3 ] && alert "Docker or the database could not be repaired automatically"
    sleep "$CHECK_SECS"; continue
  fi

  # 2. API (managed by the Claude app preview, so it can only be reported)
  if curl -s -o /dev/null --max-time 20 -w '%{http_code}' "$API_URL" | grep -q 200; then api_fail=0; else
    api_fail=$((api_fail + 1)); log "API health check failed (${api_fail}/3)"
    [ $api_fail -ge 3 ] && alert "the API at $API_URL is down"
  fi

  # 3. disk space
  free_gb=$(df -BG /c | awk 'NR==2 {gsub("G","",$4); print $4}')
  if [ "${free_gb:-0}" -lt "$MIN_FREE_GB" ]; then
    powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -match 'koko_pipeline plateau' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force }" >/dev/null 2>&1
    alert "only ${free_gb} GB free on C: (minimum ${MIN_FREE_GB} GB); PLATEAU download paused"
  fi

  # 4. PLATEAU download: restart it if it died before finishing; retry failed cities once at the end
  running=$(pipeline_running "plateau")
  if [ "${running:-0}" = "0" ]; then
    if ! grep -q "^exit" data/logs/plateau_all.log 2>/dev/null; then
      plateau_restarts=$((plateau_restarts + 1))
      [ $plateau_restarts -gt 5 ] && alert "the PLATEAU download keeps stopping (restarted 5 times)"
      log "PLATEAU download is not running and never finished: restarting (cached cities are skipped)"
      ( "$PY" -I -X utf8 -m koko_pipeline plateau --cities all >> data/logs/plateau_all.log 2>&1; echo "exit $?" >> data/logs/plateau_all.log ) &
    elif [ $retry_done = 0 ]; then
      retry_done=1
      log "PLATEAU download finished ($(staged_count) staged files): retrying any failed cities once"
      ( "$PY" -I -X utf8 -m koko_pipeline plateau --cities all > data/logs/plateau_retry.log 2>&1; echo "exit $?" >> data/logs/plateau_retry.log ) &
    elif grep -q "^exit" data/logs/plateau_retry.log 2>/dev/null && [ "$(pipeline_running "load")" = "0" ]; then
      # everything staged: one last load, then finish
      log "retry finished: final load"
      sleep 70  # the loader skips files written in the last minute; let the newest one settle
      "$PY" -I -X utf8 -m koko_pipeline load >> data/logs/load_watchdog.log 2>&1
      n=$(timeout 30 docker exec koko-db psql -U koko -d koko -tAc "select count(*) || ' datasets, ' || sum(rows) || ' buildings' from koko.loaded_files where layer='buildings'")
      failed=$(grep -c "FAILED" data/logs/plateau_retry.log)
      log "ALL DONE: $n in the database; cities still failing after retry: $failed"
      echo "ALL DONE: $n; cities still failing after retry: $failed"
      exit 0
    fi
  fi

  # 5. load newly staged files (the loader takes a lock, so overlapping runs are skipped safely)
  now=$(date +%s)
  if [ $((now - last_load)) -ge "$LOAD_EVERY_SECS" ] && [ "$(pipeline_running "load")" = "0" ]; then
    last_load=$now
    log "loading newly staged files"
    "$PY" -I -X utf8 -m koko_pipeline load >> data/logs/load_watchdog.log 2>&1 \
      && log "load finished: $(grep -c 'buildings [0-9]*:' data/logs/load_watchdog.log) building files loaded so far by the watchdog" \
      || log "load exited with an error (see load_watchdog.log); will retry next round"
  fi

  sleep "$CHECK_SECS"
done
