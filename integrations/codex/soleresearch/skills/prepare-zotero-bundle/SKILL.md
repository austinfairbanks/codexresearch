---
name: prepare-zotero-bundle
description: Prepare a deterministic, human-reviewed BibTeX and CSL-JSON bundle from a valid Soleresearch project for manual import into Zotero. Use when the user wants Zotero-ready citation files, duplicate/metadata review, or a citation handoff without granting library or network access.
---

# Prepare Zotero Bundle

1. Use `soleresearch_doctor` and stop on an invalid or unmigrated
   project.
2. Use `soleresearch_zotero_bundle` with a new explicit output directory.
3. Read `review-manifest.json`. Verify both file hashes, inspect every reported
   duplicate citation key/ID, DOI, and arXiv ID, resolve BibTeX-only and CSL-only
   canonical identities, and complete the remaining metadata/import checks.
4. Present the manifest, counts, and paths to the human. The bundle remains
   `pending_human_review` even after discussion because Soleresearch does not
   control Zotero.
5. Let the human manually import either `references.bib` or
   `references.csl.json` in Zotero. Do not import both unless they deliberately
   want duplicate records.

The named MCP tool invokes the deterministic native runtime. It
never contacts Zotero, uses the network, opens an application, mutates a Zotero
library, overwrites an output directory, or copies controller capabilities.
