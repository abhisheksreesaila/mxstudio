#!/usr/bin/env bash
set -euo pipefail

# Read-only by default. --apply (or legacy --install) is the explicit approval boundary.
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
ACTION=dry-run
ACTION_OPTION=
WITH_CADDY=0
WITH_GATEWAY=0
SERVICE_USER=mxstudio
SERVICE_GROUP=mxstudio
SERVICE_GROUP_EXPLICIT=0
SERVICE_USER_CURRENT=0
SERVICE_USER_EXPLICIT=0
ENV_FILE=/etc/mxstudio/nucleus.env
PREFIX=/etc/mxstudio

usage() {
    printf '%s\n' "Usage: $0 [--dry-run|--apply] [--with-caddy] [--with-gateway]" \
        "[--env-file ABSOLUTE_PATH] [--service-user USER|--service-user-current] [--service-group GROUP]" \
        "--install is a backwards-compatible alias for --apply"
}
while (($#)); do
    case "$1" in
        --dry-run)
            [[ -z "$ACTION_OPTION" || "$ACTION_OPTION" == dry-run ]] || { echo "Choose only one of --dry-run or --apply" >&2; exit 2; }
            ACTION_OPTION=dry-run; ACTION=dry-run ;;
        --apply|--install)
            [[ -z "$ACTION_OPTION" || "$ACTION_OPTION" == apply ]] || { echo "Choose only one of --dry-run or --apply" >&2; exit 2; }
            ACTION_OPTION=apply; ACTION=apply ;;
        --with-caddy) WITH_CADDY=1 ;;
        --with-gateway) WITH_GATEWAY=1 ;;
        --env-file|--service-user|--service-group)
            option=$1
            shift
            (($#)) || { printf 'Missing value for %s\n' "$option" >&2; exit 2; }
            case "$option" in
                --env-file) ENV_FILE=$1 ;;
                --service-user)
                    ((SERVICE_USER_CURRENT == 0)) || { echo "Choose only one of --service-user or --service-user-current" >&2; exit 2; }
                    SERVICE_USER=$1; SERVICE_USER_EXPLICIT=1
                    ;;
                --service-group) SERVICE_GROUP=$1; SERVICE_GROUP_EXPLICIT=1 ;;
            esac
            ;;
        --service-user-current)
            ((SERVICE_USER_EXPLICIT == 0)) || { echo "Choose only one of --service-user or --service-user-current" >&2; exit 2; }
            SERVICE_USER_CURRENT=1
            ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

case "$ROOT" in *[!A-Za-z0-9_./-]*) echo "Unsafe repository path" >&2; exit 2 ;; esac
if ((SERVICE_USER_CURRENT)); then
    if ((EUID == 0)); then INVOKING_USER=${SUDO_USER:-}; else INVOKING_USER=$(id -un); fi
    [[ -n "$INVOKING_USER" && "$INVOKING_USER" != root ]] || { echo "--service-user-current requires a non-root invoking user (use sudo from that account)" >&2; exit 2; }
    SERVICE_USER=$INVOKING_USER
    if ((SERVICE_GROUP_EXPLICIT == 0)); then SERVICE_GROUP=$(id -gn "$SERVICE_USER"); fi
fi
if [[ ! "$SERVICE_USER" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]*$ || ! "$SERVICE_GROUP" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]*$ ]]; then
    echo "Unsafe service user or group" >&2
    exit 2
fi
if [[ "$ENV_FILE" != /* || "$ENV_FILE" =~ [^A-Za-z0-9_./-] || "$ENV_FILE" == *"/../"* || "$ENV_FILE" == */.. || "$ENV_FILE" == *"/./"* || "$ENV_FILE" == *//* ]]; then
    echo "--env-file must be a normalized absolute path using only letters, digits, and /._-" >&2
    exit 2
fi

PLAN_ARGS=(--platform-plan --env-file "$ENV_FILE" --service-user "$SERVICE_USER")
((SERVICE_USER_CURRENT)) && PLAN_ARGS+=(--service-user-current)
((WITH_CADDY)) && PLAN_ARGS+=(--with-caddy)
((WITH_GATEWAY)) && PLAN_ARGS+=(--gateway)
PLAN=$(python3 "$ROOT/deploy/nucleus/preflight.py" "${PLAN_ARGS[@]}") || exit 1
if ((SERVICE_USER_CURRENT)); then
    python3 "$ROOT/deploy/nucleus/preflight.py" --check-private-group --service-user "$SERVICE_USER" --service-group "$SERVICE_GROUP" || exit 1
fi
if [[ "$ACTION" == dry-run ]]; then
    printf 'DRY RUN (read-only; no files, packages, users, services, or network changes)\n'
    printf 'repository=%q service_user=%q service_group=%q env_file=%q\n' "$ROOT" "$SERVICE_USER" "$SERVICE_GROUP" "$ENV_FILE"
    printf 'plan=%s\n' "$PLAN"
    printf 'would verify the environment file, service-user access, repository/.pixi and HF cache ownership, hostname, listening ports, Caddy conflicts, and executable paths\n'
    printf 'approval boundary: rerun with --apply (legacy alias: --install); no installer confirmation prompt or implicit apply\n'
    exit 0
fi

if ((EUID != 0)); then echo "--apply must be run as root" >&2; exit 1; fi
PLAN_VALUES=$(python3 -c 'import json,sys; p=json.loads(sys.argv[1]); print("|".join([str(p["supported"]), str(p["family"]), str(p["package_manager"] or ""), " ".join(p["packages"]), str(p["caddy_available"])]))' "$PLAN")
IFS='|' read -r PLATFORM_SUPPORTED PLATFORM_FAMILY PACKAGE_MANAGER PACKAGES CADDY_AVAILABLE <<< "$PLAN_VALUES"
PACKAGES=${PACKAGES:-}
[[ "$PLATFORM_SUPPORTED" == True ]] || { echo "Unsupported host: $PLAN" >&2; exit 1; }
command -v "$PACKAGE_MANAGER" >/dev/null || { echo "$PACKAGE_MANAGER is required" >&2; exit 1; }
check_arch_upgrade_state() {
    local output status
    if output=$(pacman --query --upgrades 2>&1); then
        status=0
    else
        status=$?
    fi
    if ((status == 1)) && [[ -z "$output" ]]; then
        return 0
    fi
    if ((status != 0)); then
        echo "Could not inspect Arch upgrade state; refusing package changes" >&2
        [[ -z "$output" ]] || printf '%s\n' "$output" >&2
        return 1
    fi
    if [[ -n "$output" ]]; then
        printf 'Pending Arch updates detected; complete a full system upgrade separately before applying:\n%s\n' "$output" >&2
        return 1
    fi
}
if [[ "$PACKAGE_MANAGER" == pacman && -n "$PACKAGES" ]]; then
    check_arch_upgrade_state || exit 1
fi
[[ -r /etc/os-release ]] || { echo "Cannot identify Linux distribution" >&2; exit 1; }
command -v systemctl >/dev/null || { echo "systemctl is required" >&2; exit 1; }
command -v runuser >/dev/null || { echo "runuser is required" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
command -v getent >/dev/null || { echo "getent is required" >&2; exit 1; }
command -v id >/dev/null || { echo "id is required" >&2; exit 1; }
[[ "$(uname -m)" == x86_64 ]] || { echo "Only x86_64 is supported" >&2; exit 1; }
[[ -r "$ROOT/pixi.toml" ]] || { echo "Missing pixi.toml" >&2; exit 1; }
[[ -r "$ROOT/deploy/nucleus/mxstudio-nucleus.service.in" ]] || { echo "Missing Nucleus unit template" >&2; exit 1; }
if [[ -e "$PREFIX" || -L "$PREFIX" ]]; then
    [[ -d "$PREFIX" && ! -L "$PREFIX" && "$(stat -c '%U' "$PREFIX")" == root ]] || { echo "$PREFIX must be a root-owned real directory" >&2; exit 1; }
    PREFIX_MODE=$(stat -c '%a' "$PREFIX")
    (( 8#$PREFIX_MODE & 022 )) && { echo "$PREFIX must not be group- or world-writable" >&2; exit 1; }
fi
[[ -d /etc/systemd/system && ! -L /etc/systemd/system && "$(stat -c '%U' /etc/systemd/system)" == root ]] || { echo "/etc/systemd/system must be a root-owned real directory" >&2; exit 1; }
SYSTEMD_MODE=$(stat -c '%a' /etc/systemd/system)
(( 8#$SYSTEMD_MODE & 022 )) && { echo "/etc/systemd/system must not be group- or world-writable" >&2; exit 1; }
[[ -r "$ENV_FILE" && ! -L "$ENV_FILE" ]] || { echo "Create populated $ENV_FILE as a regular file before --apply" >&2; exit 1; }
python3 "$ROOT/deploy/nucleus/preflight.py" "$ENV_FILE" --check-env-path
ENV_MODE=$(stat -c '%a' "$ENV_FILE")
[[ "$ENV_MODE" == 640 ]] || { echo "$ENV_FILE must have mode 0640" >&2; exit 1; }
[[ "$(stat -c '%U:%G' "$ENV_FILE")" == "root:$SERVICE_GROUP" ]] || { echo "$ENV_FILE must be owned by root:$SERVICE_GROUP" >&2; exit 1; }
PREFLIGHT_ARGS=("$ENV_FILE")
((WITH_GATEWAY)) && PREFLIGHT_ARGS+=(--gateway)
python3 "$ROOT/deploy/nucleus/preflight.py" "${PREFLIGHT_ARGS[@]}"
mapfile -t IDENTITY_FIELDS < <(python3 "$ROOT/deploy/nucleus/preflight.py" "$ENV_FILE" --deployment-identity)
MXSTUDIO_HOME=${IDENTITY_FIELDS[0]}
HF_HOME=${IDENTITY_FIELDS[1]}
NUCLEUS_MODEL_ID=${IDENTITY_FIELDS[2]}
NUCLEUS_MODEL_REVISION=${IDENTITY_FIELDS[3]}
LITELLM_VENV=$MXSTUDIO_HOME/.litellm-venv

find_pixi() {
    local candidate
    candidate=$(command -v pixi || true)
    if [[ -n "$candidate" && -x "$candidate" ]]; then printf '%s\n' "$(readlink -f "$candidate")"; return 0; fi
    for candidate in /usr/local/bin/pixi /usr/bin/pixi /home/*/.pixi/bin/pixi; do
        if [[ -x "$candidate" ]]; then printf '%s\n' "$(readlink -f "$candidate")"; return 0; fi
    done
    return 1
}
PIXI_BIN=$(find_pixi) || { echo "Pixi is required; install it at a shared executable path before --apply" >&2; exit 1; }
if getent passwd "$SERVICE_USER" >/dev/null && ((SERVICE_USER_CURRENT == 0)); then
    EXISTING_SHELL=$(getent passwd "$SERVICE_USER" | cut -d: -f7)
    [[ "$EXISTING_SHELL" == */nologin ]] || { echo "Existing $SERVICE_USER is not a non-login service account; use --service-user-current only when intentionally sharing the invoking account" >&2; exit 1; }
fi
if ((SERVICE_USER_CURRENT)); then
    python3 "$ROOT/deploy/nucleus/preflight.py" --check-private-group --service-user "$SERVICE_USER" --service-group "$SERVICE_GROUP"
fi
# Refuse conflicts before any host mutation. Integrate Caddy manually if a host
# instance or a pre-existing managed config/unit already exists.
CADDY_PREEXISTED=
if ((WITH_CADDY)); then
    if [[ -x /usr/bin/caddy && "$CADDY_AVAILABLE" != True ]]; then
        echo "Unmanaged /usr/bin/caddy exists; preserve it and install the distro package or integrate manually" >&2
        exit 1
    fi
    [[ "$CADDY_AVAILABLE" == True ]] && CADDY_PREEXISTED=/usr/bin/caddy
fi
if ((WITH_CADDY)); then
    if [[ -z "$CADDY_PREEXISTED" ]]; then
        [[ ! -e /etc/systemd/system/caddy.service && ! -L /etc/systemd/system/caddy.service ]] || { echo "Existing caddy.service override must be preserved" >&2; exit 1; }
        caddy_fragment=$(systemctl show -p FragmentPath --value caddy.service 2>/dev/null || true)
        [[ -z "$caddy_fragment" ]] || { echo "Existing caddy.service is installed; preserve it and integrate manually" >&2; exit 1; }
    fi
    if systemctl is-active --quiet caddy.service || systemctl is-enabled --quiet caddy.service; then
        echo "Host caddy.service is active or enabled; preserve it and integrate the reviewed route block manually" >&2
        exit 1
    fi
    [[ ! -e "$PREFIX/Caddyfile" && ! -L "$PREFIX/Caddyfile" ]] || { echo "Preserving existing $PREFIX/Caddyfile; review and integrate Caddy manually" >&2; exit 1; }
fi
for unit in mxstudio-nucleus.service; do
    fragment=$(systemctl show -p FragmentPath --value "$unit" 2>/dev/null || true)
    [[ -z "$fragment" && ! -e "/etc/systemd/system/$unit" && ! -L "/etc/systemd/system/$unit" ]] || { echo "Existing $unit conflicts; inspect it and resolve manually" >&2; exit 1; }
done
if ((WITH_GATEWAY)); then
    [[ ! -e "$LITELLM_VENV" && ! -L "$LITELLM_VENV" ]] || { echo "Existing gateway environment found; preserve it and inspect manually" >&2; exit 1; }
    fragment=$(systemctl show -p FragmentPath --value litellm-nucleus.service 2>/dev/null || true)
    [[ -z "$fragment" && ! -e /etc/systemd/system/litellm-nucleus.service && ! -L /etc/systemd/system/litellm-nucleus.service ]] || { echo "Existing litellm-nucleus.service conflicts" >&2; exit 1; }
fi
if ((WITH_CADDY)); then
    fragment=$(systemctl show -p FragmentPath --value mxstudio-nucleus-caddy.service 2>/dev/null || true)
    [[ -z "$fragment" && ! -e /etc/systemd/system/mxstudio-nucleus-caddy.service && ! -L /etc/systemd/system/mxstudio-nucleus-caddy.service ]] || { echo "Existing mxstudio-nucleus-caddy.service conflicts" >&2; exit 1; }
fi
PORTS=(8080)
((WITH_GATEWAY)) && PORTS+=(4000)
((WITH_CADDY)) && PORTS+=(443)
python3 "$ROOT/deploy/nucleus/preflight.py" --check-ports "${PORTS[@]}"

# Never repair developer checkout or model-cache ownership implicitly.
ensure_directory() {
    local path=$1 owner=$2 group=$3 mode=$4
    if [[ -L "$path" ]]; then
        echo "$path must not be a symlink" >&2; return 1
    elif [[ -e "$path" ]]; then
        [[ -d "$path" ]] || { echo "$path exists but is not a directory" >&2; return 1; }
        [[ "$(stat -c '%U:%G' "$path")" == "$owner:$group" ]] || {
            echo "$path must already be owned by $owner:$group; refusing to chown existing data" >&2; return 1;
        }
        runuser -u "$SERVICE_USER" -- test -r "$path" && runuser -u "$SERVICE_USER" -- test -w "$path" && runuser -u "$SERVICE_USER" -- test -x "$path" || {
            echo "$path must be readable, writable, and traversable by $SERVICE_USER" >&2; return 1;
        }
    else
        install -d -o "$owner" -g "$group" -m "$mode" "$path"
        printf 'created-directory=%s\n' "$path" >> "$MANIFEST"
    fi
}

# Keep a root-only per-run record before making persistent account/cache changes.
if [[ ! -d "$PREFIX" ]]; then install -d -o root -g root -m 0755 "$PREFIX"; fi
RUN_ID=$(date -u +%Y%m%dT%H%M%SZ)-$$
MANIFEST_DIR=$PREFIX/install-manifests
if [[ -L "$MANIFEST_DIR" ]]; then echo "$MANIFEST_DIR must not be a symlink" >&2; exit 1; fi
if [[ ! -e "$MANIFEST_DIR" ]]; then install -d -o root -g root -m 0700 "$MANIFEST_DIR"; fi
[[ -d "$MANIFEST_DIR" && "$(stat -c '%U:%G' "$MANIFEST_DIR")" == "root:root" ]] || { echo "$MANIFEST_DIR must be root:root" >&2; exit 1; }
(( 8#$(stat -c '%a' "$MANIFEST_DIR") & 077 )) && { echo "$MANIFEST_DIR must not be accessible to group/other users" >&2; exit 1; }
MANIFEST=$MANIFEST_DIR/$RUN_ID.txt
[[ ! -e "$MANIFEST" && ! -L "$MANIFEST" ]] || { echo "Installer manifest path already exists" >&2; exit 1; }
printf 'run=%s\nstatus=applying\nrepository=%s\nprofile=caddy:%s,gateway:%s\n' "$RUN_ID" "$ROOT" "$WITH_CADDY" "$WITH_GATEWAY" > "$MANIFEST"
chmod 0600 "$MANIFEST"

OWNED_FILES=()
OWNED_DIRS=()
STARTED_SERVICES=()
ENABLED_SERVICES=()
PACKAGE_CADDY_OWNED=0
if ((WITH_CADDY)) && [[ -z "$CADDY_PREEXISTED" ]]; then PACKAGE_CADDY_OWNED=1; fi
rollback() {
    local status=$?
    trap - EXIT
    if ((status != 0)); then
        local i
        for ((i=${#STARTED_SERVICES[@]}-1; i>=0; i--)); do systemctl stop "${STARTED_SERVICES[i]}" >/dev/null 2>&1 || true; done
        for ((i=${#ENABLED_SERVICES[@]}-1; i>=0; i--)); do systemctl disable "${ENABLED_SERVICES[i]}" >/dev/null 2>&1 || true; done
        if ((PACKAGE_CADDY_OWNED)); then systemctl disable --now caddy.service >/dev/null 2>&1 || true; fi
        for ((i=${#OWNED_FILES[@]}-1; i>=0; i--)); do rm -f -- "${OWNED_FILES[i]}"; done
        for ((i=${#OWNED_DIRS[@]}-1; i>=0; i--)); do rm -rf -- "${OWNED_DIRS[i]}"; done
        systemctl daemon-reload >/dev/null 2>&1 || true
        if [[ -n "${MANIFEST:-}" && -f "$MANIFEST" ]]; then
            printf 'status=rolled-back\n' >> "$MANIFEST"
            for path in "${OWNED_FILES[@]}"; do printf 'removed=%s\n' "$path" >> "$MANIFEST"; done
            for path in "${OWNED_DIRS[@]}"; do printf 'removed-directory=%s\n' "$path" >> "$MANIFEST"; done
            for service in "${STARTED_SERVICES[@]}"; do printf 'stopped=%s\n' "$service" >> "$MANIFEST"; done
            for service in "${ENABLED_SERVICES[@]}"; do printf 'disabled=%s\n' "$service" >> "$MANIFEST"; done
        fi
        echo "Activation failed; newly created service files and started/enabled units were rolled back. Packages, accounts, cache, and model data were preserved." >&2
    fi
    exit "$status"
}
trap rollback EXIT

if ((SERVICE_USER_CURRENT == 0)); then
    if ! getent group "$SERVICE_GROUP" >/dev/null; then
        groupadd --system "$SERVICE_GROUP"
        printf 'created-group=%s\n' "$SERVICE_GROUP" >> "$MANIFEST"
    fi
    if ! id "$SERVICE_USER" >/dev/null 2>&1; then
        useradd --system --gid "$SERVICE_GROUP" --home-dir /var/lib/mxstudio --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
        printf 'created-user=%s\n' "$SERVICE_USER" >> "$MANIFEST"
    fi
fi
SERVICE_HOME=$(getent passwd "$SERVICE_USER" | cut -d: -f6)
[[ -n "$SERVICE_HOME" ]] || { echo "$SERVICE_USER needs a valid home directory for Pixi and Caddy state" >&2; exit 1; }
runuser -u "$SERVICE_USER" -- test -x "$PIXI_BIN" || { echo "$SERVICE_USER cannot execute $PIXI_BIN; use a shared executable path" >&2; exit 1; }
runuser -u "$SERVICE_USER" -- test -r "$ROOT/pixi.toml" || { echo "$SERVICE_USER cannot read/traverse repository" >&2; exit 1; }
id -nG "$SERVICE_USER" | tr ' ' '\n' | grep -Fxq "$SERVICE_GROUP" || { echo "$SERVICE_USER must belong to $SERVICE_GROUP" >&2; exit 1; }
runuser -u "$SERVICE_USER" -- test -r "$ENV_FILE" || { echo "$ENV_FILE is not readable by $SERVICE_USER; set root:$SERVICE_GROUP mode 0640" >&2; exit 1; }
if ((SERVICE_USER_CURRENT == 0)) && [[ "$SERVICE_USER" == mxstudio && "$MXSTUDIO_HOME" == /var/lib/mxstudio && "$HF_HOME" == /var/cache/mxstudio/huggingface ]]; then
    ensure_directory "$MXSTUDIO_HOME" "$SERVICE_USER" "$SERVICE_GROUP" 0750
    ensure_directory /var/cache/mxstudio "$SERVICE_USER" "$SERVICE_GROUP" 0750
    ensure_directory "$HF_HOME" "$SERVICE_USER" "$SERVICE_GROUP" 0750
fi
python3 "$ROOT/deploy/nucleus/preflight.py" "$ENV_FILE" --installation-values --service-user "$SERVICE_USER" --service-group "$SERVICE_GROUP"
printf 'service_user=%s\nmxstudio_home=%s\nhf_home=%s\nmodel_id=%s\nmodel_revision=%s\n' \
    "$SERVICE_USER" "$MXSTUDIO_HOME" "$HF_HOME" "$NUCLEUS_MODEL_ID" "$NUCLEUS_MODEL_REVISION" >> "$MANIFEST"
if ((WITH_CADDY)); then
    ensure_directory /var/lib/mxstudio-caddy "$SERVICE_USER" "$SERVICE_GROUP" 0750
    ensure_directory /var/cache/mxstudio-caddy "$SERVICE_USER" "$SERVICE_GROUP" 0750
fi

if [[ -L "$ROOT/.pixi" ]]; then
    echo "Existing $ROOT/.pixi must not be a symlink" >&2; exit 1
elif [[ -e "$ROOT/.pixi" ]]; then
    runuser -u "$SERVICE_USER" -- test -r "$ROOT/.pixi" && runuser -u "$SERVICE_USER" -- test -w "$ROOT/.pixi" && runuser -u "$SERVICE_USER" -- test -x "$ROOT/.pixi" || {
        echo "$SERVICE_USER needs read/write/traverse access to existing $ROOT/.pixi; refusing to change ownership" >&2; exit 1;
    }
    if [[ -d "$ROOT/.pixi/envs/default" ]]; then
        runuser -u "$SERVICE_USER" -- test -w "$ROOT/.pixi/envs/default" || { echo "The existing Pixi environment is not writable by $SERVICE_USER" >&2; exit 1; }
    fi
else
    install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0750 "$ROOT/.pixi"
    printf 'created-directory=%s\n' "$ROOT/.pixi" >> "$MANIFEST"
fi


# Install only named distro packages. Debian package lists and pacman sync DBs
# are not refreshed; no package-manager system upgrade is run.
read -r -a PACKAGE_LIST <<< "$PACKAGES"
if ((${#PACKAGE_LIST[@]})); then
    if [[ "$PACKAGE_MANAGER" == apt-get ]]; then
        DEBIAN_FRONTEND=noninteractive apt-get install --yes --no-install-recommends "${PACKAGE_LIST[@]}"
    else
        pacman --sync --needed --noconfirm "${PACKAGE_LIST[@]}"
    fi
fi
runuser -u "$SERVICE_USER" -- env HOME="$SERVICE_HOME" "$PIXI_BIN" install --locked --manifest-path "$ROOT/pixi.toml"
if ((WITH_CADDY)); then
    [[ -x /usr/bin/caddy ]] || { echo "Caddy package did not provide /usr/bin/caddy" >&2; exit 1; }
    [[ ! -e /etc/systemd/system/caddy.service && ! -L /etc/systemd/system/caddy.service ]] || { echo "Caddy system unit override appeared; preserve it and integrate manually" >&2; exit 1; }
    if ((PACKAGE_CADDY_OWNED)); then
        # A newly installed distro unit is package-owned; stop/disable only this
        # fresh default instance so it cannot race the separate managed ingress.
        systemctl disable --now caddy.service >/dev/null 2>&1 || true
        systemctl is-active --quiet caddy.service && { echo "Could not stop the new package Caddy service safely" >&2; exit 1; }
        printf 'disabled-package-service=caddy.service\n' >> "$MANIFEST"
    elif systemctl is-active --quiet caddy.service; then
        echo "Existing host Caddy became active; leaving it untouched and aborting dedicated ingress" >&2
        exit 1
    fi
    PACKAGE_CADDY_OWNED=0
fi

render() {
    sed -e "s#@MXSTUDIO_ROOT@#$ROOT#g" -e "s#@NUCLEUS_USER@#$SERVICE_USER#g" \
        -e "s#@NUCLEUS_GROUP@#$SERVICE_GROUP#g" -e "s#@PIXI_BIN@#$PIXI_BIN#g" \
        -e "s#@NUCLEUS_ENV_FILE@#$ENV_FILE#g" -e "s#@MXSTUDIO_HOME@#$MXSTUDIO_HOME#g" \
        -e "s#@LITELLM_VENV@#$LITELLM_VENV#g" "$1" > "$2"
}
install_owned() {
    local source=$1 destination=$2 mode=$3
    [[ ! -e "$destination" && ! -L "$destination" ]] || { echo "Refusing to overwrite existing $destination" >&2; return 1; }
    OWNED_FILES+=("$destination")
    if [[ "$source" == rendered:* ]]; then render "${source#rendered:}" "$destination"; chmod "$mode" "$destination"
    else install -m "$mode" "$source" "$destination"; fi
    printf 'created=%s\n' "$destination" >> "$MANIFEST"
}
install -d -m 0755 /etc/systemd/system
install_owned "rendered:$ROOT/deploy/nucleus/mxstudio-nucleus.service.in" /etc/systemd/system/mxstudio-nucleus.service 0644
if ((WITH_GATEWAY)); then
    install_owned "rendered:$ROOT/deploy/nucleus/litellm-nucleus.service.in" /etc/systemd/system/litellm-nucleus.service 0644
    OWNED_DIRS+=("$LITELLM_VENV")
    printf 'created-directory=%s\n' "$LITELLM_VENV" >> "$MANIFEST"
    runuser -u "$SERVICE_USER" -- /usr/bin/python3 -m venv "$LITELLM_VENV"
    runuser -u "$SERVICE_USER" -- "$LITELLM_VENV/bin/pip" install --requirement "$ROOT/deploy/nucleus/litellm-requirements.txt"
fi
if ((WITH_CADDY)); then
    if ((WITH_GATEWAY)); then CADDY_TEMPLATE="$ROOT/deploy/nucleus/Caddyfile.gateway"; else CADDY_TEMPLATE="$ROOT/deploy/nucleus/Caddyfile"; fi
    install_owned "$CADDY_TEMPLATE" "$PREFIX/Caddyfile" 0644
    install_owned "rendered:$ROOT/deploy/nucleus/mxstudio-nucleus-caddy.service.in" /etc/systemd/system/mxstudio-nucleus-caddy.service 0644
    HOSTNAME=$(PYTHONPATH="$ROOT" python3 -c 'from deploy.nucleus.preflight import read_environment; from pathlib import Path; import sys; print(read_environment(Path(sys.argv[1])).get("NUCLEUS_HOSTNAME", "api.mxstudio.local"))' "$ENV_FILE")
    runuser -u "$SERVICE_USER" -- env HOME="$SERVICE_HOME" XDG_DATA_HOME=/var/lib/mxstudio-caddy \
        XDG_CONFIG_HOME=/var/cache/mxstudio-caddy NUCLEUS_HOSTNAME="$HOSTNAME" \
        /usr/bin/caddy validate --config "$PREFIX/Caddyfile" --adapter caddyfile
fi
systemctl daemon-reload

activate() {
    local service=$1
    if ! systemctl is-enabled --quiet "$service"; then
        systemctl enable "$service"
        ENABLED_SERVICES+=("$service")
        printf 'enabled=%s\n' "$service" >> "$MANIFEST"
    fi
    if ! systemctl is-active --quiet "$service"; then
        STARTED_SERVICES+=("$service")
        printf 'started=%s\n' "$service" >> "$MANIFEST"
        systemctl start "$service"
    fi
}
activate mxstudio-nucleus.service
python3 "$ROOT/deploy/nucleus/preflight.py" "$ENV_FILE" --probe
if ((WITH_GATEWAY)); then
    activate litellm-nucleus.service
    python3 "$ROOT/deploy/nucleus/preflight.py" "$ENV_FILE" --gateway --probe
fi
if ((WITH_CADDY)); then
    activate mxstudio-nucleus-caddy.service
    systemctl is-active --quiet mxstudio-nucleus-caddy.service
fi
printf 'status=activated\n' >> "$MANIFEST"
printf 'APIs responsive. This does not prove generated inference or second-client HTTPS trust. Inspect preparation status and enroll each client in the Caddy CA. Manifest: %s\n' "$MANIFEST"
