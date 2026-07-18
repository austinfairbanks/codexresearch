import { getProjection } from "../../../../db/snapshots";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const projectId = new URL(request.url).searchParams.get("project");
  const projection = await getProjection(projectId);
  const signalId = projection
    ? `sig_${projection.project_revision.slice(0, 32)}`
    : null;
  return Response.json({
    schema_version: 1,
    signal_id: signalId,
    project_id: projection?.project_id ?? null,
    published_revision: projection?.published_revision ?? null,
    produced_at: projection?.produced_at ?? null,
  }, {
    headers: { "Cache-Control": "no-store" },
  });
}
