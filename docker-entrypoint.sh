#!/usr/bin/env bash
set -euo pipefail

resolve_docker_host() {
    python - <<'PY'
from __future__ import annotations

import socket
import struct
from pathlib import Path

try:
    socket.getaddrinfo("host.docker.internal", None)
except OSError:
    pass
else:
    print("host.docker.internal")
    raise SystemExit

route_table = Path("/proc/net/route")
if route_table.is_file():
    for line in route_table.read_text().splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 3 and fields[1] == "00000000":
            print(socket.inet_ntoa(struct.pack("<I", int(fields[2], 16))))
            raise SystemExit
raise SystemExit(1)
PY
}

replace_localhost() {
    local input="$1"
    local docker_host
    if ! docker_host="$(resolve_docker_host)"; then
        printf '%s\n' "$input"
        return 0
    fi
    printf '%s\n' "${input/localhost/$docker_host}"
}

processed_args=("$1")
shift
for argument in "$@"; do
    if [[ "$argument" == postgres*://*localhost* ]]; then
        printf '%s\n' "Remapping localhost in a database URL for container access." >&2
        processed_args+=("$(replace_localhost "$argument")")
    else
        processed_args+=("$argument")
    fi
done

if [[ "${DATABASE_URI:-}" == postgres*://*localhost* ]]; then
    printf '%s\n' "Remapping localhost in DATABASE_URI for container access." >&2
    DATABASE_URI="$(replace_localhost "$DATABASE_URI")"
    export DATABASE_URI
fi

has_sse=false
has_sse_host=false
for argument in "${processed_args[@]}"; do
    case "$argument" in
        --transport=sse|sse) has_sse=true ;;
        --sse-host|--sse-host=*) has_sse_host=true ;;
    esac
done
if [[ "$has_sse" == true && "$has_sse_host" == false ]]; then
    processed_args+=("--sse-host=0.0.0.0")
fi

exec "${processed_args[@]}"
