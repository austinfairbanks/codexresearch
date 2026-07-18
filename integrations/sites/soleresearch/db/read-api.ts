import { type Projection } from "./snapshots";

const DEFAULT_LIMIT = 100;
const MAX_LIMIT = 200;

type Cursor = {
  project_id: string;
  collection: string;
  published_revision: number;
  position: number;
  limit: number;
};

function encodeCursor(cursor: Cursor): string {
  return btoa(JSON.stringify(cursor)).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function decodeCursor(value: string): unknown {
  if (value.length > 1000 || !/^[A-Za-z0-9_-]+$/.test(value)) throw new Error("invalid cursor");
  const normalized = value.replaceAll("-", "+").replaceAll("_", "/");
  return JSON.parse(atob(normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "=")));
}

function pageInput(request: Request, projection: Projection, collection: string): { position: number; limit: number } {
  const url = new URL(request.url);
  const requestedLimit = Number(url.searchParams.get("limit") ?? DEFAULT_LIMIT);
  if (!Number.isSafeInteger(requestedLimit) || requestedLimit < 1 || requestedLimit > MAX_LIMIT) throw new Error("invalid limit");
  const encoded = url.searchParams.get("cursor");
  if (!encoded) return { position: 0, limit: requestedLimit };
  const decoded = decodeCursor(encoded) as Partial<Cursor>;
  if (
    decoded.project_id !== projection.project_id
    || decoded.collection !== collection
    || decoded.published_revision !== projection.published_revision
    || !Number.isSafeInteger(decoded.position) || Number(decoded.position) < 0
    || !Number.isSafeInteger(decoded.limit) || Number(decoded.limit) < 1 || Number(decoded.limit) > MAX_LIMIT
  ) throw new Error("cursor does not match the selected project revision");
  return { position: Number(decoded.position), limit: Number(decoded.limit) };
}

export function collectionPage(request: Request, projection: Projection, collection: string): Record<string, unknown> {
  const view = projection.state.views[collection] as { items?: unknown[]; total?: number; truncated?: boolean } | undefined;
  if (!view || !Array.isArray(view.items)) throw new Error("unknown collection");
  const { position, limit } = pageInput(request, projection, collection);
  const items = view.items.slice(position, position + limit);
  const nextPosition = position + items.length;
  const hasIncludedMore = nextPosition < view.items.length;
  return {
    schema_version: 1,
    project_id: projection.project_id,
    published_revision: projection.published_revision,
    collection,
    items,
    total: Number(view.total ?? view.items.length),
    included: view.items.length,
    truncated: Boolean(view.truncated),
    next_cursor: hasIncludedMore ? encodeCursor({
      project_id: projection.project_id,
      collection,
      published_revision: projection.published_revision,
      position: nextPosition,
      limit,
    }) : null,
  };
}

export function projectHeaders(projection: Projection): HeadersInit {
  return {
    "Cache-Control": "no-store",
    "X-Sole-Research-Project": projection.project_id,
    "X-Sole-Research-Revision": String(projection.published_revision),
  };
}
