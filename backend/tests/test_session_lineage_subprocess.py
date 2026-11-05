"""Process-level coverage for first-boot session lineage atomicity."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from backend.core.db_manager import DatabaseManager
from backend.models.database import SessionLineage, set_db_manager


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_INIT_WORKER = r'''
import json
import sys

db_path = sys.argv[1]
from backend.core import session as session_module
from backend.core.db_manager import DatabaseManager
from backend.models.database import set_db_manager

dbm = DatabaseManager(db_path)
set_db_manager(dbm)
with dbm.get_session():
    pass
print(json.dumps({"id": session_module.get_session_id()}))
'''


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "subprocess-lineage.db"
    dbm = DatabaseManager(str(path))
    with dbm.get_session():
        pass
    dbm.engine.dispose()
    return str(path)


def test_subprocess_first_boot_races_to_one_id_and_one_lineage_row(db_path, tmp_path):
    script = tmp_path / "worker.py"
    script.write_text(DB_INIT_WORKER, encoding="utf-8")

    processes = []
    for _ in range(6):
        process = subprocess.Popen(
            [sys.executable, str(script), db_path],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT), "PYTHONIOENCODING": "utf-8"},
        )
        processes.append(process)

    stdout_values = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, stderr
        stdout_values.append(stdout)

    ids = [ (lambda output: __import__("json").loads(output)["id"])(value) for value in stdout_values ]
    assert len(set(ids)) == 1

    dbm = DatabaseManager(db_path)
    set_db_manager(dbm)
    try:
        with dbm.get_session() as db:
            lineage_rows = db.query(SessionLineage).count()
            trusted_ids = [row.session_id for row in db.query(SessionLineage).all()]
    finally:
        dbm.engine.dispose()
    assert lineage_rows == 1
    assert trusted_ids == ids[:1]