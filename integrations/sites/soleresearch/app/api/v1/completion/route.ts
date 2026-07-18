import { getProjection } from "../../../../db/snapshots";

export const dynamic = "force-dynamic";

export async function GET() {
  const projection = await getProjection(null);
  const signalId = projection
    ? `sig_${projection.project_revision.slice(0, 32)}`
    : null;
  return Response.json({ schema_version: 1, signal_id: signalId }, {
    headers: { "Cache-Control": "no-store" },
  });
}
