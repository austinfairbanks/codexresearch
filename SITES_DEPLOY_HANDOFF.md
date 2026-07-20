# Codex Sites deployment handoff

This handoff intentionally leaves the current source revision undeployed so the
dashboard can be tuned locally first. The existing production Site remains
available at:

`https://sole-research.general992066.chatgpt.site`

## Fixed deployment identity

- Sites project ID: `appgprj_6a5bcbaa632881919854d73b80782749`
- Hosting manifest: `integrations/sites/soleresearch/.openai/hosting.json`
- Site source: `integrations/sites/soleresearch`
- Access policy: public read-only
- Browser credentials: none
- Snapshot writes: protected by the Site-scoped
  `SOLERESEARCH_PUBLISH_TOKEN`
- Publishable research policy: `public_only` projects only

Reuse this project ID. Do not create a replacement Site, generate a Sign in with
ChatGPT bypass token, or place credentials in the repository.

## Tune and validate locally

The Node toolchain is maintainer-only for changing and validating the hosted
Site. It is not a prerequisite for an installed Sole Research user.

From `integrations/sites/soleresearch`:

```bash
npm run dev
npm test
npm run lint
```

`npm test` performs the production build and the rendered/route/security tests.
At handoff time, all 274 Python tests and all 5 Site tests passed. Lint had zero
errors and five existing unused-variable warnings in the shared dashboard
assets.

If tuning changes shared dashboard assets under `src/soleresearch/ui`, the Site
prebuild step copies them into `integrations/sites/soleresearch/public`. Commit
both the authoritative shared assets and their synchronized Site copies.

If tuning changes the local runtime or plugin rather than only the Site, also
run:

```bash
uv run pytest -q
uv run python scripts/build-native.py --output-dir artifacts --verify --plugin
python3 /Users/austinfairbanks/.codex/skills/.system/plugin-creator/scripts/update_plugin_cachebuster.py integrations/codex/soleresearch
```

Then validate the plugin, synchronize it to the existing personal-marketplace
source at `/Users/austinfairbanks/plugins/soleresearch`, and reinstall
`soleresearch@personal`. Do not hand-edit the marketplace file.

## Deploy the tuned commit with Sites

Use the current `sites:sites-building` and `sites:sites-hosting` workflows. The
deployment must reference one exact validated Git commit:

1. Confirm the worktree contains only the intended tuning changes.
2. Run the Site build/tests above and fix any failures.
3. Commit the exact validated source and record `git rev-parse HEAD`.
4. Read the existing project ID from `.openai/hosting.json`; do not call
   `create_site`.
5. Request a short-lived source-repository write credential with the Sites
   connector. Use it only as a per-command HTTP authorization header; never
   persist or print it.
6. Push the exact committed source to the credential's returned repository and
   branch. The pushed branch-head SHA must equal the recorded commit SHA.
7. Package the Site with the installed Sites plugin's root-level
   `scripts/package-site.sh`, passing `integrations/sites/soleresearch` and a
   temporary archive path.
8. Save one Site version using the exact commit SHA and archive.
9. Because this Site is public, obtain explicit approval for the production
   deployment, then deploy that saved version with `deploy_site_version`.
10. Poll `get_deployment_status` until it succeeds or fails. Do not claim a
    deployment from a nonterminal status.
11. Open the returned production URL and verify the checks below.

The production runtime environment must retain `SOLERESEARCH_PUBLISH_TOKEN` as
a secret. Do not rotate it merely for a source deployment. If it is deliberately
rotated, update the external mode-0600 publisher file used by Sole Research at
the same time.

## Post-deployment verification

Verify all of the following without a viewer token or authenticated browser
session:

- `/` loads without redirecting to sign-in.
- `/api/v1/workspace` returns the public project list.
- `/projects/<project-id>` redirects to the read-only dashboard.
- `/api/v1/projects/<project-id>/revision` returns the current immutable
  revision without an `OAI-Sites-Authorization` header.
- The dashboard exposes no edit, mutation, approval, or credential controls.
- A normal publisher request with the configured write credential succeeds.
- A bogus publisher credential receives HTTP 401 and does not advance the
  project revision.
- A `local_private` project is rejected locally before any publication request.

Finally, record the deployed Site version, commit SHA, deployment status, live
URL, and published project revision in `TRANSITION_IMPLEMENTATION_STATUS.md`.
