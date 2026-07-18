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
  ]);
  const hosting = JSON.parse(await readFile(new URL("dist/.openai/hosting.json", root), "utf8"));
  assert.equal(hosting.d1, "DB");
  assert.equal(hosting.r2, null);
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
