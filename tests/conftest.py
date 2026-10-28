import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from demo_site.site import mock_transport  # noqa: E402
from robot_op.config import load_settings  # noqa: E402
from robot_op.models import Database  # noqa: E402
from robot_op.robot.pipeline import Robot  # noqa: E402

needs_tesseract = pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract не установлен")

DEMO_BASE = "http://demo.local/demo-site"


def demo_settings(tmp_path, **extra):
    env = {
        "ROBOT_APP_DATA_DIR": str(tmp_path),
        "ROBOT_APP_DATABASE_URL": f"sqlite:///{tmp_path}/robot.db",
        "ROBOT_SITE_BASE_URL": DEMO_BASE,
        "ROBOT_SITE_ALLOWED_HOSTS": "demo.local",
        "ROBOT_SITE_ALLOW_HTTP_HOSTS": "demo.local",
        "ROBOT_SITE_REQUESTS_PER_SECOND": "0",
        "ROBOT_SITE_RETRIES": "1",
        "ROBOT_SITE_BACKOFF_BASE": "0",
        "ROBOT_LLM_PROVIDER": "none",
    }
    env.update(extra)
    return load_settings(env=env)


@pytest.fixture
def settings(tmp_path):
    return demo_settings(tmp_path)


@pytest.fixture
def robot(settings):
    db = Database(settings.app.database_url)
    db.create_all()
    return Robot(settings, db, transport=mock_transport(), sleep=lambda s: None)
