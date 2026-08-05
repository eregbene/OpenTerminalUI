import pytest

pytest.importorskip("mibian")

from backend.main import livez


def test_liveness_endpoint_is_lightweight():
    assert livez()["status"] == "ok"
