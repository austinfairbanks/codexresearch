import { database, ensureSnapshotSchema, runtimeEnv, type Projection } from "../../../../db/snapshots";

export const dynamic = "force-dynamic";
const MAX_BODY_BYTES = 2 * 1024 * 1024;
const PROJECT_ID = /^prj_[0-9a-f]{32}$/;
const REVISION = /^[0-9a-f]{64}$/;

async function tokensEqual(actual: string, expected: string): Promise<boolean> {
  const encoder = new TextEncoder();
  const [a, b] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(actual)),
    crypto.subtle.digest("SHA-256", encoder.encode(expected)),
  ]);
  const left = new Uint8Array(a);
  const right = new Uint8Array(b);
  let difference = left.length ^ right.length;
  for (let index = 0; index < Math.min(left.length, right.length); index += 1) difference |= left[index] ^ right[index];
  return difference === 0;
}

function validProjection(value: unknown): value is Projection {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<Projection>;
  return item.schema_version === 1 && item.projection_schema_version === "1.0.0"
    && typeof item.project_id === "string" && PROJECT_ID.test(item.project_id)
    && typeof item.thread_id === "string" && item.thread_id.length > 0 && item.thread_id.length <= 200
    && typeof item.project_revision === "string" && REVISION.test(item.project_revision)
    && Number.isSafeInteger(item.published_revision) && Number(item.published_revision) > 0
    && typeof item.produced_at === "string"
    && !!item.outline && typeof item.outline.content === "string"
    && !!item.state && typeof item.state === "object";
}

export async function POST(request: Request) {
  const expected = runtimeEnv().SOLERESEARCH_PUBLISH_TOKEN;
  const authorization = request.headers.get("authorization") ?? "";
  const actual = authorization.startsWith("Bearer ") ? authorization.slice(7) : "";
  if (!expected || !(await tokensEqual(actual, expected))) {
    return Response.json({ error: "publisher authentication failed" }, { status: 401 });
  }
  const length = Number(request.headers.get("content-length") ?? "0");
  if (length > MAX_BODY_BYTES) return Response.json({ error: "projection is too large" }, { status: 413 });
  const body = await request.arrayBuffer();
  if (body.byteLength > MAX_BODY_BYTES) return Response.json({ error: "projection is too large" }, { status: 413 });
  let projection: unknown;
  try {
    projection = JSON.parse(new TextDecoder().decode(body));
  } catch {
    return Response.json({ error: "invalid JSON" }, { status: 400 });
  }
  if (!validProjection(projection)) return Response.json({ error: "invalid projection contract" }, { status: 400 });

  const db = database();
  await ensureSnapshotSchema(db);
  const current = await db.prepare(
    "SELECT published_revision, project_revision FROM projects WHERE project_id = ?1",
  ).bind(projection.project_id).first<{ published_revision: number; project_revision: string }>();
  if (current && projection.published_revision < current.published_revision) {
    return Response.json({ error: "stale revision", current_revision: current.published_revision }, { status: 409 });
  }
  if (current && projection.published_revision === current.published_revision) {
    if (projection.project_revision !== current.project_revision) {
      return Response.json({ error: "revision identity conflict", current_revision: current.published_revision }, { status: 409 });
    }
    return Response.json({ project_id: projection.project_id, published_revision: projection.published_revision, idempotent: true });
  }

  const snapshot = JSON.stringify(projection);
  await db.batch([
    db.prepare("INSERT INTO revisions (project_id, published_revision, project_revision, produced_at, snapshot) VALUES (?1, ?2, ?3, ?4, ?5)")
      .bind(projection.project_id, projection.published_revision, projection.project_revision, projection.produced_at, snapshot),
    db.prepare(`INSERT INTO projects (project_id, published_revision, project_revision, produced_at, snapshot)
      VALUES (?1, ?2, ?3, ?4, ?5)
      ON CONFLICT(project_id) DO UPDATE SET
        published_revision = excluded.published_revision,
        project_revision = excluded.project_revision,
        produced_at = excluded.produced_at,
        snapshot = excluded.snapshot
      WHERE excluded.published_revision > projects.published_revision`)
      .bind(projection.project_id, projection.published_revision, projection.project_revision, projection.produced_at, snapshot),
  ]);
  return Response.json({ project_id: projection.project_id, published_revision: projection.published_revision, idempotent: false }, { status: 201 });
}
