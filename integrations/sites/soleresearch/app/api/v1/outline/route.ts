import { getProjection } from "../../../../db/snapshots";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const projectId = new URL(request.url).searchParams.get("project");
  const projection = await getProjection(projectId);
  if (!projection) return Response.json({ error: "project not found" }, { status: 404 });
  return Response.json({
    schema_version: 1,
    content: projection.outline.content,
    outline_hash: projection.outline.hash,
  }, { headers: { "Cache-Control": "no-store", ETag: `"${projection.outline.hash}"` } });
}
