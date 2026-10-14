# Deploying MX Studio Nucleus

**Try [the localhost quickstart](../../docs/LEGACY_NUCLEUS.md#optional-nucleus-appliance-profile)
before installing services.** The backend is FastAPI, not FastHTML. Local jobs
already use SQLite; no PostgreSQL server is needed for the core controller.

## Choose the smallest profile

| Profile | Components | Use it when |
| --- | --- | --- |
| Local trial | Nucleus + MAX + existing SQLite job store | Test a model and connect an app on this computer. No installer needed. |
| Core LAN appliance | Caddy → Nucleus → MAX; existing SQLite job store | LAN clients need HTTPS and one shared key is sufficient. |
| Optional gateway | Caddy → LiteLLM → Nucleus → MAX; SQLite for Studio, PostgreSQL for gateway records | Separate app keys or gateway routing justify the extra service/database. |

SQLite is not a drop-in connection-string replacement for the supplied
PostgreSQL-backed gateway configuration. Skipping the gateway is the simple,
supported way to avoid that dependency.

## Preview and explicit apply boundary

From the repository root, preview is always read-only (also the default):

```bash
./deploy/nucleus/install.sh --dry-run --with-caddy \
  --env-file /etc/mxstudio/nucleus.env --service-user-current
```

The JSON plan reports the detected distro/architecture, exact package names,
files, directories, services and (when readable) the non-secret environment
identity. Apply only after a person has reviewed and approved that plan:

```bash
sudo ./deploy/nucleus/install.sh --apply --with-caddy \
  --env-file /etc/mxstudio/nucleus.env --service-user-current
```

The installer is a manual operator action; MX Studio deployment workers must
never invoke it through sudo or root. `--service-user-current` resolves the
invoking user (including `SUDO_USER` under sudo), uses that user's primary
private group, and refuses shared groups; if needed, pre-provision a private group and pass
`--service-group GROUP`. Prepare the EnvironmentFile as
`root:<private-group>` mode `0640`; it is referenced by systemd, never copied or
rewritten. The service user must already own and be able to read/write/traverse
both `MXSTUDIO_HOME` and `HF_HOME`. The installer checks those exact paths and
does not chown, copy, or create the user's state/cache directories.

`--install` remains a backwards-compatible explicit-apply alias. There is no
installer confirmation prompt or implicit apply. The installer supports Debian/Ubuntu
and Arch/Omarchy on x86_64. Debian uses named `apt-get install` packages without
refreshing package indexes; Arch uses `pacman --sync --needed` against its
existing sync databases (the approved package plan is noninteractive). It never
runs `pacman --refresh` or `--sysupgrade`, and refuses package changes if `pacman --query --upgrades` reports updates. Bring Arch fully up to date using
the normal system-upgrade procedure first (with current sync databases), not a
partial package upgrade from this installer. Add
`--with-gateway` only after provisioning PostgreSQL and gateway credentials.

Before apply, complete the
[service-user, cache, environment-file, and port prerequisites](../../docs/MXSTUDIO_NUCLEUS.md#optional-systemd-installation).
The checkout must be readable/traversable by the selected service user, and
its existing `.pixi` environment must be writable by that account. Put Pixi at a
shared executable path or ensure the service account can execute its discovered
binary. Do not `chown` the checkout or existing state/cache paths to satisfy
preflight. The installer never copies model weights. In current-user mode,
`MXSTUDIO_HOME` and `HF_HOME` must already exist and be owned by that user; the
legacy `mxstudio` account path may create only its standard directories when
absent. The protected EnvironmentFile is required before apply; use mode `0640`, owner
`root:<service-group>`, and the example as a template, not ready-to-use
credentials. Populate canonical `NUCLEUS_MODEL_ID` and its immutable 40-hex
`NUCLEUS_MODEL_REVISION`, and point `MXSTUDIO_HOME`/`HF_HOME` at the state and
model cache used by the selected service user. `preflight.py ENV --verify-installed-identity --service-user USER
--service-group GROUP` returns checked non-secret identity JSON, and verifies the
loaded unit user/group and EnvironmentFile for orchestration resume checks.

The installer preserves existing config and refuses active/enabled host Caddy,
conflicting unit names, or an existing managed Caddyfile. Review and integrate
the inference-only route block manually when those conflicts exist. It validates
the selected Caddyfile before enabling ingress. On activation failure, only
newly installed MX Studio unit/config files, a newly created gateway virtual
environment, and services started/enabled by that run are rolled back; packages,
accounts, shared cache/CA state, model data, and prior configuration are
preserved. A run manifest is kept in
`/etc/mxstudio/install-manifests/` (root-only).

## What still needs your verification

- Run a real inference request; installation's HTTP probe alone does not prove MAX readiness.
- Trust Caddy's CA on each client and test hostname resolution/HTTPS from another device.
- Enable/test mDNS only if the receiving app supports it; discovery does not establish TLS trust.
- Test service startup and inference after reboot.
- If using LiteLLM, test app-key creation and revocation. Cloud fallback is a separate explicit opt-in, never the local default.

See [the complete usage and operations guide](../../docs/MXSTUDIO_NUCLEUS.md),
[troubleshooting](../../docs/MXSTUDIO_NUCLEUS.md#troubleshooting), and the
[original-requirement checklist](../../docs/MXSTUDIO_NUCLEUS.md#does-this-complete-the-original-specification).
