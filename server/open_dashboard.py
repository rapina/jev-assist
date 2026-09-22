"""Open a one-use browser login without exposing the provider credential."""
import json
import urllib.request
import webbrowser
from pathlib import Path

from dashboard import ORIGIN
from local_runtime import local_secret


def login_url():
    origin = ORIGIN
    config = Path.home() / '.codex' / 'jev-client.json'
    if config.is_file():
        settings = json.loads(config.read_text(encoding='utf-8'))
        origin = settings['origin']
        return origin + '/dashboard'
    else:
        secret = local_secret()
    if not secret:
        raise RuntimeError("Jev local credential is unavailable; run the installer")
    request = urllib.request.Request(origin + "/dashboard/session", data=b"{}",
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + secret})
    with urllib.request.urlopen(request, timeout=5) as response:
        url = json.load(response)["url"]
        if not url.startswith(origin + '/dashboard/login?code='):
            raise ValueError('Unexpected dashboard login origin')
        return url


if __name__ == "__main__":
    try:
        webbrowser.open(login_url())
        print("Opened Jev dashboard.")
    except Exception as error:
        print("Dashboard could not open: " + type(error).__name__)
        raise SystemExit(1)
