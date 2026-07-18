import { listProjections } from "../../../../db/snapshots";

export const dynamic = "force-dynamic";

export async function GET() {
  const projections = await listProjections();
  const projects = projections.map((projection) => {
    const state = projection.state;
    const nodes = state.views.nodes;
    const activeNodes = (nodes.items as Array<Record<string, unknown>>).filter((node) => !node.retired);
    return {
      project_id: projection.project_id,
      name: state.project.name,
      directory: projection.project_id,
      questions: activeNodes
        .filter((node) => node.node_type === "question" && !node.parent_id)
        .map((node) => String(node.title)),
      node_count: nodes.total,
      source_count: state.views.sources.total,
      evidence_count: state.views.evidence.total,
      editable: false,
      available: true,
      availability_error: null,
      preview: {
        nodes: activeNodes.map((node) => ({
          node_id: node.node_id,
          parent_id: node.parent_id ?? null,
          node_type: node.node_type,
          title: node.title,
          evidence_count: Array.isArray(node.evidence_ids) ? node.evidence_ids.length : 0,
        })),
        total: nodes.total,
        truncated: nodes.truncated,
      },
    };
  });
  return Response.json({
    schema_version: 1,
    default_project_id: projects[0]?.project_id ?? null,
    workspace_name: projects.length === 1 ? projects[0].name : "Sole Research",
    projects,
  }, { headers: { "Cache-Control": "no-store" } });
}
