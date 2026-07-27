import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

test("packages the full read-only dashboard instead of the starter", async () => {
  const [html, app, layout, css] = await Promise.all([
    readFile(new URL("public/dashboard.html", root), "utf8"),
    readFile(new URL("public/assets/app.js", root), "utf8"),
    readFile(new URL("public/assets/layout.js", root), "utf8"),
    readFile(new URL("public/assets/app.css", root), "utf8"),
  ]);
  assert.match(html, /<title>Sole Research<\/title>/);
  assert.match(html, /data-bootstrap="\{&quot;schema_version&quot;:1/);
  assert.match(html, /id="map-canvas"/);
  assert.match(html, /id="outline-editor"[^>]*readonly/);
  assert.match(app, /fetch\("\/api\/v1\/workspace"/);
  assert.match(app, /function renderEmptyWorkspaceMap\(\)/);
  assert.match(app, /Research question/);
  assert.match(app, /Research branch/);
  assert.match(app, /Evidence/);
  assert.match(app, /if \(!workspace\.projects\.length\)/);
  assert.match(css, /body\.empty-workspace/);
  assert.match(layout, /SoleResearchLayout/);
  assert.match(css, /\.split-workspace/);
  assert.doesNotMatch(html, /codex-preview|react-loading-skeleton/);
});

test("build output contains Sites metadata, migrations, worker, and dashboard", async () => {
  await Promise.all([
    access(new URL("dist/server/index.js", root)),
    access(new URL("dist/client/dashboard.html", root)),
    access(new URL("dist/client/assets/app.js", root)),
    access(new URL("dist/.openai/hosting.json", root)),
    access(new URL("dist/.openai/drizzle/0000_chemical_silver_centurion.sql", root)),
    access(new URL("dist/.openai/drizzle/0001_fast_longshot.sql", root)),
    access(new URL("dist/.openai/drizzle/0002_remove_outsole_demo.sql", root)),
  ]);
  const hosting = JSON.parse(await readFile(new URL("dist/.openai/hosting.json", root), "utf8"));
  assert.equal(hosting.d1, "DB");
  assert.equal(hosting.r2, null);
});

test("removes only the obsolete outsole demo from production storage", async () => {
  const migration = await readFile(new URL("drizzle/0002_remove_outsole_demo.sql", root), "utf8");
  assert.match(migration, /DELETE FROM `revisions` WHERE `project_id` = 'prj_fe682aed09914e4e835882ad221117fd'/);
  assert.match(migration, /DELETE FROM `projects` WHERE `project_id` = 'prj_fe682aed09914e4e835882ad221117fd'/);
  assert.doesNotMatch(migration, /DELETE FROM `projects`;|DELETE FROM `revisions`;/);
});

test("publisher is authenticated, bounded, revision-safe, and browser routes stay read-only", async () => {
  const [publisher, snapshotAlias, readApi, worker] = await Promise.all([
    readFile(new URL("app/api/v1/publish/route.ts", root), "utf8"),
    readFile(new URL("app/api/v1/projects/[projectId]/snapshots/route.ts", root), "utf8"),
    readFile(new URL("db/read-api.ts", root), "utf8"),
    readFile(new URL("worker/index.ts", root), "utf8"),
  ]);
  assert.match(publisher, /SOLERESEARCH_PUBLISH_TOKEN/);
  assert.match(publisher, /MAX_BODY_BYTES/);
  assert.match(publisher, /stale revision/);
  assert.match(publisher, /revision identity conflict/);
  assert.match(publisher, /projection content hash mismatch/);
  assert.match(publisher, /LIMIT 20/);
  assert.match(publisher, /crypto\.subtle\.digest/);
  assert.match(publisher, /project ID substitution rejected/);
  assert.match(snapshotAlias, /publishSnapshot\(request, projectId\)/);
  assert.match(readApi, /cursor does not match the selected project revision/);
  assert.match(readApi, /MAX_LIMIT = 200/);
  assert.doesNotMatch(worker, /PUT|PATCH|DELETE/);
});

test("public dashboard has no viewer authentication surface", async () => {
  const [page, layout, siteReadme] = await Promise.all([
    readFile(new URL("app/page.tsx", root), "utf8"),
    readFile(new URL("app/layout.tsx", root), "utf8"),
    readFile(new URL("README.md", root), "utf8"),
  ]);
  assert.doesNotMatch(page, /signin|authenticated|credential|token/i);
  assert.match(layout, /public, read-only/);
  assert.match(siteReadme, /Public, read-only/);
  await assert.rejects(access(new URL("app/chatgpt-auth.ts", root)));
});

test("provides every project-scoped route in the transition contract", async () => {
  await Promise.all([
    "app/projects/[projectId]/route.ts",
    "app/api/v1/projects/[projectId]/summary/route.ts",
    "app/api/v1/projects/[projectId]/graph/route.ts",
    "app/api/v1/projects/[projectId]/sources/route.ts",
    "app/api/v1/projects/[projectId]/evidence/route.ts",
    "app/api/v1/projects/[projectId]/outline/route.ts",
    "app/api/v1/projects/[projectId]/revision/route.ts",
    "app/api/v1/projects/[projectId]/snapshots/route.ts",
  ].map((path) => access(new URL(path, root))));
});
