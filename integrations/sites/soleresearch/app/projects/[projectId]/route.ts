import { getProjection } from "@/db/snapshots";

export const dynamic = "force-dynamic";

export async function GET(request: Request, context: { params: Promise<{ projectId: string }> }) {
  const { projectId } = await context.params;
  if (!(await getProjection(projectId))) return Response.json({ error: "project not found" }, { status: 404 });
  const dashboard = new URL("/dashboard.html", request.url);
  dashboard.hash = new URLSearchParams({ project: projectId }).toString();
  return Response.redirect(dashboard, 307);
}
