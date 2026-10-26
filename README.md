# eugh-mcp

A small [Model Context Protocol](https://modelcontextprotocol.io) server that lets an AI assistant (Claude, as a custom connector) look after a Linux home server: check services, read logs, edit files, run commands and manage a Minecraft server, from a phone, without opening SSH to the internet.

It runs on my mini PC (Ubuntu, Ryzen 5 3500U) next to my websites, Discord bots and a modded Minecraft server.

## Tools

| Tool | What it does |
|---|---|
| `service_status` / `service_control` | systemd status, and start / stop / restart / reload for units the sudoers rule allows |
| `machine_health` | disk, memory, load, uptime and the biggest processes |
| `network_info` | interfaces, public IP, and whether DNS still points at it |
| `list_dir` / `read_file` / `write_file` | file access, limited to a few root folders; writes keep a `.bak` copy by default |
| `run_command` | a shell command with a timeout (a short deny-list blocks the obviously destructive ones) |
| `minecraft_status` / `minecraft_log` / `minecraft_command` | server state, log search, console commands via `screen` |

## How it's kept safe

The tools are deliberately broad, so the safety lives **outside** the Python file, in layers:

1. **Bound to localhost.** The server listens on `127.0.0.1:8787` only.
2. **Reverse proxy with an unguessable path.** nginx exposes it over HTTPS at a long random URL path (the connector dialog has no field for auth headers, so the secret lives in the path). Anything else gets a 404.
3. **systemd sandbox.** `ProtectSystem=strict` makes the whole OS read-only except the folders it works in, and `InaccessiblePaths=` hides secrets at kernel level (`.env` files, TLS keys, the nginx file holding the secret path), so not even `run_command` can read them.
4. **No root.** It runs as a normal user; anything privileged goes through narrow sudoers rules.

See [`eugh-mcp.service.example`](eugh-mcp.service.example) and [`nginx.example.conf`](nginx.example.conf).

## Setup

```bash
python3 -m venv venv
venv/bin/pip install "mcp[cli]<2"
sudo cp eugh-mcp.service.example /etc/systemd/system/eugh-mcp.service   # edit paths first
sudo systemctl enable --now eugh-mcp
```

Then put nginx in front of it (see the example), and add the HTTPS URL as a custom connector in Claude.

Edit `ALLOWED_ROOTS`, `EUGH` and `SCREEN_SESSION` at the top of `eugh_mcp.py` to match your machine.
