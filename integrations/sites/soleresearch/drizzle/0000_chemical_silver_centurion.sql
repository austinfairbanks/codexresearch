CREATE TABLE `projects` (
	`project_id` text PRIMARY KEY NOT NULL,
	`published_revision` integer NOT NULL,
	`project_revision` text NOT NULL,
	`produced_at` text NOT NULL,
	`snapshot` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `revisions` (
	`project_id` text NOT NULL,
	`published_revision` integer NOT NULL,
	`project_revision` text NOT NULL,
	`produced_at` text NOT NULL,
	`snapshot` text NOT NULL,
	PRIMARY KEY(`project_id`, `published_revision`)
);
