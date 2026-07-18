# Sole Research plugin lifecycle canary

Canary date: 2026-07-18  
Plugin: `soleresearch@personal`  
Version: `0.2.0+codex.20260718200323`  
Site version: 4, owner-only production deployment

## Procedure and results

1. Recorded SHA-256 hashes for the macOS Application Support workspace and
   Site registries.
2. Removed only `soleresearch@personal` through `codex plugin remove`.
3. Confirmed Codex reported the plugin as not installed while the marketplace
   source, workspace, deployed Site, and both registries remained present.
4. Confirmed both registry hashes were unchanged:
   - workspace: `3cafacea2ebc17df60cdd2448b0c7a6cdf759cf0a58e7facb18a17e14a328a39`
   - Site: `4f06e0f4cecd02d8a4589f4e320f49d7c1c5c7e08599e4f3d63e8da5f5776b51`
5. Reinstalled from the personal marketplace and confirmed Codex selected the
   same immutable cachebuster version.
6. Confirmed the installed binary and checked-in plugin binary have the same
   SHA-256:
   `f79292ea13b854e840ea59907fccb55d44f542a98ac7069138cca13825c77650`.
7. Launched the installed standalone binary directly. `--version`, `workspace
   show`, and `site show` succeeded and recovered the preserved workspace and
   Site configuration without a target Node or Python process.
8. Sent fail-closed production probes with bogus/revoked credential values.
   Both Sites access and snapshot publication returned HTTP 401, and the live
   project revision remained 7 before and after the probes.
9. Registered a disposable local marketplace containing a copy of the plugin,
   installed cachebuster version `0.2.0+codex.20260718200323`, updated it to
   `0.2.0+codex.20260718202710`, and rolled it back to the original version.
   Each install resolved to its versioned Codex cache path. The aggregate hash
   of the independent demo project remained
   `66777dbf06eaf69764cebaad08c59295b9deee8d63ec507aa698ff2181358495`
   throughout. The canary plugin, marketplace registration, and temporary
   copied source were removed afterward; the primary personal plugin remained
   installed and enabled.

## Disposition

Install, update, rollback, uninstall, reinstall/reconnection, data
preservation, and revoked-credential rejection pass on the supported macOS
arm64 developer-preview row. The rollback mechanism is verified with two
disposable local cachebuster versions; testing two real published immutable
releases remains a distribution check. Site deletion and real credential
rotation remain explicit human-controlled operations and are not part of an
uninstall canary.
