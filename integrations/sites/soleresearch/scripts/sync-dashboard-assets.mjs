import { copyFile, mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const siteRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = resolve(siteRoot, "../../..");
const sourceRoot = resolve(repositoryRoot, "src/soleresearch/ui");
const publicRoot = resolve(siteRoot, "public");
const assetRoot = resolve(publicRoot, "assets");

await mkdir(assetRoot, { recursive: true });
for (const name of ["app.css", "app.js", "layout.js"]) {
  await copyFile(resolve(sourceRoot, name), resolve(assetRoot, name));
}

const sourceHtml = await readFile(resolve(sourceRoot, "index.html"), "utf8");
const bootstrap = JSON.stringify({
  schema_version: 1,
  annotation_enabled: false,
  csrf_token: null,
}).replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
await writeFile(
  resolve(publicRoot, "dashboard.html"),
  sourceHtml.replace("__SOLERESEARCH_BOOTSTRAP__", bootstrap),
  "utf8",
);
