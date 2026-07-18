import { index, integer, primaryKey, sqliteTable, text } from "drizzle-orm/sqlite-core";

export const projects = sqliteTable("projects", {
  projectId: text("project_id").primaryKey().notNull(),
  publishedRevision: integer("published_revision").notNull(),
  projectRevision: text("project_revision").notNull(),
  producedAt: text("produced_at").notNull(),
  snapshot: text("snapshot").notNull(),
});

export const revisions = sqliteTable("revisions", {
  projectId: text("project_id").notNull(),
  publishedRevision: integer("published_revision").notNull(),
  projectRevision: text("project_revision").notNull(),
  producedAt: text("produced_at").notNull(),
  snapshot: text("snapshot").notNull(),
}, (table) => [
  primaryKey({ columns: [table.projectId, table.publishedRevision] }),
  index("revisions_project_revision_idx").on(table.projectId, table.publishedRevision),
]);
