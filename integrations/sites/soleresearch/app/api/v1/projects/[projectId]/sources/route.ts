import { collectionPage, projectHeaders } from "@/db/read-api";
import { getProjection } from "@/db/snapshots";

export const dynamic = "force-dynamic";

export async function GET(request: Request, context: { params: Promise<{ projectId: string }> }) {
  const { projectId } = await context.params;
  const projection = await getProjection(projectId);
  if (!projection) return Response.json({ error: "project not found" }, { status: 404 });
  try {
    return Response.json(collectionPage(request, projection, "sources"), { headers: projectHeaders(projection) });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "invalid cursor" }, { status: 400 });
  }
}
