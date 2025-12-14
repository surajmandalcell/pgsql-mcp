# Comprehensive Test Plan for Postgres MCP Pro

This document outlines all test cases for the Postgres MCP Pro server, covering existing functionality, edge cases, and new features.

## Test Categories

### 1. Server Core Tests (`tests/unit/server/`)

#### 1.1 CLI Argument Parsing
- [ ] `test_cli_database_url_positional` - Database URL as positional argument
- [ ] `test_cli_database_url_from_env` - DATABASE_URI environment variable
- [ ] `test_cli_access_mode_unrestricted` - --access-mode=unrestricted
- [ ] `test_cli_access_mode_restricted` - --access-mode=restricted
- [ ] `test_cli_transport_stdio` - --transport=stdio (default)
- [ ] `test_cli_transport_sse` - --transport=sse
- [ ] `test_cli_sse_host` - --sse-host argument
- [ ] `test_cli_sse_port` - --sse-port argument
- [ ] `test_cli_sse_path` - --sse-path argument
- [ ] `test_cli_cors_allow_origins` - --cors-allow-origins argument
- [ ] `test_cli_query_timeout` - --query-timeout argument
- [ ] `test_cli_env_sse_host` - SSE_HOST environment variable
- [ ] `test_cli_env_sse_port` - SSE_PORT environment variable
- [ ] `test_cli_env_sse_path` - SSE_PATH environment variable
- [ ] `test_cli_env_cors_allow_origins` - CORS_ALLOW_ORIGINS environment variable
- [ ] `test_cli_env_query_timeout` - QUERY_TIMEOUT environment variable
- [ ] `test_cli_arg_precedence_over_env` - CLI arguments override env vars
- [ ] `test_cli_invalid_sse_port` - Invalid SSE_PORT value
- [ ] `test_cli_invalid_query_timeout` - Invalid QUERY_TIMEOUT value
- [ ] `test_cli_missing_database_url` - Error when no database URL provided

#### 1.2 .env File Loading
- [ ] `test_dotenv_loads_database_uri` - Load DATABASE_URI from .env
- [ ] `test_dotenv_loads_sse_config` - Load SSE config from .env
- [ ] `test_dotenv_loads_cors_config` - Load CORS config from .env
- [ ] `test_dotenv_loads_query_timeout` - Load QUERY_TIMEOUT from .env
- [ ] `test_dotenv_cli_precedence` - CLI args override .env values
- [ ] `test_dotenv_env_precedence_over_file` - Shell env overrides .env file

#### 1.3 SSE Transport
- [ ] `test_sse_server_starts` - SSE server starts successfully
- [ ] `test_sse_server_custom_host` - SSE server binds to custom host
- [ ] `test_sse_server_custom_port` - SSE server uses custom port
- [ ] `test_sse_server_custom_path` - SSE endpoint uses custom path
- [ ] `test_sse_cors_single_origin` - CORS with single origin
- [ ] `test_sse_cors_multiple_origins` - CORS with multiple origins
- [ ] `test_sse_cors_wildcard` - CORS with wildcard (*)
- [ ] `test_sse_cors_preflight` - CORS preflight request handling
- [ ] `test_sse_cors_credentials` - CORS with credentials

#### 1.4 Signal Handling
- [ ] `test_shutdown_sigterm` - Graceful shutdown on SIGTERM
- [ ] `test_shutdown_sigint` - Graceful shutdown on SIGINT
- [ ] `test_shutdown_database_cleanup` - Database connections closed on shutdown
- [ ] `test_shutdown_force_exit` - Force exit on repeated signal

### 2. SQL Driver Tests (`tests/unit/sql/`)

#### 2.1 Safe SQL Driver - Allowed Operations
- [ ] `test_select_simple` - Simple SELECT query
- [ ] `test_select_with_where` - SELECT with WHERE clause
- [ ] `test_select_with_join` - SELECT with JOIN
- [ ] `test_select_with_subquery` - SELECT with subquery
- [ ] `test_select_with_union` - SELECT with UNION
- [ ] `test_select_with_cte` - SELECT with CTE (WITH clause)
- [ ] `test_select_with_window_function` - SELECT with window functions
- [ ] `test_select_with_group_by` - SELECT with GROUP BY
- [ ] `test_select_with_having` - SELECT with HAVING
- [ ] `test_select_with_order_by` - SELECT with ORDER BY
- [ ] `test_select_with_limit_offset` - SELECT with LIMIT/OFFSET
- [ ] `test_select_distinct` - SELECT DISTINCT
- [ ] `test_select_aggregate_functions` - SELECT with COUNT, SUM, AVG, etc.
- [ ] `test_select_json_functions` - SELECT with JSON functions
- [ ] `test_select_array_functions` - SELECT with array functions
- [ ] `test_select_system_catalogs` - SELECT from pg_catalog tables
- [ ] `test_select_information_schema` - SELECT from information_schema
- [ ] `test_explain_without_analyze` - EXPLAIN without ANALYZE
- [ ] `test_show_variable` - SHOW statements

#### 2.2 Safe SQL Driver - Blocked Operations
- [ ] `test_blocked_insert` - INSERT blocked
- [ ] `test_blocked_update` - UPDATE blocked
- [ ] `test_blocked_delete` - DELETE blocked
- [ ] `test_blocked_truncate` - TRUNCATE blocked
- [ ] `test_blocked_drop_table` - DROP TABLE blocked
- [ ] `test_blocked_drop_schema` - DROP SCHEMA blocked
- [ ] `test_blocked_drop_database` - DROP DATABASE blocked
- [ ] `test_blocked_create_table` - CREATE TABLE blocked
- [ ] `test_blocked_create_index` - CREATE INDEX blocked
- [ ] `test_blocked_alter_table` - ALTER TABLE blocked
- [ ] `test_blocked_grant` - GRANT blocked
- [ ] `test_blocked_revoke` - REVOKE blocked
- [ ] `test_blocked_create_user` - CREATE USER blocked
- [ ] `test_blocked_create_role` - CREATE ROLE blocked
- [ ] `test_blocked_copy` - COPY blocked
- [ ] `test_blocked_vacuum` - VACUUM blocked
- [ ] `test_blocked_analyze` - ANALYZE blocked
- [ ] `test_blocked_reindex` - REINDEX blocked
- [ ] `test_blocked_cluster` - CLUSTER blocked
- [ ] `test_blocked_set` - SET blocked
- [ ] `test_blocked_reset` - RESET blocked
- [ ] `test_blocked_begin` - BEGIN blocked
- [ ] `test_blocked_commit` - COMMIT blocked
- [ ] `test_blocked_rollback` - ROLLBACK blocked
- [ ] `test_blocked_savepoint` - SAVEPOINT blocked
- [ ] `test_blocked_prepare` - PREPARE blocked
- [ ] `test_blocked_execute_prepared` - EXECUTE prepared statement blocked
- [ ] `test_blocked_deallocate` - DEALLOCATE blocked
- [ ] `test_blocked_listen` - LISTEN blocked
- [ ] `test_blocked_notify` - NOTIFY blocked
- [ ] `test_blocked_explain_analyze` - EXPLAIN ANALYZE blocked
- [ ] `test_blocked_select_into` - SELECT INTO blocked
- [ ] `test_blocked_select_for_update` - SELECT FOR UPDATE blocked
- [ ] `test_blocked_select_for_share` - SELECT FOR SHARE blocked

#### 2.3 Safe SQL Driver - SQL Injection Prevention
- [ ] `test_injection_semicolon_statement` - Semicolon injection
- [ ] `test_injection_comment_escape` - Comment escape injection
- [ ] `test_injection_union_injection` - UNION injection
- [ ] `test_injection_stacked_queries` - Stacked queries
- [ ] `test_injection_encoded_characters` - Encoded character injection
- [ ] `test_injection_null_bytes` - Null byte injection
- [ ] `test_injection_quote_escape` - Quote escape injection

#### 2.4 Safe SQL Driver - Allowed Functions
- [ ] `test_allowed_aggregate_functions` - COUNT, SUM, AVG, MIN, MAX
- [ ] `test_allowed_string_functions` - String manipulation functions
- [ ] `test_allowed_math_functions` - Mathematical functions
- [ ] `test_allowed_date_functions` - Date/time functions
- [ ] `test_allowed_json_functions` - JSON functions
- [ ] `test_allowed_array_functions` - Array functions
- [ ] `test_allowed_type_cast_functions` - Type casting
- [ ] `test_allowed_privilege_functions` - has_*_privilege functions
- [ ] `test_allowed_system_info_functions` - current_user, version, etc.
- [ ] `test_allowed_pg_catalog_functions` - pg_* safe functions
- [ ] `test_allowed_hypopg_functions` - HypoPG extension functions
- [ ] `test_allowed_percentile_functions` - percentile_cont, percentile_disc, mode

#### 2.5 Safe SQL Driver - Blocked Functions
- [ ] `test_blocked_pg_sleep` - pg_sleep blocked
- [ ] `test_blocked_pg_read_file` - pg_read_file blocked
- [ ] `test_blocked_pg_write_file` - pg_write_file blocked
- [ ] `test_blocked_lo_import` - lo_import blocked
- [ ] `test_blocked_lo_export` - lo_export blocked
- [ ] `test_blocked_dblink` - dblink functions blocked
- [ ] `test_blocked_pg_execute_server_program` - pg_execute_server_program blocked
- [ ] `test_blocked_pg_cancel_backend` - pg_cancel_backend blocked
- [ ] `test_blocked_pg_terminate_backend` - pg_terminate_backend blocked
- [ ] `test_blocked_pg_reload_conf` - pg_reload_conf blocked
- [ ] `test_blocked_set_config` - set_config blocked

#### 2.6 Query Timeout
- [ ] `test_timeout_enforced` - Query timeout is enforced
- [ ] `test_timeout_custom_value` - Custom timeout value
- [ ] `test_timeout_cancels_query` - Long-running query is cancelled

#### 2.7 Connection Pool
- [ ] `test_pool_connection_success` - Successful connection
- [ ] `test_pool_connection_failure` - Connection failure handling
- [ ] `test_pool_reconnection` - Reconnection after failure
- [ ] `test_pool_multiple_queries` - Multiple concurrent queries
- [ ] `test_pool_cleanup_on_close` - Proper cleanup on close

### 3. MCP Tool Tests (`tests/unit/tools/`)

#### 3.1 list_schemas Tool
- [ ] `test_list_schemas_returns_all` - Returns all schemas
- [ ] `test_list_schemas_categorizes_system` - Categorizes system schemas
- [ ] `test_list_schemas_categorizes_user` - Categorizes user schemas
- [ ] `test_list_schemas_empty_database` - Empty database handling
- [ ] `test_list_schemas_error_handling` - Error handling

#### 3.2 list_objects Tool
- [ ] `test_list_objects_tables` - List tables
- [ ] `test_list_objects_views` - List views
- [ ] `test_list_objects_sequences` - List sequences
- [ ] `test_list_objects_extensions` - List extensions
- [ ] `test_list_objects_with_comments` - Objects with comments
- [ ] `test_list_objects_empty_schema` - Empty schema
- [ ] `test_list_objects_invalid_type` - Invalid object type
- [ ] `test_list_objects_nonexistent_schema` - Non-existent schema

#### 3.3 get_object_details Tool
- [ ] `test_get_object_details_table` - Table details
- [ ] `test_get_object_details_view` - View details
- [ ] `test_get_object_details_sequence` - Sequence details
- [ ] `test_get_object_details_extension` - Extension details
- [ ] `test_get_object_details_columns` - Column information
- [ ] `test_get_object_details_constraints` - Constraint information
- [ ] `test_get_object_details_indexes` - Index information
- [ ] `test_get_object_details_comments` - Table/column comments
- [ ] `test_get_object_details_nonexistent` - Non-existent object

#### 3.4 explain_query Tool
- [ ] `test_explain_simple_query` - Simple query explain
- [ ] `test_explain_complex_query` - Complex query explain
- [ ] `test_explain_with_analyze` - EXPLAIN ANALYZE
- [ ] `test_explain_without_analyze` - EXPLAIN without ANALYZE
- [ ] `test_explain_hypothetical_single_index` - Single hypothetical index
- [ ] `test_explain_hypothetical_multiple_indexes` - Multiple hypothetical indexes
- [ ] `test_explain_hypothetical_different_methods` - Different index methods (btree, hash, gin, gist)
- [ ] `test_explain_hypothetical_multicolumn_index` - Multicolumn index
- [ ] `test_explain_hypothetical_requires_hypopg` - HypoPG required message
- [ ] `test_explain_hypothetical_index_schema_validation` - Index schema validation
- [ ] `test_explain_bind_variables_pg16` - Bind variables in PG16+
- [ ] `test_explain_bind_variables_pg15` - Bind variables in PG15
- [ ] `test_explain_like_expressions` - LIKE expressions handling
- [ ] `test_explain_error_handling` - Error handling

#### 3.5 execute_sql Tool
- [ ] `test_execute_sql_select` - Execute SELECT
- [ ] `test_execute_sql_unrestricted_insert` - Execute INSERT in unrestricted mode
- [ ] `test_execute_sql_restricted_insert_blocked` - INSERT blocked in restricted mode
- [ ] `test_execute_sql_returns_results` - Returns query results
- [ ] `test_execute_sql_no_results` - No results handling
- [ ] `test_execute_sql_error_handling` - Error handling

#### 3.6 analyze_workload_indexes Tool
- [ ] `test_analyze_workload_dta_method` - DTA method
- [ ] `test_analyze_workload_llm_method` - LLM method
- [ ] `test_analyze_workload_custom_max_size` - Custom max index size
- [ ] `test_analyze_workload_no_slow_queries` - No slow queries
- [ ] `test_analyze_workload_pg_stat_statements_required` - pg_stat_statements required
- [ ] `test_analyze_workload_error_handling` - Error handling

#### 3.7 analyze_query_indexes Tool
- [ ] `test_analyze_query_indexes_single_query` - Single query analysis
- [ ] `test_analyze_query_indexes_multiple_queries` - Multiple queries
- [ ] `test_analyze_query_indexes_max_queries` - Max 10 queries limit
- [ ] `test_analyze_query_indexes_empty_list` - Empty query list
- [ ] `test_analyze_query_indexes_error_handling` - Error handling

#### 3.8 analyze_db_health Tool
- [ ] `test_analyze_db_health_all` - All health checks
- [ ] `test_analyze_db_health_index` - Index health only
- [ ] `test_analyze_db_health_connection` - Connection health only
- [ ] `test_analyze_db_health_vacuum` - Vacuum health only
- [ ] `test_analyze_db_health_sequence` - Sequence health only
- [ ] `test_analyze_db_health_replication` - Replication health only
- [ ] `test_analyze_db_health_buffer` - Buffer health only
- [ ] `test_analyze_db_health_constraint` - Constraint health only
- [ ] `test_analyze_db_health_multiple_types` - Multiple health types
- [ ] `test_analyze_db_health_invalid_type` - Invalid health type

#### 3.9 get_top_queries Tool
- [ ] `test_get_top_queries_resources` - Sort by resources
- [ ] `test_get_top_queries_mean_time` - Sort by mean time
- [ ] `test_get_top_queries_total_time` - Sort by total time
- [ ] `test_get_top_queries_custom_limit` - Custom limit
- [ ] `test_get_top_queries_pg_stat_statements_required` - pg_stat_statements required
- [ ] `test_get_top_queries_invalid_sort` - Invalid sort criteria

### 4. Database Health Tests (`tests/unit/database_health/`)

#### 4.1 Index Health
- [ ] `test_invalid_index_detection` - Detect invalid indexes
- [ ] `test_duplicate_index_detection` - Detect duplicate indexes
- [ ] `test_index_bloat_detection` - Detect bloated indexes
- [ ] `test_unused_index_detection` - Detect unused indexes
- [ ] `test_index_health_no_issues` - No issues found
- [ ] `test_index_health_edge_case_empty_table` - Empty table
- [ ] `test_index_health_edge_case_no_indexes` - No indexes

#### 4.2 Connection Health
- [ ] `test_connection_count` - Connection count
- [ ] `test_connection_utilization` - Connection utilization
- [ ] `test_idle_connections` - Idle connections
- [ ] `test_blocked_connections` - Blocked connections

#### 4.3 Vacuum Health
- [ ] `test_vacuum_transaction_wraparound` - Transaction ID wraparound
- [ ] `test_vacuum_dead_tuples` - Dead tuple detection
- [ ] `test_vacuum_autovacuum_status` - Autovacuum status

#### 4.4 Sequence Health
- [ ] `test_sequence_approaching_max` - Sequence approaching maximum
- [ ] `test_sequence_healthy` - Healthy sequences
- [ ] `test_sequence_integer_type` - Integer sequence
- [ ] `test_sequence_bigint_type` - Bigint sequence
- [ ] `test_sequence_quoted_names` - Quoted sequence names
- [ ] `test_sequence_schema_qualified` - Schema-qualified sequences

#### 4.5 Replication Health
- [ ] `test_replication_lag` - Replication lag detection
- [ ] `test_replication_slot_usage` - Replication slot usage
- [ ] `test_replication_no_replicas` - No replicas configured

#### 4.6 Buffer Health
- [ ] `test_buffer_cache_hit_rate_tables` - Table cache hit rate
- [ ] `test_buffer_cache_hit_rate_indexes` - Index cache hit rate
- [ ] `test_buffer_low_hit_rate_warning` - Low hit rate warning

#### 4.7 Constraint Health
- [ ] `test_invalid_constraints_detection` - Invalid constraint detection
- [ ] `test_valid_constraints` - Valid constraints

### 5. Explain Plan Tests (`tests/unit/explain/`)

#### 5.1 Plan Parsing
- [ ] `test_parse_seq_scan_plan` - Sequential scan plan
- [ ] `test_parse_index_scan_plan` - Index scan plan
- [ ] `test_parse_nested_loop_plan` - Nested loop join
- [ ] `test_parse_hash_join_plan` - Hash join
- [ ] `test_parse_merge_join_plan` - Merge join
- [ ] `test_parse_aggregate_plan` - Aggregate operations
- [ ] `test_parse_sort_plan` - Sort operations
- [ ] `test_parse_cte_plan` - CTE plans

#### 5.2 Hypothetical Indexes
- [ ] `test_hypothetical_index_creation` - Create hypothetical index
- [ ] `test_hypothetical_index_btree` - Btree index
- [ ] `test_hypothetical_index_hash` - Hash index
- [ ] `test_hypothetical_index_gin` - GIN index
- [ ] `test_hypothetical_index_gist` - GiST index
- [ ] `test_hypothetical_index_brin` - BRIN index
- [ ] `test_hypothetical_index_reset` - Reset hypothetical indexes

### 6. Index Tuning Tests (`tests/unit/index/`)

#### 6.1 DTA Algorithm
- [ ] `test_dta_candidate_generation` - Candidate index generation
- [ ] `test_dta_cost_estimation` - Cost estimation
- [ ] `test_dta_greedy_search` - Greedy search strategy
- [ ] `test_dta_space_constraint` - Space constraint handling
- [ ] `test_dta_improvement_threshold` - Improvement threshold

#### 6.2 LLM Optimization
- [ ] `test_llm_opt_requires_api_key` - Requires OPENAI_API_KEY
- [ ] `test_llm_opt_query_analysis` - Query analysis
- [ ] `test_llm_opt_index_suggestion` - Index suggestion

### 7. Top Queries Tests (`tests/unit/top_queries/`)

#### 7.1 Query Analysis
- [ ] `test_top_queries_by_total_time` - Sort by total time
- [ ] `test_top_queries_by_mean_time` - Sort by mean time
- [ ] `test_top_queries_resource_blend` - Resource blend sorting
- [ ] `test_top_queries_pg12_columns` - PG12 column names
- [ ] `test_top_queries_pg13_columns` - PG13+ column names
- [ ] `test_top_queries_stddev_time_pg12` - stddev_time in PG12
- [ ] `test_top_queries_stddev_exec_time_pg13` - stddev_exec_time in PG13+

### 8. Migration Tests (`tests/unit/migrations/`) - NEW

#### 8.1 Migration File Handling
- [ ] `test_migration_file_generation` - Generate migration file
- [ ] `test_migration_file_naming` - Migration file naming convention
- [ ] `test_migration_file_content` - Migration file content format
- [ ] `test_migration_file_ordering` - Migration ordering

#### 8.2 Migration Tracking
- [ ] `test_migration_table_creation` - Create migration tracking table
- [ ] `test_migration_status_pending` - Pending migrations
- [ ] `test_migration_status_applied` - Applied migrations
- [ ] `test_migration_history` - Migration history

#### 8.3 Migration Execution
- [ ] `test_migration_apply_single` - Apply single migration
- [ ] `test_migration_apply_multiple` - Apply multiple migrations
- [ ] `test_migration_apply_up_to` - Apply up to specific migration
- [ ] `test_migration_rollback` - Rollback migration
- [ ] `test_migration_rollback_multiple` - Rollback multiple migrations
- [ ] `test_migration_dry_run` - Dry run mode

#### 8.4 Schema Operations
- [ ] `test_migration_create_table` - Create table migration
- [ ] `test_migration_drop_table` - Drop table migration
- [ ] `test_migration_add_column` - Add column migration
- [ ] `test_migration_drop_column` - Drop column migration
- [ ] `test_migration_rename_column` - Rename column migration
- [ ] `test_migration_alter_column_type` - Alter column type
- [ ] `test_migration_add_index` - Add index migration
- [ ] `test_migration_drop_index` - Drop index migration
- [ ] `test_migration_add_constraint` - Add constraint migration
- [ ] `test_migration_drop_constraint` - Drop constraint migration
- [ ] `test_migration_add_foreign_key` - Add foreign key migration

#### 8.5 Schema Pull
- [ ] `test_schema_pull_tables` - Pull table definitions
- [ ] `test_schema_pull_columns` - Pull column definitions
- [ ] `test_schema_pull_indexes` - Pull index definitions
- [ ] `test_schema_pull_constraints` - Pull constraint definitions
- [ ] `test_schema_pull_sequences` - Pull sequence definitions
- [ ] `test_schema_pull_views` - Pull view definitions

### 9. TypeScript Generation Tests (`tests/unit/typescript/`) - NEW

#### 9.1 Type Generation
- [ ] `test_typescript_table_type` - Generate table type
- [ ] `test_typescript_column_types` - Column type mapping
- [ ] `test_typescript_nullable_columns` - Nullable columns
- [ ] `test_typescript_array_columns` - Array column types
- [ ] `test_typescript_json_columns` - JSON column types
- [ ] `test_typescript_enum_types` - Enum type generation
- [ ] `test_typescript_composite_types` - Composite types

#### 9.2 Schema Types
- [ ] `test_typescript_schema_namespace` - Schema namespace
- [ ] `test_typescript_multiple_schemas` - Multiple schemas
- [ ] `test_typescript_public_schema` - Public schema handling

### 10. Integration Tests (`tests/integration/`)

#### 10.1 Full Workflow Tests
- [ ] `test_integration_connect_analyze_health` - Connect and analyze health
- [ ] `test_integration_query_execution_restricted` - Restricted query execution
- [ ] `test_integration_query_execution_unrestricted` - Unrestricted query execution
- [ ] `test_integration_index_recommendation_workflow` - Index recommendation workflow
- [ ] `test_integration_migration_workflow` - Migration workflow (NEW)
- [ ] `test_integration_typescript_generation_workflow` - TypeScript generation workflow (NEW)

#### 10.2 Multi-Version Tests
- [ ] `test_integration_postgres_15` - PostgreSQL 15 compatibility
- [ ] `test_integration_postgres_16` - PostgreSQL 16 compatibility
- [ ] `test_integration_postgres_17` - PostgreSQL 17 compatibility

### 11. Edge Cases and Error Handling (`tests/unit/edge_cases/`)

#### 11.1 Unicode and Special Characters
- [ ] `test_unicode_table_names` - Unicode in table names
- [ ] `test_unicode_column_names` - Unicode in column names
- [ ] `test_unicode_data` - Unicode in data
- [ ] `test_special_chars_in_identifiers` - Special characters in identifiers
- [ ] `test_reserved_words_as_identifiers` - Reserved words as identifiers

#### 11.2 Large Data
- [ ] `test_large_result_set` - Large result set handling
- [ ] `test_large_query` - Large query handling
- [ ] `test_many_tables` - Many tables in schema
- [ ] `test_many_columns` - Many columns in table
- [ ] `test_deep_nesting` - Deeply nested queries

#### 11.3 Concurrent Access
- [ ] `test_concurrent_queries` - Concurrent query execution
- [ ] `test_concurrent_health_checks` - Concurrent health checks
- [ ] `test_connection_pool_exhaustion` - Connection pool exhaustion

#### 11.4 Error Recovery
- [ ] `test_recover_from_connection_loss` - Recover from connection loss
- [ ] `test_recover_from_timeout` - Recover from query timeout
- [ ] `test_recover_from_invalid_sql` - Recover from invalid SQL
- [ ] `test_partial_failure_handling` - Partial failure in multi-operation

## Test Implementation Priority

### Phase 1: Core Security (High Priority)
1. SQL Injection Prevention tests
2. Blocked Operations tests
3. Query Timeout tests

### Phase 2: Feature Coverage
1. MCP Tool tests
2. Database Health tests
3. Explain Plan tests

### Phase 3: New Features
1. Migration tests
2. TypeScript Generation tests

### Phase 4: Edge Cases
1. Unicode and Special Characters
2. Large Data handling
3. Concurrent Access
4. Error Recovery

## Running Tests

```bash
# Run all tests
pytest tests/

# Run unit tests only
pytest tests/unit/

# Run integration tests only
pytest tests/integration/

# Run with coverage
pytest --cov=postgres_mcp tests/

# Run specific test file
pytest tests/unit/sql/test_safe_sql.py

# Run tests matching pattern
pytest -k "test_blocked"
```
