#!/usr/bin/env bash
set -euo pipefail

# Read-only unless --install is explicit.  This script never sources an env file.
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
ACTION=dry-run
WITH_CADDY=0
WITH_GATEWAY=0
SERVICE_USER=mxstudio
SERVICE_GROUP=mxstudio
PREFIX=/etc/mxstudio

usage() {
    printf '%s\n' "Usage: $0 [--dry-run|--install] [--with-caddy] [--with-gateway]" \
        "[--service-user USER] [--service-group GROUP]"
}
while (($#)); do
    case "$1" in
        --dry-run) ACTION=dry-run ;;
        --install) ACTION=install ;;
        --with-caddy) WITH_CADDY=1 ;;
        --with-gateway) WITH_GATEWAY=1 ;;
        --service-user) shift; SERVICE_USER=${1:?missing user} ;;
        --service-group) shift; SERVICE_GROUP=${1:?missing group} ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

case "$ROOT" in *[!A-Za-z0-9_./-]*) echo "Unsafe repository path" >&2; exit 2 ;; esac
if [[ "$SERVICE_USER" =~ [^A-Za-z0-9_.-] || "$SERVICE_GROUP" =~ [^A-Za-z0-9_.-] ]]; then
    echo "Unsafe service user or group" >&2
    exit 2
fi

if [[ "$ACTION" == dry-run ]]; then
    printf 'DRY RUN (no files, packages, users, services, or network changes)\n'
    printf 'repository=%q service_user=%q service_group=%q\n' "$ROOT" "$SERVICE_USER" "$SERVICE_GROUP"
    printf '%s\n' 'would preflight Ubuntu/Debian, pixi.toml, and protected populated env'
    printf 'would run absolute pixi install --locked as %s\n' "$SERVICE_USER"
    ((WITH_CADDY)) && printf '%s\n' 'would install Caddy and a separate mxstudio-nucleus-caddy.service'
    ((WITH_GATEWAY)) && printf '%s\n' 'would require DATABASE_URL/LITELLM_MASTER_KEY and use distro python3-venv'
    exit 0
fi

if ((EUID != 0)); then echo "--install must be run as root" >&2; exit 1; fi
if [[ ! -r /etc/os-release ]]; then echo "Cannot identify Linux distribution" >&2; exit 1; fi
. /etc/os-release
if [[ "${ID:-}" != ubuntu && "${ID:-}" != debian ]]; then
    echo "Unsupported distribution: ${ID:-unknown} (supported: Ubuntu/Debian)" >&2
    exit 1
fi
command -v systemctl >/dev/null || { echo "systemctl is required" >&2; exit 1; }
command -v runuser >/dev/null || { echo "runuser is required" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
[[ "$(uname -m)" == x86_64 ]] || { echo "This locked Linux environment supports x86_64, not Apple Silicon Linux" >&2; exit 1; }
if ((WITH_CADDY)); then
    if systemctl is-active --quiet caddy.service || systemctl is-enabled --quiet caddy.service; then
        echo "Existing host Caddy is active/enabled; integrate the template manually or disable it deliberately first" >&2
        exit 1
    fi
fi
PIXI_BIN=$(command -v pixi || true)
[[ -n "$PIXI_BIN" ]] || { echo "pixi is required before --install" >&2; exit 1; }
[[ -r "$ROOT/pixi.toml" ]] || { echo "Missing pixi.toml" >&2; exit 1; }

getent group "$SERVICE_GROUP" >/dev/null || groupadd --system "$SERVICE_GROUP"
id "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --gid "$SERVICE_GROUP" \
    --home-dir /var/lib/mxstudio --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
install -d -o root -g "$SERVICE_GROUP" -m 0750 "$PREFIX"
install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0750 /var/lib/mxstudio /var/cache/mxstudio

ENV_FILE=$PREFIX/nucleus.env
if [[ ! -f "$ENV_FILE" ]]; then
    echo "Create populated $ENV_FILE (mode 0640) before --install" >&2
    exit 1
fi
ENV_MODE=$(stat -c '%a' "$ENV_FILE")
(( 8#$ENV_MODE & 007 )) && { echo "$ENV_FILE is accessible by other users" >&2; exit 1; }
if ! runuser -u "$SERVICE_USER" -- test -r "$ENV_FILE"; then
    echo "$ENV_FILE is not readable by $SERVICE_USER" >&2
    exit 1
fi
PREFLIGHT_ARGS=()
((WITH_GATEWAY)) && PREFLIGHT_ARGS+=(--gateway)
python3 "$ROOT/deploy/nucleus/preflight.py" "$ENV_FILE" "${PREFLIGHT_ARGS[@]}"
runuser -u "$SERVICE_USER" -- test -x "$PIXI_BIN" || {
    echo "$SERVICE_USER cannot execute $PIXI_BIN; install Pixi at a shared executable path" >&2; exit 1;
}

# The service user must be able to use the existing project environment; never
# silently chown a developer checkout or overwrite an existing environment.
runuser -u "$SERVICE_USER" -- test -r "$ROOT/pixi.toml" || {
    echo "$SERVICE_USER cannot read/traverse repository" >&2; exit 1;
}
if [[ -e "$ROOT/.pixi" ]]; then
    runuser -u "$SERVICE_USER" -- test -w "$ROOT/.pixi" || {
        echo "$SERVICE_USER cannot write existing $ROOT/.pixi" >&2; exit 1;
    }
    if [[ -d "$ROOT/.pixi/envs/default" ]]; then
        runuser -u "$SERVICE_USER" -- test -w "$ROOT/.pixi/envs/default" || {
            echo "$SERVICE_USER cannot update the existing Pixi environment" >&2; exit 1;
        }
    fi
else
    install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0750 "$ROOT/.pixi"
fi
runuser -u "$SERVICE_USER" -- env HOME=/var/lib/mxstudio "$PIXI_BIN" install \
    --locked --manifest-path "$ROOT/pixi.toml"

install -d -m 0755 /etc/systemd/system
TEMP_DIR=$(mktemp -d)
trap 'rm -rf -- "$TEMP_DIR"' EXIT
render() {
    sed -e "s#@MXSTUDIO_ROOT@#$ROOT#g" -e "s#@NUCLEUS_USER@#$SERVICE_USER#g" \
        -e "s#@NUCLEUS_GROUP@#$SERVICE_GROUP#g" -e "s#@PIXI_BIN@#$PIXI_BIN#g" "$1" > "$2"
}
render "$ROOT/deploy/nucleus/mxstudio-nucleus.service.in" "$TEMP_DIR/mxstudio-nucleus.service"
if [[ ! -e /etc/systemd/system/mxstudio-nucleus.service ]]; then
    install -m 0644 "$TEMP_DIR/mxstudio-nucleus.service" /etc/systemd/system/mxstudio-nucleus.service
else
    echo "Preserving existing mxstudio-nucleus.service"
fi
if ((WITH_GATEWAY)); then
    apt-get update
    apt-get install -y python3-venv
    render "$ROOT/deploy/nucleus/litellm-nucleus.service.in" "$TEMP_DIR/litellm-nucleus.service"
    if [[ ! -e /etc/systemd/system/litellm-nucleus.service ]]; then
        install -m 0644 "$TEMP_DIR/litellm-nucleus.service" /etc/systemd/system/litellm-nucleus.service
    else
        echo "Preserving existing litellm-nucleus.service"
    fi
    runuser -u "$SERVICE_USER" -- /usr/bin/python3 -m venv /var/lib/mxstudio/litellm-venv
    runuser -u "$SERVICE_USER" -- /var/lib/mxstudio/litellm-venv/bin/pip install \
        --requirement "$ROOT/deploy/nucleus/litellm-requirements.txt"
fi

if ((WITH_CADDY)); then
    CADDY_WAS_INSTALLED=$(command -v caddy || true)
    apt-get update
    apt-get install -y caddy
    if [[ -z "$CADDY_WAS_INSTALLED" ]]; then
        # Debian packages start the stock server; only disable that fresh install.
        systemctl disable --now caddy.service
    fi
    CADDY_CONFIG="$PREFIX/Caddyfile"
    if [[ ! -e "$CADDY_CONFIG" ]]; then
        if ((WITH_GATEWAY)); then source_template="$ROOT/deploy/nucleus/Caddyfile.gateway"
        else source_template="$ROOT/deploy/nucleus/Caddyfile"; fi
        install -m 0644 "$source_template" "$CADDY_CONFIG"
    else
        echo "Preserving existing $CADDY_CONFIG"
    fi
    render "$ROOT/deploy/nucleus/mxstudio-nucleus-caddy.service.in" "$TEMP_DIR/mxstudio-nucleus-caddy.service"
    if [[ ! -e /etc/systemd/system/mxstudio-nucleus-caddy.service ]]; then
        install -m 0644 "$TEMP_DIR/mxstudio-nucleus-caddy.service" /etc/systemd/system/mxstudio-nucleus-caddy.service
    else
        echo "Preserving existing mxstudio-nucleus-caddy.service"
    fi
fi
systemctl daemon-reload
systemctl enable --now mxstudio-nucleus.service
python3 "$ROOT/deploy/nucleus/preflight.py" "$ENV_FILE" --probe
if ((WITH_GATEWAY)); then
    systemctl enable --now litellm-nucleus.service
    python3 "$ROOT/deploy/nucleus/preflight.py" "$ENV_FILE" --gateway --probe
fi
if ((WITH_CADDY)); then
    systemctl enable --now mxstudio-nucleus-caddy.service
    systemctl is-active --quiet mxstudio-nucleus-caddy.service
fi
printf 'APIs responsive. Inspect preparation job status before inference; trust the Caddy CA on each client. Configuration: %s\n' "$ENV_FILE"
