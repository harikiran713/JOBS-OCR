from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

TOKEN = "test-token"


@pytest.fixture(scope="session")
def fixtures_dir(tmp_path_factory) -> dict[str, Path]:  # noqa: ANN001
    from tests.fixtures.make_fixtures import build_all

    return build_all(tmp_path_factory.mktemp("pdfs"))


@pytest.fixture(scope="session")
def data_dir(tmp_path_factory) -> Path:  # noqa: ANN001
    return tmp_path_factory.mktemp("appdata")


@pytest.fixture(scope="session")
def client(data_dir):  # noqa: ANN001, ANN201
    os.environ["MMI_API_TOKEN"] = TOKEN
    from fastapi.testclient import TestClient

    from mmi.api.app import create_app
    from mmi.config import set_paths
    from mmi.db.session import init_engine

    paths = set_paths(data_dir)
    init_engine(paths.db, paths.backups)
    app = create_app(start_engine=True)
    with TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"}) as c:
        yield c


def wait_idle(client, project_id: str, timeout: float = 240) -> dict:  # noqa: ANN001
    deadline = time.time() + timeout
    status = {}
    while time.time() < deadline:
        status = client.get(f"/api/projects/{project_id}/processing/status").json()
        if status["state"] == "idle":
            return status
        time.sleep(0.5)
    raise AssertionError(f"Processing did not finish: {status}")
