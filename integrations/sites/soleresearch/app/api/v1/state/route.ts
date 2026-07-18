import { getProjection } from "../../../../db/snapshots";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const projectId = new URL(request.url).searchParams.get("project");
  const projection = await getProjection(projectId);
  if (!projection) return Response.json({ error: "project not found" }, { status: 404 });
  return Response.json({
    ...projection.state,
    publication: {
      status: "live",
      published_revision: projection.published_revision,
      project_revision: projection.project_revision,
      produced_at: projection.produced_at,
    },
  }, {
    headers: {
      "Cache-Control": "no-store",
      "X-Sole-Research-Revision": String(projection.published_revision),
    },
  });
}
