# HeyClaw

Python 3.12 voice assistant powered by ElevenLabs, Gemini, Mem0, and MCP tools. Server code and tests live in `heyclaw/`; local audio and wake-word code lives in `satellite/app/`, with tests in `satellite/tests/`. `shared/heyclaw_shared/` is the `heyclaw-shared` package that both components install as an editable path dependency; it holds the settings, logging, performance instrumentation, and ElevenLabs provider config they have in common. `heyclaw/workspace/` contains the instructions, profile, and skills loaded by the assistant at runtime.

## Configuration

- Requires Python 3.12, `uv`, GNU Make, `ngrok`, and `lsof`.
- Prepare `heyclaw/.env` from `heyclaw/.env.example`. It contains logging settings only.
- Prepare `heyclaw/config.json` from `heyclaw/config.example.json`. It defines server providers, gateway, agent defaults, workspace, memory, and MCP servers.
- Prepare `satellite/.env` and `satellite/config.json` from their example files. They define local logging, audio, wake word, and the ElevenLabs connection.
- Never commit credentials from either component.
- The full development stack exposes the HTTP API on port 8000, the Speech Engine WebSocket on port 3001, and the ngrok tunnel configured by `gateway.publicWsUrl`.

## Unix/WSL Commands

Use only `Makefile` targets from the repository root:

- `make setup`: installs Python 3.12 and dependencies for both components; each `uv sync` also installs `shared/` in editable mode.
- `make backend`: stops local project processes, then starts the backend, Speech Engine, and ngrok tunnel.
- `make satellite`: starts the interactive voice satellite; it requires microphone and audio devices configured in `satellite/config.json`.
- `make kill`: stops processes on the ports used by the project.
- `make check`: formats the code, applies Ruff fixes, and runs type checking. It may modify files.

After `make setup`, `uv run --project satellite heyclaw-satellite onboard`
creates or refreshes both component `config.json` files without replacing existing
values. It also recommends explicit PortAudio device indices; it is a CLI workflow,
not a Makefile target. Pass `--update-audio` to replace only the existing audio
indices and echo mode after the connected hardware changes; interactive terminals
show selectable lists powered by Questionary.
If the new hardware is not the Windows default, pass `--input-device-index` and/or
`--output-device-index` together with `--update-audio` after inspecting
`heyclaw-satellite devices`.

Always specify a target: the default `help` goal is not defined.

## Windows (Secondary)

Windows uses the separate `Makefile.windows`, backed by `heyclaw/scripts/dev.ps1`. Invoke it explicitly with `make -f Makefile.windows <target>`. It exposes the same active targets as the Linux Makefile and requires PowerShell 7, GNU Make, `uv`, and `ngrok` in `PATH`.

## Working Rules

- Treat `Makefile`, `Makefile.windows`, and each component's `.env.example`, `config.example.json`, and `pyproject.toml` as the operational sources of truth.
- Code needed by both `heyclaw/` and `satellite/` belongs in `shared/heyclaw_shared/`; neither component may import from the other.
- Do not modify either Makefile unless explicitly requested.
- Never expose or commit either component's `.env` or `config.json`.
- Use `make check` for static verification; tests currently have no dedicated `Makefile` target.
