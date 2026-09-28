# Online migration decisions

Measure table size, write rate, replication lag, and long-running transactions. Determine whether the proposed DDL takes an exclusive lock, scans existing rows, or rewrites the table on the deployed engine version.

For a required field, consider adding a compatible nullable field, deploying compatible writers, backfilling in bounded batches, validating completeness, then enforcing the constraint. Choose a stable key and checkpoint so interrupted batches can resume without skipping records.

Keep reads compatible during the transition; dual writes require defined failure and reconciliation behavior. A copied field is not authoritative until divergence has been checked.

Set lock and statement timeouts appropriate to the operation. Test failure midway and validate constraints independently from application assumptions. Do not put operations requiring nontransactional execution inside the migration runner's default transaction.

Consult [PostgreSQL ALTER TABLE](https://www.postgresql.org/docs/current/sql-altertable.html) for lock levels and constraint validation. Remove old fields only after dependent readers and writers are retired.
