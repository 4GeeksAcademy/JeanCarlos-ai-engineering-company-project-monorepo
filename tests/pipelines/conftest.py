import pytest

from tests.memory_fixtures import isolate_memory


@pytest.fixture(autouse=True)
def isolated_memory(monkeypatch, tmp_path):
    return isolate_memory(monkeypatch, tmp_path)
