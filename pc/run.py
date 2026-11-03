"""Grab for PC: runs Grab on this computer and opens it in the browser.

Downloads go through your own internet connection, so YouTube works and videos come in 1080p.
Works both as a plain Python script and as the installed GrabSetup.exe app.
It closes itself after 90 idle minutes.
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
import zipfile

FROZEN = getattr(sys, "frozen", False)
HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))  # bundled files
DATA = os.path.join(os.environ.get("LOCALAPPDATA", HERE), "Grab") if FROZEN else HERE  # writable
os.makedirs(DATA, exist_ok=True)
PORT = 8765
URL = f"http://127.0.0.1:{PORT}/"
IDLE_LIMIT = 90 * 60
NO_WINDOW = 0x08000000
UPDATE_EVERY = 2 * 86400

# No console window: send output to a log file
log = open(os.path.join(DATA, "grab.log"), "a", encoding="utf-8", buffering=1)
sys.stdout = sys.stderr = log


def already_running() -> bool:
    try:
        return bool(json.load(urllib.request.urlopen(URL + "api/health", timeout=2)).get("local"))
    except Exception:
        return False


def open_when_ready():
    for _ in range(80):
        if already_running():
            webbrowser.open(URL)
            return
        time.sleep(0.5)


if already_running():
    webbrowser.open(URL)
    sys.exit()


# ---------- downloader engine (yt-dlp) that can update itself ----------
# Sites change often, so the installed app keeps its own copy of yt-dlp in
# %LOCALAPPDATA%\Grab\engine and refreshes it from PyPI every 2 days.
ENGINE = os.path.join(DATA, "engine")
ENGINE_PKGS = ("yt-dlp", "yt-dlp-ejs")


def engine_dir():
    try:
        with open(os.path.join(ENGINE, "current.txt"), encoding="utf-8") as f:
            d = os.path.join(ENGINE, f.read().strip())
        return d if os.path.isdir(os.path.join(d, "yt_dlp")) else None
    except OSError:
        return None


class EngineFinder:
    """Load yt_dlp from the updatable engine folder instead of the copy inside the app."""

    def __init__(self, path):
        self.path = path

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] not in ("yt_dlp", "yt_dlp_ejs"):
            return None
        search = path if path else [self.path]
        return importlib.machinery.PathFinder.find_spec(name, search)


def latest_wheel(pkg):
    meta = json.load(urllib.request.urlopen(f"https://pypi.org/pypi/{pkg}/json", timeout=20))
    ver = meta["info"]["version"]
    for f in meta["releases"][ver]:
        if f["packagetype"] == "bdist_wheel" and f["filename"].endswith("py3-none-any.whl"):
            return ver, f["url"]
    raise RuntimeError(f"no wheel for {pkg}")


def update_engine():
    stamp = os.path.join(DATA, ".engine-checked")
    if os.path.exists(stamp) and time.time() - os.path.getmtime(stamp) < UPDATE_EVERY:
        return
    try:
        if not FROZEN:  # plain Python install: just use pip
            subprocess.run([sys.executable.replace("pythonw.exe", "python.exe"), "-m", "pip", "install", "-q",
                            "--disable-pip-version-check", "-U", "yt-dlp[default,curl-cffi]"],
                           stdout=log, stderr=log, creationflags=NO_WINDOW)
        else:
            import yt_dlp.version as v
            ver, _ = latest_wheel("yt-dlp")
            if ver != v.__version__:
                new = os.path.join(ENGINE, ver)
                tmp = new + ".part"
                shutil.rmtree(tmp, ignore_errors=True)
                os.makedirs(tmp)
                for pkg in ENGINE_PKGS:
                    _, url = latest_wheel(pkg)
                    whl = os.path.join(tmp, "pkg.whl")
                    urllib.request.urlretrieve(url, whl)
                    with zipfile.ZipFile(whl) as z:
                        z.extractall(tmp)
                    os.remove(whl)
                shutil.rmtree(new, ignore_errors=True)
                os.rename(tmp, new)
                with open(os.path.join(ENGINE, "current.txt"), "w", encoding="utf-8") as f:
                    f.write(ver)
                print("Engine updated to", ver, "(used from next start)")
        open(stamp, "w").close()
    except Exception as e:  # noqa: BLE001
        print("Engine update failed:", e)


if FROZEN:
    d = engine_dir()
    if d:
        sys.meta_path.insert(0, EngineFinder(d))
    deno = os.path.join(HERE, "deno.exe")
    if os.path.exists(deno):
        os.environ["GRAB_DENO"] = deno
    ff = os.path.join(HERE, "ffmpeg.exe")
    if os.path.exists(ff):
        os.environ["IMAGEIO_FFMPEG_EXE"] = ff

os.environ["GRAB_LOCAL"] = "1"
os.environ["GRAB_MAX_HEIGHT"] = "1080"
os.chdir(DATA)
sys.path.insert(0, HERE)

import app as grab  # noqa: E402
import uvicorn  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

last_seen = time.time()


@grab.app.middleware("http")
async def touch(request, call_next):
    global last_seen
    last_seen = time.time()
    response = await call_next(request)
    last_seen = time.time()
    return response


def idle_watch():
    while True:
        time.sleep(60)
        if time.time() - last_seen > IDLE_LIMIT:
            print("Idle, closing Grab.")
            os._exit(0)


grab.app.mount("/", StaticFiles(directory=os.path.join(HERE, "public"), html=True), name="static")
threading.Thread(target=update_engine, daemon=True).start()
threading.Thread(target=open_when_ready, daemon=True).start()
threading.Thread(target=idle_watch, daemon=True).start()
print(time.strftime("%Y-%m-%d %H:%M"), "Grab started on", URL, "engine", engine_dir() or "built-in")
uvicorn.run(grab.app, host="127.0.0.1", port=PORT, log_level="warning", log_config=None)
