import json
import os
import logging
import time
import re
import requests
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from dotenv import load_dotenv

load_dotenv()

# Config
COOKIES_FILE = os.getenv("COOKIES_FILE", "freepik_cookies_raw.json")
STORAGE_STATE_FILE = os.getenv("STORAGE_STATE_FILE", "state.json")
USER_AGENT_ENV = os.getenv("USER_AGENT", None)
HEADFUL = os.getenv("HEADFUL", "0").lower() not in ("0", "false", "no")
DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

# Behavior toggles
SEND_FILE_AS_DOCUMENT = os.getenv("SEND_FILE_AS_DOCUMENT", "0").strip().lower() in ("1", "true", "yes")

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("bot.log", mode="a")]
)

# -------------------------
# Cookie / UA helpers
# -------------------------
def _normalize_cookie_for_playwright(c):
    nc = {
        "name": c.get("name"),
        "value": c.get("value", ""),
        "path": c.get("path", "/"),
        "httpOnly": bool(c.get("httpOnly", False)),
        "secure": bool(c.get("secure", False))
    }
    domain = c.get("domain")
    if domain:
        domain_clean = domain.lstrip(".")
        nc["domain"] = domain_clean
        if "freepik.com" in domain_clean.lower():
            nc["url"] = "https://www.freepik.com" + nc["path"]
        elif "magnific.com" in domain_clean.lower():
            nc["url"] = "https://www.magnific.com" + nc["path"]
        else:
            nc["url"] = f"https://{domain_clean}{nc['path']}"
    expires = c.get("expires", None)
    try:
        if expires not in (None, -1):
            nc["expires"] = int(float(expires))
    except Exception:
        pass
    ss = c.get("sameSite")
    if isinstance(ss, str):
        ssc = ss.capitalize()
        if ssc in ("Lax", "Strict", "None"):
            nc["sameSite"] = ssc
    return nc

def load_cookies_into_context(context, path=COOKIES_FILE):
    p = Path(path)
    if not p.exists():
        logging.info("No cookies file: %s", path)
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        logging.exception("Failed loading cookies JSON")
        return False
    cookies = data.get("cookies", data) if isinstance(data, dict) else data
    now = datetime.now(timezone.utc).timestamp()
    usable = []
    for c in cookies:
        expires = c.get("expires", None)
        if expires in (None, -1):
            usable.append(c)
        else:
            try:
                if int(float(expires)) > now:
                    usable.append(c)
            except Exception:
                usable.append(c)
    normalized = []
    for c in usable:
        nc = _normalize_cookie_for_playwright(c)
        if nc.get("name") and (nc.get("value") is not None):
            normalized.append(nc)
    if not normalized:
        logging.warning("No normalized cookies to add")
        return False
    try:
        context.add_cookies(normalized)
        logging.info("Applied %d cookies to context", len(normalized))
        return True
    except Exception:
        logging.exception("context.add_cookies failed")
        return False

def read_user_agent_from_files():
    if USER_AGENT_ENV:
        return USER_AGENT_ENV
    try:
        p = Path(COOKIES_FILE)
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            ua = d.get("user_agent")
            if ua:
                return ua
        p = Path(STORAGE_STATE_FILE)
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            meta = d.get("metadata") or {}
            ua = meta.get("user_agent") or d.get("user_agent") or d.get("userAgent")
            if ua:
                return ua
    except Exception:
        pass
    return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

def _localstorage_to_array(ls):
    """
    Playwright requires localStorage as an array of {name, value} dicts.
    state.json sometimes stores it as a plain {key: value} object — fix that here.
    """
    if isinstance(ls, dict):
        return [{"name": k, "value": v} for k, v in ls.items()]
    if isinstance(ls, list):
        return ls
    return []

def load_and_sanitize_storage_state(path=STORAGE_STATE_FILE):
    """
    Load state.json and convert any localStorage/sessionStorage that is a plain
    dict into the [{name, value}] array format that Playwright expects.
    Returns the sanitized data as a dict, or None if the file doesn't exist.
    """
    p = Path(path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        logging.exception("Failed to read storage state file")
        return None

    origins = data.get("origins") or []
    for origin in origins:
        ls = origin.get("localStorage")
        if ls is not None:
            origin["localStorage"] = _localstorage_to_array(ls)
        ss = origin.get("sessionStorage")
        if ss is not None:
            origin["sessionStorage"] = _localstorage_to_array(ss)

    sanitized = {
        "cookies": data.get("cookies") or [],
        "origins": origins,
    }
    logging.info("Loaded storage state: %d cookies, %d origins", len(sanitized["cookies"]), len(origins))
    return sanitized

# -------------------------
# UI helpers (cookie banner, verify)
# -------------------------
def _click_accept_cookie_banner(page):
    selectors = [
        "button:has-text('Accept All Cookies')",
        "button:has-text('Accept all cookies')",
        "button:has-text('Accept')",
        "button#onetrust-accept-btn-handler",
        "button[aria-label*='accept']",
        "button[class*='cookie']",
    ]
    try:
        for sel in selectors:
            try:
                loc = page.locator(sel)
                if loc.count() and loc.first.is_visible(timeout=800):
                    loc.first.click()
                    page.wait_for_timeout(800)
                    logging.info("Clicked cookie accept selector: %s", sel)
                    return True
            except Exception:
                continue
        for frame in page.frames:
            for sel in selectors:
                try:
                    loc = frame.locator(sel)
                    if loc.count() and loc.first.is_visible(timeout=800):
                        loc.first.click()
                        page.wait_for_timeout(800)
                        logging.info("Clicked cookie accept in frame: %s", sel)
                        return True
                except Exception:
                    continue
    except Exception:
        logging.exception("cookie accept helper error")
    return False

def verify_logged_in_by_profile(context, screenshot_path=None):
    profile_urls = [
        "https://www.freepik.com/profile",
        "https://www.magnific.com/app",
    ]
    positives = ["sign out", "log out", "my profile", "my account", "dashboard"]
    page = context.new_page()
    try:
        for profile_url in profile_urls:
            try:
                page.goto(profile_url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(1200)
                html = page.content().lower()
                for p in positives:
                    if p in html:
                        return True
                if _click_accept_cookie_banner(page):
                    try:
                        page.reload(wait_until="domcontentloaded", timeout=8000)
                        page.wait_for_timeout(1000)
                        html = page.content().lower()
                        for p in positives:
                            if p in html:
                                return True
                    except Exception:
                        pass
                try:
                    keys = page.evaluate("() => Object.keys(window.localStorage || {})")
                    low = [k.lower() for k in keys]
                    logging.info("localStorage keys during verify (%s): %s", profile_url, low[:20])
                    for k in low:
                        if any(tok in k for tok in ("gr_token", "gr_refresh", "token", "auth", "session")):
                            logging.info("Found localStorage auth key: %s", k)
                            return True
                except Exception:
                    logging.debug("Could not inspect localStorage")
            except Exception:
                logging.debug("verify_logged_in_by_profile: failed checking %s", profile_url)
                continue
        if screenshot_path:
            try:
                page.screenshot(path=screenshot_path, full_page=True)
            except Exception:
                pass
        return False
    except Exception:
        logging.exception("verify_logged_in_by_profile error")
        try:
            if screenshot_path:
                page.screenshot(path=screenshot_path, full_page=True)
        except Exception:
            pass
        return False
    finally:
        try:
            page.close()
        except Exception:
            pass

def inject_localstorage_from_state(context, state_path=STORAGE_STATE_FILE):
    p = Path(state_path)
    if not p.exists():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        logging.exception("Failed to read state.json")
        return False
    origins = data.get("origins") or []
    if not origins:
        return False
    applied = False
    for origin in origins:
        origin_url = origin.get("origin") or origin.get("baseURL") or ""
        if "freepik.com" not in origin_url and "magnific.com" not in origin_url:
            continue
        raw_ls = origin.get("localStorage") or {}
        local_storage = _localstorage_to_array(raw_ls)
        if not local_storage:
            continue
        page = context.new_page()
        try:
            page.goto(origin_url, wait_until="domcontentloaded", timeout=10000)
            for item in local_storage:
                name = item.get("name")
                value = item.get("value", "")
                if name:
                    try:
                        page.evaluate("([k,v]) => localStorage.setItem(k,v)", [name, value])
                    except Exception:
                        try:
                            js = f"window.localStorage.setItem({json.dumps(name)}, {json.dumps(value)});"
                            page.evaluate(js)
                        except Exception:
                            logging.debug("Failed set localStorage key %s", name)
            page.wait_for_timeout(400)
            applied = True
        except Exception:
            logging.exception("Failed injecting localStorage for %s", origin_url)
        finally:
            try:
                page.close()
            except Exception:
                pass
    return applied

# -------------------------
# Download helpers
# -------------------------
def try_playwright_download(page, selectors=None, timeout=12000):
    selectors = selectors or [
        "button:has-text('Download')",
        "button:has-text('Download now')",
        "button:has-text('Free download')",
        "[data-cy='download']",
        "[data-testid*='download']",
        ".download-button",
        "a[href*='download']",
        "a:has-text('Download')",
        "a:has-text('Download now')"
    ]
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=3000):
                logging.info("Attempting Playwright download for selector %s", sel)
                try:
                    href = el.get_attribute("href")
                    if href and href.startswith("http"):
                        logging.info("Found direct href download URL: %s", href)
                        return href
                except Exception:
                    pass

                try:
                    with page.expect_download(timeout=timeout) as dd:
                        el.click()
                    download = dd.value
                    dl_url = None
                    try:
                        dl_url = getattr(download, "url", None)
                        if callable(dl_url):
                            try:
                                dl_url = dl_url()
                            except Exception:
                                dl_url = None
                    except Exception:
                        dl_url = None
                    if not dl_url:
                        try:
                            dl_url = download.url if hasattr(download, "url") else None
                        except Exception:
                            dl_url = None

                    if dl_url and isinstance(dl_url, str) and dl_url.startswith("http"):
                        logging.info("Found browser download URL: %s", dl_url)
                        return dl_url

                    if SEND_FILE_AS_DOCUMENT:
                        suggested = getattr(download, "suggested_filename", None) or f"file_{int(time.time())}"
                        out_path = DOWNLOAD_DIR / suggested
                        try:
                            download.save_as(str(out_path))
                            logging.info("Saved download to %s", out_path)
                            return str(out_path)
                        except Exception:
                            logging.exception("Failed saving download as file")
                            return None
                    else:
                        logging.info("No direct download URL and SEND_FILE_AS_DOCUMENT disabled.")
                        return None
                except PWTimeout:
                    logging.info("No browser download event for selector %s", sel)
                except Exception:
                    logging.exception("Error during expect_download for %s", sel)
        except Exception:
            continue
    return None

def _is_candidate_response(response):
    url = response.url.lower()
    if any(x in url for x in (
        "/download", "/get-file", "/get-download", "/export",
        ".s3.", "presigned", "signed-url", "download-url",
        "amazonaws.com", "cdn.", "storage", ".zip", ".psd", ".ai", ".jpg", ".png"
    )):
        return True
    try:
        ct = (response.headers.get("content-type") or "").lower()
        if "application/json" in ct:
            return True
        if any(x in ct for x in (
            "application/octet-stream",
            "application/zip",
            "image/",
            "application/x-photoshop"
        )):
            return True
    except Exception:
        pass
    return False

def capture_signed_download_url_and_fetch(page, click_action, wait_timeout=15000):
    try:
        with page.expect_response(lambda r: _is_candidate_response(r), timeout=wait_timeout) as resp_info:
            click_action()
        resp = resp_info.value
    except PWTimeout:
        logging.info("No candidate response within timeout")
        return None
    except Exception:
        logging.exception("Error while waiting for response")
        return None

    logging.info("Captured response URL: %s", resp.url)
    signed = None
    resp_text = None
    try:
        ct = (resp.headers.get("content-type") or "").lower()
        if "application/json" in ct:
            j = resp.json()
            resp_text = json.dumps(j)[:2000]
            for k in ("url", "downloadUrl", "signedUrl", "presigned_url", "fileUrl", "file_url", "link"):
                v = j.get(k)
                if isinstance(v, str) and v.startswith("http"):
                    signed = v
                    break
            if not signed:
                m = re.search(r"https?://[^\s\"']+", json.dumps(j))
                if m:
                    signed = m.group(0)
        else:
            if any(x in resp.url.lower() for x in (".s3.", "amazonaws.com", "cdn.", "content.", ".zip", ".psd", ".ai", ".jpg", ".png")):
                signed = resp.url
    except Exception:
        logging.exception("Failed to parse response JSON")

    try:
        diag = f"diagnostics/response_{int(time.time())}.json"
        Path("diagnostics").mkdir(exist_ok=True)
        with open(diag, "w", encoding="utf-8") as fh:
            fh.write(resp_text or f'url: {resp.url}\nheaders: {json.dumps(dict(resp.headers), indent=2)}')
        logging.info("Saved diagnostic response to %s", diag)
    except Exception:
        pass

    if not signed:
        logging.warning("No signed URL discovered in response")
        if SEND_FILE_AS_DOCUMENT:
            logging.info("SEND_FILE_AS_DOCUMENT enabled; attempting server-side fetch fallback.")
        else:
            return None

    if signed:
        logging.info("Returning signed URL: %s", signed)
        return signed

    try:
        ctx = page.context
        cookies = ctx.cookies()
        session = requests.Session()
        session.headers.update({"User-Agent": read_user_agent_from_files(), "Accept-Language": "en-US,en;q=0.9"})
        for c in cookies:
            domain = c.get("domain", "")
            name = c.get("name")
            val = c.get("value")
            if name and val:
                session.cookies.set(name, val, domain=domain)
        if not signed:
            logging.error("No signed URL to fetch for server-side fallback.")
            return None
        r = session.get(signed, timeout=60, stream=True)
        if r.status_code != 200:
            logging.warning("Signed URL returned status %s", r.status_code)
            return None
        filename = signed.split("/")[-1].split("?")[0] or f"file_{int(time.time())}"
        out_path = DOWNLOAD_DIR / filename
        with open(out_path, "wb") as fh:
            for chunk in r.iter_content(8192):
                if chunk:
                    fh.write(chunk)
        logging.info("Saved signed-download to %s", out_path)
        return str(out_path)
    except Exception:
        logging.exception("Failed to fetch signed URL (server-side fallback)")
        return None

def find_download_url_in_html(page):
    try:
        html = page.content()
        patterns = [
            r'https?://[^\s"\']+amazonaws[^\s"\']+',
            r'https?://[^\s"\']+cdn[^\s"\']+',
            r'https?://[^\s"\']+download[^\s"\']+',
            r'https?://[^\s"\']+\.zip[^\s"\']*',
            r'https?://[^\s"\']+\.psd[^\s"\']*',
            r'https?://[^\s"\']+\.ai[^\s"\']*',
            r'https?://[^\s"\']+\.jpg[^\s"\']*',
            r'https?://[^\s"\']+\.png[^\s"\']*',
        ]
        for pattern in patterns:
            m = re.search(pattern, html, re.IGNORECASE)
            if m:
                logging.info("Found download-like URL in HTML: %s", m.group(0))
                return m.group(0)
    except Exception:
        logging.exception("Failed scanning HTML for download URL")
    return None

# -------------------------
# Core: handle Freepik/Magnific link (main flow)
# -------------------------
def handle_freepik_download(file_url):
    """
    Download file from Freepik/Magnific using Playwright

    Args:
    - file_url: URL to download

    Returns:
    - download_url: Direct download link or file path, or None if failed
    """

    try:
        logging.info("Starting handle_freepik_download for URL: %s", file_url)

        with sync_playwright() as p:
            ua = read_user_agent_from_files()
            browser = p.chromium.launch(
                headless=(not HEADFUL),
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"]
            )

            sanitized_state = load_and_sanitize_storage_state(STORAGE_STATE_FILE)
            if sanitized_state is not None:
                logging.info("Using storage_state: %s (sanitized)", STORAGE_STATE_FILE)
                context = browser.new_context(
                    storage_state=sanitized_state,
                    user_agent=ua,
                    viewport={"width": 1920, "height": 1080},
                    extra_http_headers={"Accept-Language": "en-US,en;q=0.9"}
                )
            else:
                context = browser.new_context(
                    user_agent=ua,
                    viewport={"width": 1920, "height": 1080},
                    extra_http_headers={"Accept-Language": "en-US,en;q=0.9"}
                )
                if Path(COOKIES_FILE).exists():
                    load_cookies_into_context(context, COOKIES_FILE)

            verify_shot = f"verify_{int(time.time())}.png"
            ok = verify_logged_in_by_profile(context, screenshot_path=verify_shot)
            if not ok:
                injected = inject_localstorage_from_state(context, STORAGE_STATE_FILE)
                if injected:
                    ok = verify_logged_in_by_profile(context, screenshot_path=verify_shot)
            if not ok:
                logging.warning("Login verification failed")
                browser.close()
                return None

            page = context.new_page()
            page.set_default_timeout(30000)

            logging.info("Navigating to file URL")
            page.goto(file_url, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            _click_accept_cookie_banner(page)

            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass

            try:
                logging.info("Current URL after navigation: %s", page.url)
                logging.info("Page title: %s", page.title())
            except Exception:
                pass

            # Try direct hrefs first
            anchor_selectors = [
                "a[href*='download']",
                "a[href*='.zip']",
                "a[href*='.psd']",
                "a[href*='.ai']",
                "a[href*='amazonaws']",
                "a[href*='cdn']",
            ]
            for sel in anchor_selectors:
                try:
                    loc = page.locator(sel).first
                    if loc.is_visible(timeout=1500):
                        href = loc.get_attribute("href")
                        if href and href.startswith("http"):
                            logging.info("Found direct anchor href: %s", href)
                            browser.close()
                            return href
                except Exception:
                    continue

            # Try playwright download first
            download_result = try_playwright_download(page)
            if download_result:
                logging.info("✅ Got download URL: %s", download_result)
                browser.close()
                return download_result

            # Try XHR capture for signed URLs
            download_selectors = [
                "button:has-text('Download')",
                "button:has-text('Download now')",
                "button:has-text('Free download')",
                "[data-cy='download']",
                "[data-testid*='download']",
                ".download-button",
                "a[href*='download']",
                "a:has-text('Download')",
                "a:has-text('Download now')"
            ]
            for sel in download_selectors:
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=2000):
                        logging.info("Attempting XHR capture for selector %s", sel)
                        click_action = lambda e=el: e.click()
                        download_url = capture_signed_download_url_and_fetch(page, click_action, wait_timeout=15000)
                        if download_url:
                            logging.info("✅ Got download URL: %s", download_url)
                            browser.close()
                            return download_url
                except Exception:
                    continue

            # Final HTML scan fallback
            html_download = find_download_url_in_html(page)
            if html_download:
                logging.info("✅ Found fallback HTML download URL: %s", html_download)
                browser.close()
                return html_download

            logging.error("❌ Could not find download URL")
            shot = f"no_download_{int(time.time())}.png"
            try:
                page.screenshot(path=shot, full_page=True)
                logging.info("Saved failure screenshot: %s", shot)
            except Exception:
                pass
            browser.close()
            return None

    except Exception:
        logging.exception("handle_freepik_download error")
        return None
