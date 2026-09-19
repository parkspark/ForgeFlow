import pytest


@pytest.fixture(autouse=True)
def qt_application(qapp):
    """All UI tests share the session's QApplication."""
    return qapp
