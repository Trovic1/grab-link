// Same rules as the Android app: is this link a single video / audio / picture post?
// Returns "media", "not_media" or "unknown" (unknown sites are tried, and skipped if nothing is found).
(function () {
  const MEDIA_EXT = new Set(["mp4","m4v","mov","webm","mkv","3gp","m3u8","mp3","m4a","aac","ogg","oga","opus","wav","flac","jpg","jpeg","png","webp","gif"]);
  const NEVER = ["google.com","goo.gl","wa.me","whatsapp.com","t.me","telegram.me","play.google.com","apps.apple.com",
    "github.com","wikipedia.org","amazon.com","jumia.com.ng","chatgpt.com","claude.ai"];

  function quick(raw) {
    let u;
    try { u = new URL(String(raw).trim()); } catch { return "not_media"; }
    if (u.protocol !== "http:" && u.protocol !== "https:") return "not_media";
    const host = u.hostname.toLowerCase().replace(/^(www|m|mobile)\./, "");
    const path = u.pathname.toLowerCase().replace(/\/+$/, "");
    const query = u.search.replace(/^\?/, "");
    const last = path.split("/").pop();
    const ext = last.includes(".") ? last.split(".").pop() : "";
    if (MEDIA_EXT.has(ext)) return "media";

    const on = (...hs) => hs.some(h => host === h || host.endsWith("." + h));
    const p = re => new RegExp(re).test(path);
    const yes = b => (b ? "media" : "not_media");
    const hasPath = path.length > 1;

    if (on("youtu.be")) return yes(hasPath);
    if (on("youtube.com", "youtube-nocookie.com"))
      return yes((path === "/watch" && /(^|&)v=/.test(query)) || p("^/(shorts|live|embed|v)/[\\w-]+"));
    if (/^(vm|vt)\.tiktok\./.test(host)) return yes(hasPath);
    if (on("tiktok.com")) return yes(p("^/@[^/]+/(video|photo)/\\d+") || p("^/t/\\w+") || p("^/v/\\d+"));
    if (on("instagram.com")) return yes(p("(^|/)(p|reel|reels|tv)/[\\w-]+") || p("^/stories/[^/]+/\\d+") || p("^/share/(p|reel)/\\w+"));
    if (on("x.com", "twitter.com", "fxtwitter.com", "vxtwitter.com", "fixupx.com"))
      return yes(p("^/[^/]+/status(es)?/\\d+") || p("^/i/status/\\d+"));
    if (on("fb.watch")) return yes(hasPath);
    if (on("facebook.com", "fb.com"))
      return yes(p("/(videos|reel|reels)/") || (path === "/watch" && query.includes("v=")) || p("^/share/(v|r)/") || p("^/reel/\\d+"));
    if (on("pin.it")) return yes(hasPath);
    if (/(^|\.)pinterest\.[a-z.]+$/.test(host)) return yes(p("^/pin/[\\w-]+"));
    if (on("redd.it")) return yes(hasPath);
    if (on("reddit.com")) return yes(p("/comments/\\w+") || p("^/r/[^/]+/s/\\w+"));
    if (on("threads.net", "threads.com")) return yes(p("/post/[\\w-]+"));
    if (on("snapchat.com")) return yes(p("/spotlight/") || p("^/t/\\w+"));
    if (on("vimeo.com")) return yes(p("^/(\\d+|video/\\d+|channels/[^/]+/\\d+|groups/[^/]+/videos/\\d+)"));
    if (on("dai.ly")) return yes(hasPath);
    if (on("dailymotion.com")) return yes(p("^/video/"));
    if (on("on.soundcloud.com")) return yes(hasPath);
    if (on("soundcloud.com")) {
      const parts = path.split("/").filter(Boolean);
      return yes(parts.length === 2 && !["discover","search","you","charts","upload","stations"].includes(parts[0]) && parts[1] !== "sets");
    }
    if (on("clips.twitch.tv")) return yes(hasPath);
    if (on("twitch.tv")) return yes(p("^/[^/]+/clip/") || p("^/videos/\\d+"));
    if (on("linkedin.com")) return yes(p("^/(posts|feed/update)/"));
    if (on("streamable.com")) return yes(hasPath);
    if (on("b23.tv")) return yes(hasPath);
    if (on("bilibili.com")) return yes(p("^/video/"));
    if (on(...NEVER)) return "not_media";
    return "unknown";
  }

  const URL_RE = /https?:\/\/[^\s<>"'`]+/gi;
  function findLinks(text) {
    return [...new Set((String(text).match(URL_RE) || []).map(s => s.replace(/[),.;!?\]]+$/, "")))];
  }

  const api = { quick, findLinks };
  if (typeof module !== "undefined") module.exports = api; else window.LinkFilter = api;
})();
