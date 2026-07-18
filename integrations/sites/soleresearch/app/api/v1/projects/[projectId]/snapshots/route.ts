import { publishSnapshot } from "@/app/api/v1/publish/route";

export const dynamic = "force-dynamic";

export async function POST(request: Request, context: { params: Promise<{ projectId: string }> }) {
  const { projectId } = await context.params;
  return publishSnapshot(request, projectId);
}
