"""Validate the Nucleus EnvironmentFile and describe safe Linux install plans."""
from __future__ import annotations

import argparse
import errno
import json
import os
import platform
import grp
import pwd
import re
import socket
import stat
import subprocess
import time
from pathlib import Path
from urllib import error, request


def read_environment(path: Path) -> dict[str, str]:
    if stat.S_IMODE(path.stat().st_mode) & 0o007:
        raise ValueError("environment file must not be accessible to other users")
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError("expected one KEY=value assignment per line")
        value = value.strip()
        if value.startswith(('"', "'")) and value.endswith(value[0]):
            value = value[1:-1]
        if any(char.isspace() or char in "\\\"'" for char in value):
            raise ValueError("use single-line values without whitespace or escapes; URL-encode database passwords")
        if key in values:
            raise ValueError(f"duplicate environment setting: {key}")
        values[key] = value
    return values


def validate(values: dict[str, str], gateway: bool = False) -> None:
    required = [
        "MXSTUDIO_NUCLEUS_API_KEY",
        "NUCLEUS_MODEL_ID",
        "NUCLEUS_MODEL_REVISION",
        "MXSTUDIO_HOME",
        "HF_HOME",
    ]
    if gateway:
        required += ["DATABASE_URL", "LITELLM_MASTER_KEY"]
    for key in required:
        if not values.get(key):
            raise ValueError(f"missing populated {key}")
    if gateway and not values["DATABASE_URL"].startswith(("postgresql://", "postgres://")):
        raise ValueError("DATABASE_URL must refer to explicitly provisioned PostgreSQL")
    model_id = values["NUCLEUS_MODEL_ID"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*", model_id):
        raise ValueError("NUCLEUS_MODEL_ID must be a canonical namespace/repository ID")
    if not re.fullmatch(r"[0-9a-f]{40}", values["NUCLEUS_MODEL_REVISION"], re.IGNORECASE):
        raise ValueError("NUCLEUS_MODEL_REVISION must be a 40-character immutable commit hash")
    for key in ("MXSTUDIO_HOME", "HF_HOME"):
        value = values[key]
        path = Path(value)
        if (
            not path.is_absolute()
            or path.as_posix() != value
            or any(part in {".", ".."} for part in path.parts)
            or value == "/"
            or not re.fullmatch(r"/[A-Za-z0-9_./-]+", value)
        ):
            raise ValueError(f"{key} must be a normalized absolute directory path")
    hostname = values.get("NUCLEUS_HOSTNAME", "api.mxstudio.local")
    labels = hostname.rstrip(".").split(".")
    if len(hostname) > 253 or any(
        not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in labels
    ):
        raise ValueError("NUCLEUS_HOSTNAME must contain valid DNS labels")


def inspect_installation(path: Path, service_user: str) -> dict[str, str]:
    """Return a non-secret deployment identity after checking existing owned paths."""
    values = read_environment(path)
    validate(values)
    try:
        account = pwd.getpwnam(service_user)
    except KeyError as exc:
        raise ValueError(f"service user does not exist: {service_user}") from exc
    for key in ("MXSTUDIO_HOME", "HF_HOME"):
        directory = Path(values[key])
        if directory.is_symlink() or not directory.is_dir() or directory.resolve(strict=True) != directory:
            raise ValueError(f"{key} must be an existing real directory: {directory}")
        info = directory.stat()
        if info.st_uid != account.pw_uid:
            raise ValueError(f"{key} is not owned by service user {service_user}: {directory}")
        if stat.S_IMODE(info.st_mode) & 0o700 != 0o700 or stat.S_IMODE(info.st_mode) & 0o002:
            raise ValueError(f"{key} must be writable and traversable by service user {service_user}: {directory}")
        if os.geteuid() == account.pw_uid:
            if not os.access(directory, os.R_OK | os.W_OK | os.X_OK):
                raise ValueError(f"service user cannot access {key}: {directory}")
        elif os.geteuid() == 0:
            for permission in ("-r", "-w", "-x"):
                result = subprocess.run(
                    ["runuser", "-u", service_user, "--", "test", permission, str(directory)],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
                if result.returncode != 0:
                    raise ValueError(f"service user cannot access {key}: {directory}")
        else:
            raise ValueError("installation identity must be inspected by the service user or root")
    return {
        "service_user": service_user,
        "mxstudio_home": values["MXSTUDIO_HOME"],
        "hf_home": values["HF_HOME"],
        "nucleus_model_id": values["NUCLEUS_MODEL_ID"],
        "nucleus_model_revision": values["NUCLEUS_MODEL_REVISION"],
    }


def verify_installed_identity(path: Path, service_user: str, service_group: str) -> dict[str, str]:
    """Verify the effective Nucleus unit still points at the approved identity."""
    validate_env_file(path, service_group)
    validate_private_group(service_user, service_group)
    identity = inspect_installation(path, service_user)
    unit = "/etc/systemd/system/mxstudio-nucleus.service"

    def property_value(name: str) -> str:
        result = subprocess.run(
            ["systemctl", "show", f"--property={name}", "--value", "mxstudio-nucleus.service"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        value = result.stdout.strip()
        if result.returncode != 0 or not value:
            raise ValueError(f"could not inspect installed Nucleus service property {name}")
        return value

    fragment = property_value("FragmentPath")
    user = property_value("User")
    group = property_value("Group")
    environment_files = property_value("EnvironmentFiles")
    if fragment != unit or user != service_user or group != service_group or str(path) not in environment_files:
        raise ValueError("installed Nucleus service identity does not match the approved user, group, or EnvironmentFile")
    identity.update({
        "unit_fragment": fragment,
        "unit_user": user,
        "unit_group": group,
        "unit_env_file": str(path),
        "unit_state": property_value("ActiveState"),
    })
    return identity


def validate_protected_env_path(path: Path) -> None:
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError("environment file must be an existing absolute regular file")
    if path.resolve(strict=True) != path:
        raise ValueError("environment file path must not traverse symlinks")
    for directory in (path.parent, *path.parent.parents):
        info = directory.stat()
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022:
            raise ValueError("environment file and its parent path must be root-owned and not group/world-writable")


def validate_env_file(path: Path, service_group: str) -> None:
    validate_protected_env_path(path)
    info = path.stat()
    try:
        group_name = grp.getgrgid(info.st_gid).gr_name
    except KeyError as exc:
        raise ValueError("environment file group is unknown") from exc
    if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o640 or group_name != service_group:
        raise ValueError("environment file must be root:service-group mode 0640")


def validate_private_group(service_user: str, service_group: str) -> None:
    group = grp.getgrnam(service_group)
    others = {member for member in group.gr_mem if member not in {service_user, "root"}}
    others.update(
        account.pw_name
        for account in pwd.getpwall()
        if account.pw_gid == group.gr_gid and account.pw_name not in {service_user, "root"}
    )
    if others:
        raise ValueError(f"service group {service_group} is shared with other accounts")


def read_os_release(path: Path = Path("/etc/os-release")) -> dict[str, str]:
    """Read only the simple ID fields; never execute/shell-source os-release."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key in {"ID", "ID_LIKE"}:
            value = value.strip().strip('"').strip("'")
            values[key] = value.lower()
    return values


def caddy_is_distro_managed(family: str) -> bool:
    binary = Path("/usr/bin/caddy")
    if not binary.is_file():
        return False
    if family == "debian":
        command = ["dpkg-query", "--search", str(binary)]
    elif family == "arch":
        command = ["pacman", "--query", "--owns", "--quiet", str(binary)]
    else:
        return False
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    owner = result.stdout.strip().splitlines()[0].split(":", 1)[0] if result.stdout.strip() else ""
    return result.returncode == 0 and owner == "caddy"


def installation_plan(
    distro: str,
    machine: str,
    *,
    with_caddy: bool = False,
    with_gateway: bool = False,
    caddy_available: bool = False,
    env_file: str = "/etc/mxstudio/nucleus.env",
    service_user: str = "mxstudio",
    service_user_current: bool = False,
    environment_identity: dict[str, str] | None = None,
) -> dict[str, object]:
    distro = distro.lower()
    if distro in {"debian", "ubuntu"}:
        family, package_manager = "debian", "apt-get"
        packages = []
        if with_caddy and not caddy_available:
            packages.append("caddy")
        if with_gateway:
            packages.append("python3-venv")
        package_command = ["apt-get", "install", "--yes", "--no-install-recommends", *packages] if packages else []
    elif distro in {"arch", "omarchy"}:
        family, package_manager = "arch", "pacman"
        packages = []
        if with_caddy and not caddy_available:
            packages.append("caddy")
        package_command = ["pacman", "--sync", "--needed", "--noconfirm", *packages] if packages else []
    else:
        family, package_manager, packages, package_command = "unsupported", None, [], []
    supported = machine == "x86_64" and family != "unsupported"
    services = ["mxstudio-nucleus.service"]
    if with_gateway:
        services.append("litellm-nucleus.service")
    if with_caddy:
        services.append("mxstudio-nucleus-caddy.service")
        if not caddy_available:
            services.append("caddy.service (new package unit is disabled)")
    return {
        "distribution": distro or "unknown",
        "family": family,
        "architecture": machine,
        "supported": supported,
        "caddy_available": caddy_available,
        "service_user": service_user,
        "service_user_current": service_user_current,
        "environment_identity": environment_identity or {"status": "needs_setup"},
        "package_manager": package_manager,
        "packages": packages,
        "package_command": package_command,
        "services": services,
        "directories": [
            "MXSTUDIO_HOME and HF_HOME (owned by selected service user; current-user mode requires existing paths)",
            "repository/.pixi (service-owned only when absent; existing ownership preserved)",
            "/etc/mxstudio/install-manifests (root-only, create only if absent)",
            *(["/var/lib/mxstudio-caddy and /var/cache/mxstudio-caddy (service-owned; create only if absent)"] if with_caddy else []),
        ],
        "files": [
            f"{env_file} (required, preserved)",
            "/etc/mxstudio/install-manifests/<run-id>.txt (root-only)",
            "/etc/systemd/system/mxstudio-nucleus.service (create only if absent)",
            *(["/etc/mxstudio/Caddyfile (create only if absent)", "/etc/systemd/system/mxstudio-nucleus-caddy.service (create only if absent)"] if with_caddy else []),
            *(["/etc/systemd/system/litellm-nucleus.service (create only if absent)"] if with_gateway else []),
        ],
        "arch_upgrade_policy": ("requires a current sync database and zero pending pacman upgrades; never refreshes databases or runs a system upgrade" if packages else "no package changes planned") if family == "arch" else None,
    }


def check_ports(ports: list[int]) -> None:
    """Fail if a service port cannot be bound on either address family."""
    for port in ports:
        for family, address in ((socket.AF_INET, "0.0.0.0"), (socket.AF_INET6, "::")):
            try:
                sock = socket.socket(family, socket.SOCK_STREAM)
            except OSError as exc:
                if family == socket.AF_INET6 and exc.errno in {errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT}:
                    continue
                raise ValueError(f"port {port} is unavailable") from exc
            try:
                if family == socket.AF_INET6:
                    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                sock.bind((address, port))
            except OSError as exc:
                if family == socket.AF_INET6 and exc.errno in {errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT, errno.EADDRNOTAVAIL}:
                    continue
                raise ValueError(f"port {port} is already in use or unavailable") from exc
            finally:
                sock.close()


def probe(values: dict[str, str], gateway: bool = False) -> None:
    port, key = (4000, values["LITELLM_MASTER_KEY"]) if gateway else (8080, values["MXSTUDIO_NUCLEUS_API_KEY"])
    req = request.Request(f"http://127.0.0.1:{port}/v1/models", headers={"Authorization": "Bearer " + key})
    opener = request.build_opener(request.ProxyHandler({}))
    for _ in range(30):
        try:
            with opener.open(req, timeout=2) as response:
                if response.status == 200:
                    return
        except (error.URLError, TimeoutError, OSError):
            pass
        time.sleep(1)
    raise RuntimeError("inference API did not become responsive; inspect systemd status locally")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("environment", type=Path, nargs="?")
    parser.add_argument("--gateway", action="store_true")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--platform-plan", action="store_true")
    parser.add_argument("--env-file", type=Path, default=Path("/etc/mxstudio/nucleus.env"))
    parser.add_argument("--service-user", default="mxstudio")
    parser.add_argument("--service-user-current", action="store_true")
    parser.add_argument("--installation-values", action="store_true")
    parser.add_argument("--verify-installed-identity", action="store_true")
    parser.add_argument("--deployment-identity", action="store_true")
    parser.add_argument("--service-group")
    parser.add_argument("--check-env-path", action="store_true")
    parser.add_argument("--check-private-group", action="store_true")
    parser.add_argument("--with-caddy", action="store_true")
    parser.add_argument("--check-ports", type=int, nargs="*")
    args = parser.parse_args()
    try:
        if args.platform_plan:
            os_release = read_os_release()
            ids = [os_release.get("ID", ""), *os_release.get("ID_LIKE", "").split()]
            distro = next((item for item in ids if item in {"debian", "ubuntu", "arch", "omarchy"}), os_release.get("ID", "unknown"))
            machine = platform.machine()
            base_plan = installation_plan(distro, machine, with_caddy=args.with_caddy, with_gateway=args.gateway, env_file=str(args.env_file))
            caddy_available = args.with_caddy and caddy_is_distro_managed(str(base_plan["family"]))
            identity: dict[str, str] = {"status": "needs_setup"}
            if args.env_file.is_file() and not args.env_file.is_symlink():
                try:
                    values = read_environment(args.env_file)
                    identity = {key.lower(): values[key] for key in ("MXSTUDIO_HOME", "HF_HOME", "NUCLEUS_MODEL_ID", "NUCLEUS_MODEL_REVISION") if values.get(key)}
                    try:
                        validate(values, args.gateway)
                        identity["status"] = "ready" if len(identity) == 4 else "needs_setup"
                    except ValueError:
                        identity["status"] = "needs_setup"
                except (ValueError, OSError):
                    identity = {"status": "unreadable_or_invalid"}
            print(json.dumps(installation_plan(
                distro,
                machine,
                with_caddy=args.with_caddy,
                with_gateway=args.gateway,
                caddy_available=caddy_available,
                env_file=str(args.env_file),
                service_user=args.service_user,
                service_user_current=args.service_user_current,
                environment_identity=identity,
            )))
            return
        if args.check_ports is not None:
            check_ports(args.check_ports)
            return
        if args.check_env_path:
            if args.environment is None:
                parser.error("environment file is required")
            validate_protected_env_path(args.environment)
            return
        if args.check_private_group:
            if not args.service_user or not args.service_group:
                parser.error("--service-user and --service-group are required")
            validate_private_group(args.service_user, args.service_group)
            return
        if args.deployment_identity:
            if args.environment is None:
                parser.error("environment file is required")
            values = read_environment(args.environment)
            validate(values)
            for key in ("MXSTUDIO_HOME", "HF_HOME", "NUCLEUS_MODEL_ID", "NUCLEUS_MODEL_REVISION"):
                print(values[key])
            return
        if args.installation_values or args.verify_installed_identity:
            if args.environment is None or not args.service_user or not args.service_group:
                parser.error("environment file, --service-user, and --service-group are required")
            if args.verify_installed_identity:
                identity = verify_installed_identity(args.environment, args.service_user, args.service_group)
            else:
                identity = inspect_installation(args.environment, args.service_user)
            print(json.dumps(identity))
            return
        if args.environment is None:
            parser.error("environment file is required")
        values = read_environment(args.environment)
        validate(values, args.gateway)
        if args.probe:
            probe(values, args.gateway)
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(1, f"Nucleus preflight: {exc}\n")


if __name__ == "__main__":
    main()
