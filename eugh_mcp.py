#!/usr/bin/env python3
"""
theminipc — general admin MCP server (v3).

Broad access to Isaac's working directories so work can be done from a phone.
Safety comes from systemd sandboxing in eugh-mcp.service, not from this file:
secrets (.env, /etc/letsencrypt, the nginx config holding the MCP secret) are
made invisible at kernel level, so even run_command cannot reach them.

Install:
    /home/sleepy/mcp/venv/bin/pip install "mcp[cli]<2"
"""

import shlex
import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("theminipc")

EUGH = Path("/home/sleepy/eugh")
SCREEN_SESSION = "eugh"

# Roots the file tools will operate under. Anything else is refused here, and
# separately blocked by the systemd sandbox.
ALLOWED_ROOTS = [
    Path("/home/sleepy"),
    Path("/var/www"),
    Path("/etc/nginx"),          # read-only via ProtectSystem=strict
]

# Refused outright: destructive, or has a dedicated tool.
COMMAND_DENY = ("rm -rf /", "mkfs", "dd if=", ":(){", "shutdown", "reboot",
                "userdel", "passwd ")


def _run(cmd, timeout: int = 60, cwd=None) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, cwd=cwd)
        return (r.stdout + r.stderr).strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"Timed out after {timeout}s"
    except Exception as e:                                   # noqa: BLE001
        return f"Error: {e}"


def _safe(path: str) -> Path:
    """Resolve a path and refuse anything outside ALLOWED_ROOTS."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = Path("/home/sleepy") / p
    p = p.resolve()
    for root in ALLOWED_ROOTS:
        if p == root or root in p.parents:
            return p
    raise ValueError(f"Refused: {p} is outside the allowed roots "
                     f"({', '.join(str(r) for r in ALLOWED_ROOTS)})")


# ── services ────────────────────────────────────────────────────────────────

@mcp.tool()
def service_status(unit: str = "") -> str:
    """Status of a systemd unit, or a summary of the main ones if omitted.

    Known units: eugh (Minecraft), eugh-mcp (this server), nginx, php8.3-fpm,
    netdata, docker."""
    if unit:
        return _run(["systemctl", "status", unit, "--no-pager", "-n", "5"])
    out = []
    for u in ("eugh", "eugh-mcp", "nginx", "netdata"):
        out.append(f"{u:12} {_run(['systemctl', 'is-active', u])}")
    return "\n".join(out)


@mcp.tool()
def service_control(unit: str, action: str) -> str:
    """Start, stop or restart a systemd unit.

    Only units listed in the sudoers rule are permitted; anything else
    fails with a password prompt error."""
    if action not in ("start", "stop", "restart", "reload"):
        return "Refused: action must be start, stop, restart or reload."
    return _run(["sudo", "systemctl", action, unit], timeout=180)


@mcp.tool()
def machine_health() -> str:
    """Disk, memory, load and uptime on theminipc."""
    return (f"{_run(['df', '-h', '/'])}\n\n"
            f"{_run(['free', '-h'])}\n\n"
            f"uptime: {_run(['uptime'])}\n\n"
            f"top by memory:\n{_run(['bash', '-c', 'ps aux --sort=-%mem | head -6'])}")


@mcp.tool()
def network_info() -> str:
    """Public IP, what DNS resolves to, and whether they match.

    Use after a power cut — a changed public IP silently breaks the connector,
    the website and anyone connecting by IP."""
    public = _run(["curl", "-4", "-s", "--max-time", "10", "ifconfig.me"])
    local = _run(["bash", "-c", "hostname -I | awk '{print $1}'"])
    out = [f"public IP : {public}", f"local IP  : {local}", ""]
    for host in ("isaac-dev.eu", "mcp.isaac-dev.eu", "monitor.isaac-dev.eu"):
        resolved = _run(["dig", "+short", host, "@1.1.1.1"])
        flag = "OK" if public and public in resolved else "check Cloudflare"
        out.append(f"{host:24} -> {resolved or '(none)':16} {flag}")
    out.append("")
    out.append("listening ports:")
    out.append(_run(["bash", "-c", "ss -tulpn | grep LISTEN | head -20"]))
    return "\n".join(out)


# ── files ───────────────────────────────────────────────────────────────────

@mcp.tool()
def list_dir(path: str = "/home/sleepy") -> str:
    """List a directory. Absolute, or relative to /home/sleepy."""
    try:
        p = _safe(path)
    except ValueError as e:
        return str(e)
    return _run(["ls", "-la", str(p)])


@mcp.tool()
def read_file(path: str, lines: int = 0) -> str:
    """Read a file. Absolute, or relative to /home/sleepy.

    lines: if set, return only the last N lines (useful for logs)."""
    try:
        p = _safe(path)
    except ValueError as e:
        return str(e)
    if not p.is_file():
        return f"Not a file: {p}"
    if lines:
        return _run(["tail", "-n", str(max(1, min(int(lines), 500))), str(p)])[:20000]
    if p.stat().st_size > 200_000:
        return (f"Too large ({p.stat().st_size} bytes) — pass lines=N "
                f"or grep it with run_command.")
    return p.read_text(errors="replace")[:20000]


@mcp.tool()
def write_file(path: str, content: str, backup: bool = True) -> str:
    """Create or overwrite a file. Keeps a .bak copy by default.

    Config and code changes usually need a service reload to take effect."""
    try:
        p = _safe(path)
    except ValueError as e:
        return str(e)
    p.parent.mkdir(parents=True, exist_ok=True)
    note = ""
    if backup and p.exists():
        bak = p.with_suffix(p.suffix + ".bak")
        bak.write_text(p.read_text(errors="replace"))
        note = f" (previous version saved to {bak.name})"
    p.write_text(content)
    return f"Wrote {len(content)} bytes to {p}{note}"


@mcp.tool()
def run_command(command: str, cwd: str = "/home/sleepy", timeout: int = 60) -> str:
    """Run a shell command. Use for git, grep, tar, find, package queries, etc.

    The service is sandboxed: secrets and system directories are unreachable
    regardless of what the command says."""
    c = command.strip()
    if not c:
        return "Refused: empty command."
    for bad in COMMAND_DENY:
        if bad in c:
            return f"Refused: contains '{bad}'."
    try:
        wd = _safe(cwd)
    except ValueError as e:
        return str(e)
    return _run(["bash", "-lc", c], timeout=max(5, min(int(timeout), 300)),
                cwd=str(wd))


# ── minecraft ───────────────────────────────────────────────────────────────

@mcp.tool()
def minecraft_status() -> str:
    """Is the Minecraft server running and listening on 25565?"""
    unit = _run(["systemctl", "is-active", "eugh"])
    detail = _run(["systemctl", "status", "eugh", "--no-pager", "-n", "0"])
    ports = _run(["ss", "-tulpn"])
    listening = "\n".join(l for l in ports.splitlines() if "25565" in l) or \
                "NOT listening on 25565"
    return f"unit: {unit}\n\n{detail}\n\nports:\n{listening}"


@mcp.tool()
def minecraft_log(lines: int = 60, contains: str = "") -> str:
    """Tail the Minecraft log, optionally filtered (e.g. 'ERROR', a player name)."""
    lines = max(1, min(int(lines), 300))
    log = str(EUGH / "logs" / "latest.log")
    if contains:
        return _run(["bash", "-c",
                     f"grep -i -- {shlex.quote(contains)} {shlex.quote(log)} "
                     f"| tail -n {lines}"])[:12000]
    return _run(["tail", "-n", str(lines), log])[:12000]


@mcp.tool()
def minecraft_command(command: str) -> str:
    """Send a command to the Minecraft console.

    Examples: 'list', 'whitelist add Steve', 'gamemode creative Steve',
    'say Back in 5', 'save-all', 'reload'.
    Output does not return directly — follow with minecraft_log."""
    c = command.strip().lstrip("/")
    if not c:
        return "Refused: empty command."
    if c.lower().startswith("stop"):
        return "Refused: use service_control('eugh', 'stop') so the world saves cleanly."
    _run(["screen", "-p", "0", "-S", SCREEN_SESSION, "-X", "stuff", f"{c}\r"])
    return f"Sent: {c}\nUse minecraft_log to see the response."


if __name__ == "__main__":
    mcp.settings.host = "127.0.0.1"
    mcp.settings.port = 8787
    mcp.run(transport="streamable-http")
