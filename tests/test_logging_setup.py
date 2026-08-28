"""
Config must configure logging before it warns about anything.

A module-level logging.warning() installs a root handler at WARNING level, and
basicConfig() is a no-op once a handler exists — so warning first silently
throws away every INFO line the bot emits. That is how production ended up with
a three-line log.

These run in a subprocess: pytest installs its own logging handlers, which would
mask the very behaviour under test.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

PROBE = """
import logging, sys
sys.path.insert(0, {root!r})
from config.settings import Config
Config()
logging.getLogger("probe").info("INFO-LINE-EMITTED")
print("ROOT_LEVEL=" + logging.getLevelName(logging.getLogger().level))
"""


def _run_probe(env_extra):
    env = {
        "PATH": "/usr/bin:/bin",
        "TELEGRAM_BOT_TOKEN": "x",
        "TELEGRAM_CHANNEL_ID": "-100",
        "MY_ID": "1",
        # Nothing should read a real .env during the probe.
        "DOTENV_DISABLED": "1",
    }
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-c", PROBE.format(root=str(REPO_ROOT))],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT), timeout=60,
    )


def test_info_survives_the_no_vk_token_warning():
    """The exact production case: no VK token, so Config warns during startup."""
    result = _run_probe({"VK_ACCESS_TOKEN": "", "VK_GROUP": "", "VK_APP_ID": "", "OPENAI_KEY": ""})

    assert "ROOT_LEVEL=INFO" in result.stdout, (
        f"INFO logging was suppressed by an early warning.\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr[-500:]!r}"
    )
    assert "INFO-LINE-EMITTED" in result.stderr, "INFO records never reached a handler"


def test_info_also_works_when_nothing_warns():
    result = _run_probe({
        "VK_ACCESS_TOKEN": "tok", "VK_GROUP": "1", "VK_APP_ID": "1", "OPENAI_KEY": "k",
    })
    assert "ROOT_LEVEL=INFO" in result.stdout
