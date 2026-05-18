# marimo-blender

Blender extension that runs a marimo notebook server inside Blender so cells can use `bpy`.

## marimo-pair

When running the marimo-pair discover script (`discover-servers.sh`), always use `dangerouslyDisableSandbox: true` in the Bash call. Claude Code's process sandbox blocks `kill -0` to other processes (like Blender), which causes the liveness check to fail and the registry entry to be deleted.

Example correct call:
```
Bash("bash .../discover-servers.sh", dangerouslyDisableSandbox=true)
```

The same applies to `execute-code.sh` — it connects to `http://127.0.0.1:PORT` which is blocked by the network sandbox.
