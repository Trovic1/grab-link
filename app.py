"""Grab web: find the media behind a social link and stream it back as a file."""
import asyncio
import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import tempfile
import urllib.parse

import httpx
import imageio_ffmpeg
import yt_dlp
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
SECRET = (os.environ.get("GRAB_SECRET") or secrets.token_hex(32)).encode()
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
MAX_H = int(os.environ.get("GRAB_MAX_HEIGHT", "720"))  # PC version uses 1080
LOCAL = os.environ.get("GRAB_LOCAL") == "1"
IMG_EXT = ("jpg", "jpeg", "png", "webp", "gif")
VID_EXT = ("mp4", "mov", "webm", "m4v", "mkv")
AUD_EXT = ("mp3", "m4a", "aac", "wav", "ogg", "opus", "flac")


def _cookie_file():
    """Optional cookies (Netscape cookies.txt) from the GRAB_COOKIES env var."""
    raw = os.environ.get("GRAB_COOKIES", "").strip()
    if not raw:
        return None
    if not raw.startswith("#") and "\t" not in raw:
        raw = base64.b64decode(raw).decode()
    path = os.path.join(tempfile.gettempdir(), "grab_cookies.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(raw)
    return path


COOKIES = _cookie_file()


# ---------- signed tokens (so /api/file only fetches what /api/info found) ----------

def sign(payload: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    sig = hmac.new(SECRET, body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{sig}"


def unsign(token: str) -> dict:
    try:
        body, sig = token.rsplit(".", 1)
    except ValueError:
        raise HTTPException(400, "Bad token")
    good = hmac.new(SECRET, body.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, good):
        raise HTTPException(403, "This link expired, tap Grab again")
    return json.loads(base64.urlsafe_b64decode(body))


def safe_name(title: str, fallback: str = "grab") -> str:
    name = re.sub(r"https?://\S+", "", title or "")
    name = re.sub(r"[\\/:*?\"<>|\r\n\t]+", " ", name).strip(" -–·")
    name = re.sub(r"\s+", " ", name)[:80].strip(" .")
    return name or fallback


# ---------- extraction ----------

def _headers_for(ydl, fmt: dict) -> dict:
    h = dict(fmt.get("http_headers") or {})
    try:
        ck = ydl.cookiejar.get_cookie_header(fmt["url"])
        if ck:
            h["Cookie"] = ck
    except Exception:
        pass
    return h


def _is_stream(fmt: dict) -> bool:
    return str(fmt.get("protocol", "")).startswith(("m3u8", "http_dash"))


def _ytdlp(url: str, audio: bool) -> dict:
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True,
        "socket_timeout": 20, "retries": 2,
        "format": "ba/b" if audio else
        f"bv*[vcodec^=avc][height<={MAX_H}]+ba[ext=m4a]/b[ext=mp4][acodec!=none]/bv*[height<={MAX_H}]+ba/b",
    }
    if COOKIES:
        opts["cookiefile"] = COOKIES
    if os.environ.get("GRAB_DENO"):  # PC app ships Deno, which YouTube extraction needs
        opts["js_runtimes"] = {"deno": {"path": os.environ["GRAB_DENO"]}}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        entries = info.get("entries")
        if entries:  # multi-video posts: take every item
            items = [e for e in entries if e]
        else:
            items = [info]
        out = []
        for i, it in enumerate(items):
            fmts = it.get("requested_formats") or [it]
            if not fmts[0].get("url"):
                continue
            title = it.get("title") or info.get("title") or "grab"
            if len(items) > 1:
                title = f"{title} ({i + 1})"
            urls = [f["url"] for f in fmts]
            heads = [_headers_for(ydl, f) for f in fmts]
            ext = (fmts[0].get("ext") or "").lower()
            has_video = any((f.get("vcodec") or "none") != "none" for f in fmts) or ext in VID_EXT
            if audio:
                kind, out_ext = "mp3", "mp3"
            elif ext in IMG_EXT and not has_video:
                kind, out_ext = "direct", ext
            elif len(fmts) > 1 or _is_stream(fmts[0]):
                kind, out_ext = "remux", "mp4"
            else:
                kind, out_ext = "direct", ext or "mp4"
            out.append({
                "title": title,
                "thumb": it.get("thumbnail") or info.get("thumbnail"),
                "media": "audio" if audio else ("video" if has_video else "image"),
                "duration": it.get("duration"),
                "job": {"k": kind, "u": urls, "h": heads, "n": f"{safe_name(title)}.{out_ext}",
                        "t": title, "a": it.get("uploader") or info.get("uploader")},
            })
        if not out:
            raise yt_dlp.utils.DownloadError("No media found")
        return {
            "title": info.get("title"),
            "site": info.get("extractor_key") or info.get("extractor"),
            "uploader": info.get("uploader"),
            "items": out,
        }


async def _fallback(url: str, audio: bool) -> dict | None:
    """Photos and plain media links that yt-dlp skips (X photo posts, Pinterest images, og tags)."""
    if audio:
        return None
    path = urllib.parse.urlparse(url).path.lower()
    ext = path.rsplit(".", 1)[-1] if "." in path else ""
    if ext in IMG_EXT + VID_EXT:
        media = "image" if ext in IMG_EXT else "video"
        return _single(url, url, media, f"grab.{ext}", {"User-Agent": UA})

    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as c:
        m = re.search(r"(?:x|twitter)\.com/[^/]+/status/(\d+)", url)
        if m:
            r = await c.get(f"https://api.fxtwitter.com/status/{m.group(1)}", headers={"User-Agent": UA})
            if r.status_code == 200:
                tw = r.json().get("tweet") or {}
                media = (tw.get("media") or {}).get("all") or []
                title = (tw.get("text") or "X post")[:60]
                items = []
                for i, md in enumerate(media):
                    is_img = md.get("type") == "photo"
                    u = md.get("url")
                    if is_img:
                        u = re.sub(r"(\?.*)?$", "?name=orig", u)
                    e = "jpg" if is_img else "mp4"
                    name = f"{safe_name(title)} ({i + 1}).{e}" if len(media) > 1 else f"{safe_name(title)}.{e}"
                    items.append(_item(title, md.get("thumbnail_url") or md.get("url"),
                                       "image" if is_img else "video", u, name, {"User-Agent": UA}))
                if items:
                    return {"title": title, "site": "X", "uploader": (tw.get("author") or {}).get("name"),
                            "items": items}

        # Video-only sites: a page preview image is not what the user wants.
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        if re.search(r"(^|\.)(tiktok|youtube|youtu|vimeo|soundcloud|twitch|dailymotion|streamable|bilibili)\.", host + "."):
            return None
        r = await c.get(url, headers={"User-Agent": "facebookexternalhit/1.1"})
        page = r.text[:600_000]

    def og(prop):
        for pat in (rf'<meta[^>]+property=["\']{prop}["\'][^>]+content=["\']([^"\']+)',
                    rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']{prop}["\']'):
            mm = re.search(pat, page, re.I)
            if mm:
                return html.unescape(mm.group(1))
        return None

    title = og("og:title") or "grab"
    vid = og("og:video:secure_url") or og("og:video")
    img = og("og:image")
    if vid and not vid.endswith(".swf"):
        return _single(title, img, "video", vid, f"{safe_name(title)}.mp4", {"User-Agent": UA}, site="Page")
    if img:
        e = urllib.parse.urlparse(img).path.rsplit(".", 1)[-1].lower()
        e = e if e in IMG_EXT else "jpg"
        return _single(title, img, "image", img, f"{safe_name(title)}.{e}", {"User-Agent": UA}, site="Page")
    return None


def _item(title, thumb, media, url, name, headers):
    return {"title": title, "thumb": thumb, "media": media, "duration": None,
            "job": {"k": "direct", "u": [url], "h": [headers], "n": name, "t": title, "a": None}}


def _single(title, thumb, media, url, name, headers, site="Link"):
    return {"title": title, "site": site, "uploader": None,
            "items": [_item(title, thumb, media, url, name, headers)]}


def friendly(msg: str) -> str:
    m = msg.lower()
    if "unsupported url" in m or "no media found" in m or "no video" in m:
        return "That link isn't a video, audio or picture post."
    if "sign in" in m or "login" in m or "cookies" in m or "private" in m or "rate-limit" in m:
        return "This site wants a login for that post (or blocked the server). Try another link."
    if "not available" in m or "removed" in m or "404" in m:
        return "That post isn't available any more."
    return "Couldn't grab that one. The site may have blocked it."


@app.get("/api/info")
async def info(url: str, mode: str = "video"):
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        raise HTTPException(400, "Paste a full link that starts with http")
    audio = mode == "audio"
    err = None
    try:
        data = await asyncio.to_thread(_ytdlp, url, audio)
    except Exception as e:  # noqa: BLE001
        err = str(e)
        data = None
        try:
            data = await _fallback(url, audio)
        except Exception:  # noqa: BLE001
            data = None
    if not data:
        return JSONResponse({"error": friendly(err or ""), "detail": (err or "")[:300]}, status_code=422)
    for it in data["items"]:
        it["token"] = sign(it.pop("job"))
    return data


# ---------- streaming ----------

def _ff_headers(h: dict) -> list[str]:
    if not h:
        return []
    return ["-headers", "".join(f"{k}: {v}\r\n" for k, v in h.items())]


async def _ffmpeg_stream(args: list[str]):
    proc = await asyncio.create_subprocess_exec(
        FFMPEG, "-hide_banner", "-loglevel", "error", *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        while True:
            chunk = await proc.stdout.read(256 * 1024)
            if not chunk:
                break
            yield chunk
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()


def _disposition(name: str) -> str:
    ascii_name = re.sub(r"[^A-Za-z0-9._ ()-]", "_", name)
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{urllib.parse.quote(name)}"


@app.post("/api/file")
async def file(req: Request):
    form = await req.form()
    job = unsign(str(form.get("token") or ""))
    name, kind, urls, heads = job["n"], job["k"], job["u"], job["h"]
    headers = {"Content-Disposition": _disposition(name), "Cache-Control": "no-store",
               "X-Grab-Name": urllib.parse.quote(name)}

    if kind == "mp3":
        args = [*_ff_headers(heads[0]), "-i", urls[0], "-vn", "-map", "0:a:0",
                "-c:a", "libmp3lame", "-b:a", "192k", "-metadata", f"title={job.get('t') or ''}"]
        if job.get("a"):
            args += ["-metadata", f"artist={job['a']}"]
        return StreamingResponse(_ffmpeg_stream([*args, "-f", "mp3", "pipe:1"]),
                                 media_type="audio/mpeg", headers=headers)

    if kind == "remux":
        args = []
        for u, h in zip(urls, heads):
            args += [*_ff_headers(h), "-i", u]
        maps = ["-map", "0:v:0?", "-map", f"{len(urls) - 1}:a:0?"]
        args += [*maps, "-c", "copy", "-bsf:a", "aac_adtstoasc",
                 "-movflags", "frag_keyframe+empty_moov+default_base_moof", "-f", "mp4", "pipe:1"]
        return StreamingResponse(_ffmpeg_stream(args), media_type="video/mp4", headers=headers)

    client = httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30, read=60))
    upstream = await client.send(client.build_request("GET", urls[0], headers=heads[0]), stream=True)
    if upstream.status_code >= 400:
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(502, "The site refused the download, try again")
    if upstream.headers.get("content-length"):
        headers["Content-Length"] = upstream.headers["content-length"]

    async def body():
        try:
            async for chunk in upstream.aiter_bytes(256 * 1024):
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    ctype = upstream.headers.get("content-type", "application/octet-stream").split(";")[0]
    return StreamingResponse(body(), media_type=ctype, headers=headers)


@app.get("/api/health")
async def health():
    return {"ok": True, "yt_dlp": yt_dlp.version.__version__, "local": LOCAL}
