import unittest

from terminusdb.db.oracle.tdb_engine_impl import OracleEngine
from terminusdb.db.tdb_engines import ColumnDefinition, IndexDefinition, TableDefinition


class OraclePrimaryKeySelectionTests(unittest.TestCase):
    def test_prefers_unique_not_null_with_highest_distinct_keys(self) -> None:
        indexes = {
            "IDX_A": {"columns": ["COL_A"], "unique": True, "distinct_keys": 10},
            "IDX_B": {"columns": ["COL_B"], "unique": True, "distinct_keys": 20},
        }
        metadata = {
            "col_a": ColumnDefinition(name="col_a", data_type="number", nullable=False),
            "col_b": ColumnDefinition(name="col_b", data_type="number", nullable=False),
        }
        result = OracleEngine._choose_best_index_for_primary_key(indexes, metadata)
        self.assertEqual(result, ("col_b",))

    def test_not_null_unique_beats_nullable_even_with_lower_distinct(self) -> None:
        indexes = {
            "IDX_NULLABLE": {
                "columns": ["COL_B"],
                "unique": True,
                "distinct_keys": 100,
            },
            "IDX_NOT_NULL": {
                "columns": ["COL_A"],
                "unique": True,
                "distinct_keys": 10,
            },
        }
        metadata = {
            "col_a": ColumnDefinition(name="col_a", data_type="number", nullable=False),
            "col_b": ColumnDefinition(name="col_b", data_type="number", nullable=True),
        }
        result = OracleEngine._choose_best_index_for_primary_key(indexes, metadata)
        self.assertEqual(result, ("col_a",))

    def test_falls_back_to_unique_highest_distinct_when_all_nullable(self) -> None:
        indexes = {
            "IDX_LOW": {
                "columns": ["COL_A"],
                "unique": True,
                "distinct_keys": 5,
            },
            "IDX_HIGH": {
                "columns": ["COL_B"],
                "unique": True,
                "distinct_keys": 15,
            },
        }
        metadata = {
            "col_a": ColumnDefinition(name="col_a", data_type="number", nullable=True),
            "col_b": ColumnDefinition(name="col_b", data_type="number", nullable=True),
        }
        result = OracleEngine._choose_best_index_for_primary_key(indexes, metadata)
        self.assertEqual(result, ("col_b",))

    def test_uses_non_unique_when_no_unique_indexes(self) -> None:
        indexes = {
            "IDX_LOW": {
                "columns": ["COL_A"],
                "unique": False,
                "distinct_keys": 10,
            },
            "IDX_HIGH": {
                "columns": ["COL_B"],
                "unique": False,
                "distinct_keys": 30,
            },
        }
        metadata = {
            "col_a": ColumnDefinition(name="col_a", data_type="number", nullable=False),
            "col_b": ColumnDefinition(name="col_b", data_type="number", nullable=False),
        }
        result = OracleEngine._choose_best_index_for_primary_key(indexes, metadata)
        self.assertEqual(result, ("col_b",))

    def test_ignores_indexes_without_matching_columns(self) -> None:
        indexes = {
            "IDX_FUNC": {
                "columns": ["SYS_NC00005$"],
                "unique": True,
                "distinct_keys": 100,
            },
            "IDX_VALID": {
                "columns": ["COL_A"],
                "unique": True,
                "distinct_keys": 10,
            },
        }
        metadata = {
            "col_a": ColumnDefinition(name="col_a", data_type="number", nullable=False),
        }
        result = OracleEngine._choose_best_index_for_primary_key(indexes, metadata)
        self.assertEqual(result, ("col_a",))

    def test_returns_none_when_no_candidates(self) -> None:
        indexes = {
            "IDX_FUNC": {
                "columns": ["SYS_NC00005$"],
                "unique": True,
                "distinct_keys": 100,
            }
        }
        metadata: dict[str, ColumnDefinition] = {}
        result = OracleEngine._choose_best_index_for_primary_key(indexes, metadata)
        self.assertIsNone(result)


class OracleIndexPreparationTests(unittest.TestCase):
    def test_appends_process_date_to_non_primary_indexes(self) -> None:
        table = TableDefinition(
            owner="A",
            name="B",
            columns=(
                ColumnDefinition(name="col_a", data_type="number", nullable=False),
                ColumnDefinition(name="col_b", data_type="number"),
                ColumnDefinition(name="tdd_process_date", data_type="date", nullable=False),
            ),
            primary_key=("col_a",),
            indexes=(IndexDefinition(name="idx_col_b", columns=("col_b",)),),
        )
        result = OracleEngine._prepare_desired_indexes(table)
        self.assertEqual(
            result,
            {"IDX_COL_B": (("COL_B", "TDD_PROCESS_DATE"), False)},
        )

    def test_skips_process_date_for_primary_key_index(self) -> None:
        table = TableDefinition(
            owner="A",
            name="B",
            columns=(
                ColumnDefinition(name="col_a", data_type="number", nullable=False),
                ColumnDefinition(name="tdd_process_date", data_type="date", nullable=False),
            ),
            primary_key=("col_a",),
            indexes=(IndexDefinition(name="idx_pk_dup", columns=("col_a",)),),
        )
        result = OracleEngine._prepare_desired_indexes(table)
        self.assertEqual(result, {"IDX_PK_DUP": (("COL_A",), False)})

    def test_uses_tdb_process_date_when_tdd_missing(self) -> None:
        table = TableDefinition(
            owner="A",
            name="B",
            columns=(
                ColumnDefinition(name="col_b", data_type="number"),
                ColumnDefinition(name="tdb_process_date", data_type="date", nullable=False),
            ),
            primary_key=None,
            indexes=(IndexDefinition(name="idx_col_b", columns=("col_b",)),),
        )
        result = OracleEngine._prepare_desired_indexes(table)
        self.assertEqual(
            result,
            {"IDX_COL_B": (("COL_B", "TDB_PROCESS_DATE"), False)},
        )

    def test_leaves_indexes_unchanged_without_process_date_column(self) -> None:
        table = TableDefinition(
            owner="A",
            name="B",
            columns=(
                ColumnDefinition(name="col_a", data_type="number"),
                ColumnDefinition(name="col_b", data_type="number"),
            ),
            primary_key=None,
            indexes=(IndexDefinition(name="idx_col_b", columns=("col_b",)),),
        )
        result = OracleEngine._prepare_desired_indexes(table)
        self.assertEqual(result, {"IDX_COL_B": (("COL_B",), False)})

if __name__ == "__main__":
    unittest.main()
