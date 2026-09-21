# Deploying MX Studio Nucleus

**Try [the localhost quickstart](../../docs/USER_GUIDE.md#optional-nucleus-appliance-profile)
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

## Preview before installing

From the repository root:

```bash
./deploy/nucleus/install.sh --dry-run --with-caddy
```

This is read-only. Explicit installation supports **Debian/Ubuntu x86_64**, not
Arch/Omarchy or Apple-Silicon Linux. First complete the
[service-user, cache, environment-file, and port prerequisites](../../docs/MXSTUDIO_NUCLEUS.md#optional-systemd-installation).
Then:

```bash
sudo ./deploy/nucleus/install.sh --install --with-caddy
```

Add `--with-gateway` only after provisioning PostgreSQL and gateway credentials.
The installer preserves existing configuration, does not generate secrets or
download model weights, and does not silently switch an existing Caddy profile.
The environment-file example is a template, not ready-to-use credentials.

## What still needs your verification

- Run a real inference request; installation's HTTP probe alone does not prove MAX readiness.
- Trust Caddy's CA on each client and test hostname resolution/HTTPS from another device.
- Enable/test mDNS only if the receiving app supports it; discovery does not establish TLS trust.
- Test service startup and inference after reboot.
- If using LiteLLM, test app-key creation and revocation. Cloud fallback is a separate explicit opt-in, never the local default.

See [the complete usage and operations guide](../../docs/MXSTUDIO_NUCLEUS.md),
[troubleshooting](../../docs/MXSTUDIO_NUCLEUS.md#troubleshooting), and the
[original-requirement checklist](../../docs/MXSTUDIO_NUCLEUS.md#does-this-complete-the-original-specification).
