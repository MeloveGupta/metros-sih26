# db

SQLAlchemy models: `User`, `ProductRow`, `ScanRow` (canonical `Report` stored
as JSON, plus denormalized columns for search — disposition, calibrated,
finalized, has_rule7_flag), `AuditLog`. No migration framework — `init_db()`
just calls `create_all()`; on a schema change in development, delete
`data/metroscan.db` and restart rather than migrating it. Repository module
adds search/paging, stats aggregation, and audit-log helpers.
