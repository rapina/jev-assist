"""Open the local decision dashboard in a browser (no login step)."""
import json
import webbrowser
from pathlib import Path

from dashboard import ORIGIN


def dashboard_url():
    config = Path.home() / '.codex' / 'jev-client.json'
    if config.is_file():
        settings = json.loads(config.read_text(encoding='utf-8'))
        return settings['origin'] + '/dashboard'
    return ORIGIN + '/dashboard'


if __name__ == "__main__":
    try:
        webbrowser.open(dashboard_url())
        print("Opened Jev dashboard.")
    except Exception as error:
        print("Dashboard could not open: " + type(error).__name__)
        raise SystemExit(1)
