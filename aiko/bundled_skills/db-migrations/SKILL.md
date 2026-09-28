---
name: db-migrations
description: Design and validate database schema or data migrations while preserving data and version compatibility.
---

Inspect schema, constraints, migration history, data volume, and readers/writers. Define invariants such as ownership, uniqueness, null semantics, and relevant aggregate totals. Identify locking and table rewrites for the deployed engine version.

Use expand, backfill, validate, then contract for overlapping deployments. Bound and resume long backfills. Read [references/online-migration.md](references/online-migration.md) for production-sized tables.

Validate on representative restored data when available. Compare invariants, old/new application compatibility, and restart behavior. Rerun only migrations designed for idempotence; otherwise verify the runner prevents duplication. Consult [PostgreSQL ALTER TABLE](https://www.postgresql.org/docs/current/sql-altertable.html) for operation-specific locks. Reversing schema may not recover transformed data; explain the real recovery path.
