from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    source = path.read_text()
    if source.count(new) == 1 and source.count(old) == 0:
        return
    if source.count(old) != 1 or source.count(new) != 0:
        raise RuntimeError(f"expected one pgvector marker in {path}: {old!r}")
    path.write_text(source.replace(old, new, 1))


source_path = Path("src/postgres_mcp/pgvector_diagnostics.py")
old_columns_sql = '''_COLUMNS_SQL: LiteralString = """
SELECT
    relation_namespace.nspname AS schema_name,
    relation.relname AS relation_name,
    attribute.attname AS column_name,
    type.typname AS type_name,
    pg_catalog.format_type(attribute.atttypid, attribute.atttypmod) AS formatted_type,
    NOT attribute.attnotnull AS nullable
FROM pg_catalog.pg_attribute AS attribute
JOIN pg_catalog.pg_class AS relation ON relation.oid = attribute.attrelid
JOIN pg_catalog.pg_namespace AS relation_namespace ON relation_namespace.oid = relation.relnamespace
JOIN pg_catalog.pg_type AS type ON type.oid = attribute.atttypid
JOIN pg_catalog.pg_depend AS dependency
  ON dependency.classid = 'pg_catalog.pg_type'::pg_catalog.regclass
 AND dependency.objid = type.oid
 AND dependency.objsubid = 0
 AND dependency.refclassid = 'pg_catalog.pg_extension'::pg_catalog.regclass
 AND dependency.refobjid = %s
 AND dependency.deptype = 'e'
WHERE attribute.attnum > 0
  AND NOT attribute.attisdropped
  AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
ORDER BY relation_namespace.nspname, relation.relname, attribute.attnum
"""
'''
new_columns_sql = '''_COLUMNS_SQL: LiteralString = """
WITH extension_members AS (
    SELECT dependency.classid, dependency.objid
    FROM pg_catalog.pg_depend AS dependency
    WHERE dependency.refclassid = 'pg_catalog.pg_extension'::pg_catalog.regclass
      AND dependency.refobjid = %s
      AND dependency.deptype = 'e'
),
extension_index_keys AS (
    SELECT index.indrelid, key.attribute_number
    FROM pg_catalog.pg_index AS index
    JOIN pg_catalog.pg_class AS index_relation ON index_relation.oid = index.indexrelid
    JOIN pg_catalog.pg_am AS access_method ON access_method.oid = index_relation.relam
    CROSS JOIN LATERAL unnest(index.indkey::smallint[], index.indclass::oid[])
      AS key(attribute_number, operator_class_oid)
    WHERE key.attribute_number > 0
      AND (
          EXISTS (
              SELECT 1
              FROM extension_members AS member
              WHERE member.classid = 'pg_catalog.pg_am'::pg_catalog.regclass
                AND member.objid = access_method.oid
          )
          OR EXISTS (
              SELECT 1
              FROM extension_members AS member
              WHERE member.classid = 'pg_catalog.pg_opclass'::pg_catalog.regclass
                AND member.objid = key.operator_class_oid
          )
      )
)
SELECT
    relation_namespace.nspname AS schema_name,
    relation.relname AS relation_name,
    attribute.attname AS column_name,
    type.typname AS type_name,
    pg_catalog.format_type(attribute.atttypid, attribute.atttypmod) AS formatted_type,
    NOT attribute.attnotnull AS nullable
FROM pg_catalog.pg_attribute AS attribute
JOIN pg_catalog.pg_class AS relation ON relation.oid = attribute.attrelid
JOIN pg_catalog.pg_namespace AS relation_namespace ON relation_namespace.oid = relation.relnamespace
JOIN pg_catalog.pg_type AS type ON type.oid = attribute.atttypid
WHERE attribute.attnum > 0
  AND NOT attribute.attisdropped
  AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND (
      EXISTS (
          SELECT 1
          FROM extension_members AS member
          WHERE member.classid = 'pg_catalog.pg_type'::pg_catalog.regclass
            AND member.objid = type.oid
      )
      OR (
          type.typname = 'bit'
          AND EXISTS (
              SELECT 1
              FROM extension_index_keys AS key
              WHERE key.indrelid = attribute.attrelid
                AND key.attribute_number = attribute.attnum
          )
      )
  )
ORDER BY relation_namespace.nspname, relation.relname, attribute.attnum
"""
'''
replace_once(source_path, old_columns_sql, new_columns_sql)

old_column_parser = '''    formatted_type = _text(row, "formatted_type", maximum=256)
    return PgvectorColumn(
        schema=_text(row, "schema_name", maximum=63),
        relation=_text(row, "relation_name", maximum=63),
        column=_text(row, "column_name", maximum=63),
        type_name=type_name,
        formatted_type=formatted_type,
        dimensions=parse_dimensions(formatted_type),
        nullable=_boolean(row, "nullable"),
    )
'''
new_column_parser = '''    formatted_type = _text(row, "formatted_type", maximum=256)
    dimensions = parse_dimensions(formatted_type)
    match = _FORMATTED_TYPE.fullmatch(formatted_type.strip())
    if match is None or match.group(1) != type_name:
        raise PgvectorCatalogError("pgvector column type must match its formatted type")
    return PgvectorColumn(
        schema=_text(row, "schema_name", maximum=63),
        relation=_text(row, "relation_name", maximum=63),
        column=_text(row, "column_name", maximum=63),
        type_name=type_name,
        formatted_type=formatted_type,
        dimensions=dimensions,
        nullable=_boolean(row, "nullable"),
    )
'''
replace_once(source_path, old_column_parser, new_column_parser)

validation_path = Path("tests/unit/extensions/test_pgvector_catalog_validation.py")
validation_marker = '''def test_unsupported_extension_owned_column_type_is_rejected() -> None:
    malformed = column_row()
    malformed["type_name"] = "future_vector"

    with pytest.raises(PgvectorCatalogError, match="not supported"):
        snapshot(columns=[malformed])
'''
validation_replacement = validation_marker + '''\n\ndef test_column_type_must_match_formatted_catalog_type() -> None:
    malformed = column_row()
    malformed["formatted_type"] = "halfvec(3)"

    with pytest.raises(PgvectorCatalogError, match="must match"):
        snapshot(columns=[malformed])
'''
replace_once(validation_path, validation_marker, validation_replacement)

integration_path = Path("tests/integration/extensions/test_pgvector_diagnostics_integration.py")
replace_once(
    integration_path,
    '''                        embedding vector(3) NOT NULL,
                        compact halfvec(3)
''',
    '''                        embedding vector(3) NOT NULL,
                        compact halfvec(3),
                        fingerprint bit(8)
''',
)
replace_once(
    integration_path,
    '''                await cursor.execute(
                    """
                    CREATE INDEX items_embedding_hnsw_idx
                    ON pgvector_contract.items
                    USING hnsw (embedding vector_cosine_ops)
                    WITH (m = 8, ef_construction = 32)
                    """
                )
''',
    '''                await cursor.execute(
                    """
                    CREATE INDEX items_embedding_hnsw_idx
                    ON pgvector_contract.items
                    USING hnsw (embedding vector_cosine_ops)
                    WITH (m = 8, ef_construction = 32)
                    """
                )
                await cursor.execute(
                    """
                    CREATE INDEX items_fingerprint_hnsw_idx
                    ON pgvector_contract.items
                    USING hnsw (fingerprint bit_hamming_ops)
                    """
                )
''',
)
replace_once(
    integration_path,
    '''        assert columns[("pgvector_contract", "items", "compact")].type_name == "halfvec"
        assert columns[("pgvector_contract", "items", "compact")].dimensions == 3

        index = next(item for item in first.indexes if item.name == "items_embedding_hnsw_idx")
''',
    '''        assert columns[("pgvector_contract", "items", "compact")].type_name == "halfvec"
        assert columns[("pgvector_contract", "items", "compact")].dimensions == 3
        assert columns[("pgvector_contract", "items", "fingerprint")].type_name == "bit"
        assert columns[("pgvector_contract", "items", "fingerprint")].dimensions == 8

        index = next(item for item in first.indexes if item.name == "items_embedding_hnsw_idx")
''',
)
replace_once(
    integration_path,
    '''        assert index.ready is True
        assert first.findings == ()
''',
    '''        assert index.ready is True

        bit_index = next(item for item in first.indexes if item.name == "items_fingerprint_hnsw_idx")
        assert bit_index.access_method == "hnsw"
        assert bit_index.operator_classes == ("bit_hamming_ops",)
        assert first.findings == ()
''',
)

Path("scripts/apply_pgvector_bit_columns.py").unlink()
Path(".github/workflows/publish-pgvector-bit-columns.yml").unlink()
