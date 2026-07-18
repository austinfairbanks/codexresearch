import { projectHeaders } from "@/db/read-api";
import { getProjection } from "@/db/snapshots";

export const dynamic = "force-dynamic";

export async function GET(_request: Request, context: { params: Promise<{ projectId: string }> }) {
  const { projectId } = await context.params;
  const projection = await getProjection(projectId);
  if (!projection) return Response.json({ error: "project not found" }, { status: 404 });
  return Response.json({
    schema_version: 1,
    project_id: projection.project_id,
    published_revision: projection.published_revision,
    project_revision: projection.project_revision,
    produced_at: projection.produced_at,
    project: projection.state.project,
    collections: projection.collections,
    truncated: projection.truncated,
  }, { headers: projectHeaders(projection) });
}
