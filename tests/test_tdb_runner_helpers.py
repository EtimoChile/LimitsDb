from terminusdb.core import tdb_runner
from terminusdb.core.tdb_status import Status

def test_build_dependency_graph_and_layers():
  rows = [{
      "source_owner": "A",
      "table_name": "T1",
      "referencing_tables": "A.T2 r"
  }, {
      "source_owner": "A",
      "table_name": "T2",
      "referencing_tables": ""
  }, ]
  graph = tdb_runner._build_dependency_graph(rows)
  assert graph[("A", "T1")] == set()
  assert graph[("A", "T2")] == {("A", "T1")}
  layers = tdb_runner._compute_plan_layers(graph)
  assert layers[0] == [("A", "T1")]
  assert layers[1] == [("A", "T2")]

def test_cycle_detection():
  rows = [{
      "source_owner": "A",
      "table_name": "T1",
      "referencing_tables": "A.T2 r"
  }, {
      "source_owner": "A",
      "table_name": "T2",
      "referencing_tables": "A.T1 r"
  }, ]
  graph = tdb_runner._build_dependency_graph(rows)
  try:
    tdb_runner._compute_plan_layers(graph)
  except ValueError as exc:
    assert "Cyclic" in str(exc)
  else:
    raise AssertionError("cycle not detected")

def test_append_unique_and_get_next_ready():
  other_cols = [{"name": "x"}]
  tdb_runner._append_unique_name(other_cols, {"name": "x"})
  assert len(other_cols) == 1
  tdb_runner._append_unique_name(other_cols, {"name": "y"})
  assert len(other_cols) == 2

  # yapf: disable
  tables_config = {
      ("A", "T1"): {
          "skip": False,
          "conds": [{"ctl_status": None}],
          "referencing_tables": []},
      ("A", "T2"): {
          "skip": False,
          "conds": [{"ctl_status": Status.TABLE_END}],
          "referencing_tables": []},
  }
  # yapf: enable
  ready = tdb_runner.get_next_ready_table(tables_config, set())
  assert ready[0:2] == ("A", "T1")

# yapf: disable
def test_collect_privilege_targets():
  tables_config = {
      ("A", "T1"): {
          "skip": False,
          "conds": [{
              "source_owner": "A",
              "history_owner": "H",
              "table_name": "T1"}]},
      ("A", "T2"): {
          "skip": True,
          "conds": [{
              "source_owner": "A",
              "history_owner": "H",
              "table_name": "T2"}]},
  }
  # yapf: enable
  targets = tdb_runner._collect_privilege_targets(tables_config, "source_owner")
  assert targets == [("A", "T1")]
