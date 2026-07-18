import { env } from "cloudflare:workers";

export type Projection = {
  schema_version: 1;
  projection_schema_version: "1.0.0";
  project_id: string;
  thread_id: string;
  project_revision: string;
  content_sha256: string;
  published_revision: number;
  produced_at: string;
  collections: Record<string, {
    total: number;
    included: number;
    truncated: boolean;
    cursor: null | { project_id: string; collection: string; published_revision: number; position: number; limit: number };
  }>;
  truncated: boolean;
  outline: {
    hash: string;
    dirty: boolean;
    reconciliation_required: boolean;
    content: string;
  };
  state: Record<string, unknown> & {
    project: { project_id: string; name: string; data_policy: string };
    views: Record<string, { items: unknown[]; total: number; truncated: boolean }>;
  };
};

type RuntimeEnv = {
  DB?: D1Database;
  SOLERESEARCH_PUBLISH_TOKEN?: string;
};

export function runtimeEnv(): RuntimeEnv {
  return env as unknown as RuntimeEnv;
}

export function database(): D1Database {
  const db = runtimeEnv().DB;
  if (!db) throw new Error("Sites D1 binding DB is unavailable");
  return db;
}

export async function ensureSnapshotSchema(db: D1Database): Promise<void> {
  await db.batch([
    db.prepare(`CREATE TABLE IF NOT EXISTS projects (
      project_id TEXT PRIMARY KEY NOT NULL,
      published_revision INTEGER NOT NULL,
      project_revision TEXT NOT NULL,
      produced_at TEXT NOT NULL,
      snapshot TEXT NOT NULL
    )`),
    db.prepare(`CREATE TABLE IF NOT EXISTS revisions (
      project_id TEXT NOT NULL,
      published_revision INTEGER NOT NULL,
      project_revision TEXT NOT NULL,
      produced_at TEXT NOT NULL,
      snapshot TEXT NOT NULL,
      PRIMARY KEY (project_id, published_revision)
    )`),
    db.prepare("CREATE INDEX IF NOT EXISTS revisions_project_revision_idx ON revisions (project_id, published_revision DESC)"),
  ]);
}

export function parseProjection(value: string): Projection {
  return JSON.parse(value) as Projection;
}

export async function getProjection(projectId: string | null): Promise<Projection | null> {
  const db = database();
  await ensureSnapshotSchema(db);
  const row = projectId
    ? await db.prepare("SELECT snapshot FROM projects WHERE project_id = ?1").bind(projectId).first<{ snapshot: string }>()
    : await db.prepare("SELECT snapshot FROM projects ORDER BY produced_at DESC, project_id ASC LIMIT 1").first<{ snapshot: string }>();
  return row ? parseProjection(row.snapshot) : null;
}

export async function listProjections(): Promise<Projection[]> {
  const db = database();
  await ensureSnapshotSchema(db);
  const result = await db.prepare("SELECT snapshot FROM projects ORDER BY produced_at DESC, project_id ASC").all<{ snapshot: string }>();
  return result.results.map((row) => parseProjection(row.snapshot));
}
