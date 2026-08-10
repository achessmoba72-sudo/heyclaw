SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

BACKEND_DIR := heyclaw
SATELLITE_DIR := satellite
SHARED_DIR := shared
BACKEND_UV_RUN := cd $(BACKEND_DIR) && uv run
SATELLITE_UV_RUN := cd $(SATELLITE_DIR) && uv run
PUBLIC_WS_HOST_CMD := cd "$(BACKEND_DIR)" && uv run python -c 'import json, sys; from pathlib import Path; from urllib.parse import urlparse; parsed = urlparse(json.loads(Path("config.json").read_text())["gateway"]["publicWsUrl"]); sys.exit("gateway.publicWsUrl must be a valid wss:// URL ending in /ws") if parsed.scheme != "wss" or not parsed.hostname or parsed.path != "/ws" else print(parsed.hostname)'

.PHONY: setup kill backend satellite backend-api backend-ngrok lint format typecheck check clean

setup:
	@cd "$(BACKEND_DIR)" && uv sync --all-groups --python 3.12
	@cd "$(SATELLITE_DIR)" && uv sync --all-groups --python 3.12

kill:
	@for port in 8000 3000 3001 8082 8765; do \
		pids=$$(lsof -ti :$$port 2>/dev/null || true); \
		if [[ -n "$$pids" ]]; then \
			for pid in $$pids; do \
				name=$$(ps -p $$pid -o comm= 2>/dev/null); \
				echo "  Stopping $$name (PID $$pid) on port $$port"; \
				kill $$pid 2>/dev/null || true; \
				sleep 1; \
				kill -9 $$pid 2>/dev/null || true; \
			done; \
		fi; \
	done

backend:
	@$(MAKE) --no-print-directory kill
	@backend_pid=; ngrok_pid=; \
	public_ws_host=$$($(PUBLIC_WS_HOST_CMD)); \
	cleanup() { \
		trap - EXIT INT TERM; \
		[[ -z "$$ngrok_pid" ]] || kill "$$ngrok_pid" 2>/dev/null || true; \
		[[ -z "$$backend_pid" ]] || kill "$$backend_pid" 2>/dev/null || true; \
		wait 2>/dev/null || true; \
	}; \
	trap cleanup EXIT; \
	trap 'exit 130' INT TERM; \
	$(BACKEND_UV_RUN) heyclaw-serve & backend_pid=$$!; \
	until lsof -t -sTCP:LISTEN -i :3001 >/dev/null 2>&1; do \
		if ! kill -0 "$$backend_pid" 2>/dev/null; then \
			wait "$$backend_pid" || exit $$?; \
			echo "The backend stopped before opening port 3001" >&2; \
			exit 1; \
		fi; \
		sleep 0.2; \
	done; \
	ngrok http --url="$$public_ws_host" 3001 & ngrok_pid=$$!; \
	wait -n "$$backend_pid" "$$ngrok_pid"

backend-api:
	cd $(BACKEND_DIR) && uv run uvicorn app.main:app --host localhost --port 8000

backend-ngrok:
	@public_ws_host=$$($(PUBLIC_WS_HOST_CMD)); \
	ngrok http --url="$$public_ws_host" 3001

satellite:
	@$(SATELLITE_UV_RUN) heyclaw-satellite

# ── Quality ───────────────────────────────────────────────────────────────────

lint:
	cd "$(BACKEND_DIR)" && uv run ruff check app/ tests/ ../$(SHARED_DIR)/heyclaw_shared/ --unsafe-fixes --fix
	cd "$(SATELLITE_DIR)" && uv run ruff check app/ tests/ --unsafe-fixes --fix

format:
	cd "$(BACKEND_DIR)" && uv run ruff format app/ tests/ ../$(SHARED_DIR)/heyclaw_shared/
	cd "$(SATELLITE_DIR)" && uv run ruff format app/ tests/

typecheck:
	cd "$(BACKEND_DIR)" && uv run mypy app/ ../$(SHARED_DIR)/heyclaw_shared/
	cd "$(SATELLITE_DIR)" && uv run mypy app/

check: format lint typecheck

clean:
	find $(BACKEND_DIR) $(SATELLITE_DIR) $(SHARED_DIR) -not -path '*/.venv/*' -type d \( \
		-name "__pycache__" \
		-o -name "logs" \
		-o -name ".pytest_cache" \
		-o -name ".ruff_cache" \
		-o -name ".mypy_cache" \
		-o -name "*.egg-info" \
	\) -exec rm -rf {} + 2>/dev/null || true
	find $(BACKEND_DIR) $(SATELLITE_DIR) $(SHARED_DIR) -not -path '*/.venv/*' -type f \( \
		-name ".coverage" \
		-o -name ".coverage.*" \
	\) -delete 2>/dev/null || true
