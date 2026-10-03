# compositor.py: fetches the still image, draws sensor text on it, writes a finished JPEG.

import email.utils
import io
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime
import datetime as datetime_module

from PIL import Image, ImageDraw, ImageFont




def log(msg):
    print("{} compositor: {}".format(
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)



IMAGE_URL = os.environ["IMAGE_URL"]
OUTPUT_PATH = os.environ["OUTPUT_PATH"]

HA_URL = os.environ.get("HA_URL", "").rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
UNAVAILABLE_TEXT = os.environ.get("UNAVAILABLE_TEXT", "unavailable")
TEXT_ENABLED = os.environ.get("TEXT_ENABLED", "true").lower() == "true"

POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "5"))
SENSOR_POLL_SECONDS = float(os.environ.get("SENSOR_POLL_SECONDS", "60"))
STALE_SECONDS = float(os.environ.get("STALE_SECONDS", "60"))
SENSOR_HOLD_SECONDS = float(os.environ.get("SENSOR_HOLD_SECONDS", "1800"))
HTTP_TIMEOUT = float(os.environ.get("HTTP_TIMEOUT", "10"))

CORNERS = ("bottom-left", "bottom-right", "top-left", "top-right")

LEGACY_CORNER = os.environ.get("TEXT_CORNER", "bottom-left").lower()
if LEGACY_CORNER not in CORNERS:
    LEGACY_CORNER = "bottom-left"


def _suffix(corner):
    """bottom-left becomes BOTTOM_LEFT, so the variable has no hyphen.

    Hyphens are not usable in an environment variable name in most shells, and
    docker compose would treat them awkwardly. Underscores only.
    """
    return corner.replace("-", "_").upper()


def _sensor_env(corner, legacy_name):
    """Per-corner value if set, otherwise the legacy single value."""
    specific = os.environ.get("SENSOR_ENTITY_{}".format(_suffix(corner)))
    if specific is not None:
        return specific
    if corner == LEGACY_CORNER:
        return os.environ.get(legacy_name, "")
    return ""


def _unit_env(corner, legacy_name):
    specific = os.environ.get("SENSOR_UNIT_{}".format(_suffix(corner)))
    if specific is not None:
        return specific
    if corner == LEGACY_CORNER:
        return os.environ.get(legacy_name, "")
    return ""


SENSOR_ENTITY = {c: _sensor_env(c, "SENSOR_ENTITY") for c in CORNERS}
SENSOR_UNIT = {c: _unit_env(c, "SENSOR_UNIT") for c in CORNERS}

BASE_FRAC = float(os.environ.get("BASE_FRAC", "0.10"))


def parse_length(raw, axis):
    """Accept either an absolute pixel count or a percentage of the frame.

    A bare number is always pixels, so every existing value keeps working
    unchanged. Only an explicit trailing percent sign means a fraction, and only
    then does the value scale with the frame.

    Deliberately not inferring percent from the number being small. Someone
    typing 0.5 means half a pixel, not half the frame, and guessing otherwise
    would put the text in a wildly wrong place. Being literal is safer.

    Returns (value, is_percent). The chosen interpretation is reported at startup,
    since a percent sign being silently ignored would look like a rendering fault
    rather than a units mistake.
    """
    raw = str(raw).strip().lower()
    try:
        if raw.endswith("%"):
            pct = float(raw[:-1].strip())
            if pct < 0:
                raise ValueError("negative")
            return pct / 100.0, True
        px = float(raw)
        if px < 0:
            raise ValueError("negative")
        return px, False
    except (TypeError, ValueError):
        log("MARGIN {}: cannot read {!r}, using 0".format(axis, raw))
        return 0.0, False


MARGIN_X, MARGIN_X_PCT = parse_length(
    os.environ.get("MARGIN_X", "6"), "X")
MARGIN_BOTTOM, MARGIN_BOTTOM_PCT = parse_length(
    os.environ.get("MARGIN_BOTTOM", "8"), "BOTTOM")


def _corner_var(axis, corner):
    key = "MARGIN_X" if axis == "X" else "MARGIN_BOTTOM"
    return "{}_{}".format(key, corner.replace("-", "_").upper())


MARGINS = {}
for _c in CORNERS:
    _xvar = os.environ.get(_corner_var("X", _c))
    _bvar = os.environ.get(_corner_var("B", _c))
    MARGINS[_c] = (
        parse_length(_xvar, "X") if _xvar is not None
        else (MARGIN_X, MARGIN_X_PCT),
        parse_length(_bvar, "BOTTOM") if _bvar is not None
        else (MARGIN_BOTTOM, MARGIN_BOTTOM_PCT),
    )


MAX_WIDTH_FRAC = float(os.environ.get("MAX_WIDTH_FRAC", "0.40"))
STAMP_SIZE = int(os.environ.get("STAMP_SIZE", "17"))

STAMP_FORMAT_DEFAULT = "%m/%d/%Y %I:%M:%S %p"
_stamp_format = os.environ.get("STAMP_FORMAT", "").strip()
if not _stamp_format:
    _stamp_format = STAMP_FORMAT_DEFAULT
else:
    try:
        datetime.now().strftime(_stamp_format)
    except (ValueError, TypeError):
        log("STAMP_FORMAT: cannot read {!r}, using {!r}".format(
            _stamp_format, STAMP_FORMAT_DEFAULT))
        _stamp_format = STAMP_FORMAT_DEFAULT
STAMP_FORMAT = _stamp_format

DIAGNOSE_AGE = os.environ.get("DIAGNOSE_AGE", "false").lower() == "true"

DIAGNOSE = (os.environ.get("DIAGNOSE", "false").lower() == "true"
            or DIAGNOSE_AGE)



def parse_color(raw, name, fallback):
    text = str(raw).strip()
    digits = text[1:] if text.startswith("#") else text

    if "," in text:
        try:
            parts = tuple(int(p) for p in text.split(","))
            if len(parts) != 3 or any(not 0 <= p <= 255 for p in parts):
                raise ValueError
            return parts
        except ValueError:
            log("COLOR {}: cannot read {!r}, using {}".format(
                name, raw, fallback))
            return fallback

    if len(digits) == 3 and all(c in "0123456789abcdefABCDEF" for c in digits):
        return tuple(int(c * 2, 16) for c in digits)

    if len(digits) == 6 and all(c in "0123456789abcdefABCDEF" for c in digits):
        return tuple(int(digits[i:i + 2], 16) for i in (0, 2, 4))

    log("COLOR {}: cannot read {!r}, using {}".format(name, raw, fallback))
    return fallback


def _hex(rgb):
    """Render an RGB triple as #rrggbb, for the startup log.

    The format string is built by joining six placeholders and the triple is
    unpacked with a star. The first version passed a six-element tuple straight
    into .format(), which does not unpack, and raised TypeError on every single
    """
    return "#{:02x}{:02x}{:02x}".format(*rgb)


PLACEHOLDER_COLOR = parse_color(
    os.environ.get("PLACEHOLDER_COLOR")
    or os.environ.get("PLACEHOLDER_RGB", "#6e6e6e"),
    "PLACEHOLDER_COLOR", (110, 110, 110))

TEXT_COLOR = parse_color(
    os.environ.get("TEXT_COLOR", "#FFFFFF"), "TEXT_COLOR", (255, 255, 255))


FONT_PATH = os.environ.get("FONT_PATH", "/app/fonts/RobotoCondensed.ttf")

def _timezone():
    """Return (abbreviation, utc offset as +HH:MM) for the running container.

    Uses the TZ-aware branch of the time module, which reads whatever the TZ
    variable points at. Falls back to UTC naming when the platform has no
    timezone set, rather than raising.
    """
    local = time.localtime()
    if time.daylight and local.tm_isdst > 0:
        seconds = -time.altzone
        name = time.tzname[1]
    else:
        seconds = -time.timezone
        name = time.tzname[0]
    sign = "+" if seconds >= 0 else "-"
    seconds = abs(seconds)
    return name, "{}{:02d}:{:02d}".format(sign, seconds // 3600,
                                          (seconds % 3600) // 60)




def fit_font(text, height, max_width):
    """Base size as a fraction of frame height, shrinking to fit max width."""
    size = max(6, int(round(height * BASE_FRAC)))
    while size > 6:
        font = ImageFont.truetype(FONT_PATH, size)
        box = font.getbbox(text, stroke_width=1)
        if (box[2] - box[0]) <= max_width:
            return font
        size -= 1
    return ImageFont.truetype(FONT_PATH, 6)


def corner_pos(img, box, corner, margin_x, margin_v,
               margin_x_pct=False, margin_v_pct=False):
    """Top-left of the text, given the corner it is anchored to.

    margin_x always measures from the horizontal edge the text sits against.
    margin_v measures from the vertical edge on the same side as the corner.

    A margin flagged as a percentage is a fraction of the matching frame
    dimension, so it scales with resolution. Otherwise it is an absolute pixel
    count. Rounded to whole pixels, since a fractional pixel position has no
    meaning.

    Anchoring compensates for the glyph's own overhang and for the one pixel
    outline drawn around it, so all four corners give the same visible gap from
    their edge. Without this the top corners sat about 8px further from the edge
    than the bottom ones, because the font's ascent offset above the visible
    """
    w, h = box[2] - box[0], box[3] - box[1]
    W, H = img.size
    left = corner.endswith("left")
    top = corner.startswith("top")
    mx = int(round(margin_x * W)) if margin_x_pct else margin_x
    my = int(round(margin_v * H)) if margin_v_pct else margin_v

    x = mx if left else W - w - mx + 2
    y = my - 2 * box[1] if top else H - h - my
    return x, y


def stamp():
    return datetime.now().strftime(STAMP_FORMAT)


def draw_overlay(img, corner, text):
    font = fit_font(text, img.size[1], int(img.size[0] * MAX_WIDTH_FRAC))
    box = font.getbbox(text, stroke_width=1)
    (mx, mx_pct), (mv, mv_pct) = MARGINS[corner]
    x, y = corner_pos(img, box, corner, mx, mv, mx_pct, mv_pct)
    ImageDraw.Draw(img).text(
        (x, y), text, font=font, fill=TEXT_COLOR, stroke_width=1,
        stroke_fill=(0, 0, 0))


def draw_overlays(img, values):
    """Draw one value per corner. Empty values are skipped entirely."""
    draw = ImageDraw.Draw(img)
    for corner in CORNERS:
        text = values.get(corner, "")
        if not text:
            continue
        draw_overlay(img, corner, text)
    return img


def make_placeholder(size):
    img = Image.new("RGB", size, PLACEHOLDER_COLOR)
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT_PATH, STAMP_SIZE)
    text = stamp()
    box = draw.textbbox((0, 0), text, font=font, stroke_width=1)
    x = (size[0] - (box[2] - box[0])) / 2
    y = size[1] / 2 - (box[3] - box[1]) / 2
    draw.text((x, y), text, font=font, fill=TEXT_COLOR, stroke_width=1,
              stroke_fill=(40, 40, 40))
    return img




def parse_http_date(raw):
    """Parse an HTTP-date header into an aware datetime, or None.

    Returns None rather than raising, because a missing or odd Last-Modified
    must never be able to stop a frame being drawn. The diagnostic reports that
    case rather than guessing.

    Coerces to str first. An HTTP header is always a string or None, so this
    cannot happen from urlopen, but the function is also called from tests and
    a bare int raised AttributeError on .strip. Cheap to guard.
    """
    if raw is None or raw == "":
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(str(raw).strip())
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime_module.timezone.utc)
    return parsed.astimezone(datetime_module.timezone.utc)


def fetch_image():
    """Return (PIL image, change token, timing dict) or (None, reason, None).

    A half-written FTP upload fails both the JPEG marker check and the decode,
    so it is discarded and the previous good frame stays on screen.

    The timing dict is only populated when DIAGNOSE is on. It carries the
    source's own Last-Modified, when the server sent one, and how long the fetch
    itself took.
    """
    timing = {"fetch_s": 0.0, "source_mtime": None,
              "server_date": None, "bytes": 0}
    started = time.monotonic()
    try:
        req = urllib.request.Request(IMAGE_URL, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            data = resp.read()
            last_modified = resp.headers.get("Last-Modified")
            token = (last_modified or "") + str(len(data))
            server_date = resp.headers.get("Date")
        timing["fetch_s"] = time.monotonic() - started
        timing["bytes"] = len(data)
        if DIAGNOSE and DIAGNOSE_AGE:
            timing["source_mtime"] = parse_http_date(last_modified)
            timing["server_date"] = parse_http_date(server_date)
    except Exception as exc:
        return None, "error: {}".format(exc), None

    if not data:
        return None, "empty response", None
    if not (data[:2] == b"\xff\xd8" and data[-2:] == b"\xff\xd9"):
        return None, "not a complete JPEG ({} bytes)".format(len(data)), None
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:
        return None, "decode failed: {}".format(exc), None
    return img.convert("RGB"), token, timing


def report_diagnosis(timing, waited_s, render_s):
    """Log where the time went on this machine, and optionally end to end.

    Always reported, because all three are timed with a monotonic clock inside
    this single container and cannot be affected by anything outside it:

      fetch   downloading the image
      render  decoding, drawing the text, encoding
      waited  how long the frame sat before the poll noticed it

    The source age is reported only when DIAGNOSE_AGE is also set, because it is
    the one figure that depends on two machines agreeing about what time it is.
    See the comment on DIAGNOSE_AGE for what a live capture showed.
    """
    fetch_s = timing.get("fetch_s", 0.0)
    bytes_in = timing.get("bytes", 0)
    mtime = timing.get("source_mtime")
    server_date = timing.get("server_date")

    parts = ["fetch {:.0f}ms".format(fetch_s * 1000),
             "{}KB".format(bytes_in // 1024),
             "render {:.0f}ms".format(render_s * 1000)]

    if waited_s is not None:
        parts.append("waited {:.1f}s for our poll".format(waited_s))

    if not DIAGNOSE_AGE:
        log("diagnose: " + ", ".join(parts))
        return

    if mtime is None:
        parts.append("age unknown, source sent no Last-Modified")
        log("diagnose: " + ", ".join(parts))
        return

    age = (datetime.now(datetime_module.timezone.utc) - mtime).total_seconds()
    parts.append("source age {:.1f}s".format(age))

    if age < -0.5:
        parts.append("WARNING source timestamp is {:.1f}s in the future, "
                     "the clocks disagree and this age is meaningless"
                     .format(-age))

    if server_date is not None:
        skew = (datetime.now(datetime_module.timezone.utc)
                - server_date).total_seconds()
        if abs(skew) > 2.0:
            parts.append("WARNING clock differs from source by {:.0f}s, "
                         "the age figure is out by that much".format(skew))

    log("diagnose: " + ", ".join(parts))




class Sensor:
    """Holds the last good reading for one corner. Escalates to 'unavailable'.

    Note: frames and sensors both come from the same HA machine, so a full HA
    outage is handled by the stale-frame grey screen, not here.
    """

    def __init__(self, corner, entity, unit):
        self.corner = corner
        self.entity = entity
        self.unit = unit
        self.text = ""
        self.bad_since = None
        self.reported = False

    def poll(self):
        if not (HA_URL and HA_TOKEN and self.entity):
            return
        url = "{}/api/states/{}".format(HA_URL, self.entity)
        try:
            req = urllib.request.Request(
                url, headers={"Authorization": "Bearer {}".format(HA_TOKEN)})
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                body = resp.read().decode("utf-8", "replace")
            state = json.loads(body)["state"]
            if not isinstance(state, str):
                state = str(state)
        except urllib.error.HTTPError as exc:
            self._fail("HTTP {} from HA".format(exc.code))
            return
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            self._fail(str(exc))
            return

        if state in ("unknown", "unavailable", "None", ""):
            self._fail("sensor state is {}".format(state))
            return

        value = state
        if self.unit:
            value = "{}{}".format(state, self.unit)
        if value != self.text:
            log("sensor {} -> {}".format(self.corner, value))
        self.text = value
        self.bad_since = None
        if self.reported:
            log("sensor {} recovered".format(self.corner))
            self.reported = False

    def _fail(self, reason):
        now = time.monotonic()
        if self.bad_since is None:
            self.bad_since = now
            log("sensor {} failure: {}".format(self.corner, reason))
        elif not self.reported and now - self.bad_since >= SENSOR_HOLD_SECONDS:
            log("sensor {} unreadable for {:.0f}s, showing {!r}".format(
                self.corner, now - self.bad_since, UNAVAILABLE_TEXT))
            self.reported = True
        if self.bad_since is not None and self.reported:
            self.text = UNAVAILABLE_TEXT

    def value(self):
        return self.text




def write(img):
    """Write atomically so FFmpeg never reads a half-written file."""
    tmp = OUTPUT_PATH + ".tmp"
    img.save(tmp, format="JPEG", quality=95)
    os.replace(tmp, OUTPUT_PATH)


def main():
    log("image url    {}".format(IMAGE_URL))
    log("output       {}".format(OUTPUT_PATH))
    log("text enabled {}".format(TEXT_ENABLED))

    sensors = {}
    for corner in CORNERS:
        entity = SENSOR_ENTITY[corner]
        if entity:
            sensors[corner] = Sensor(corner, entity, SENSOR_UNIT[corner])
            mx, mxp = MARGINS[corner][0]
            mv, mvp = MARGINS[corner][1]
            log("corner {:13} sensor {:32} margin x{} v{}".format(
                corner, entity,
                "{:g}%".format(mx * 100) if mxp else "{:g}px".format(mx),
                "{:g}%".format(mv * 100) if mvp else "{:g}px".format(mv)))
    if not sensors:
        log("no sensors configured, text will not be drawn")

    log("base {:.0%} max width {:.0%} stale {:.0f}s".format(
        BASE_FRAC, MAX_WIDTH_FRAC, STALE_SECONDS))

    log("timezone     {} ({})".format(*_timezone()))
    log("time format   {} -> {!r}".format(STAMP_FORMAT, stamp()))
    log("diagnose     {}{}".format(
        "on" if DIAGNOSE else "off",
        " with source age" if DIAGNOSE_AGE else ""))
    log("text colour  {}  placeholder {}".format(
        _hex(TEXT_COLOR), _hex(PLACEHOLDER_COLOR)))

    def _fmt_len(value, is_pct):
        return "{:g}%".format(value * 100) if is_pct else "{:g}px".format(value)

    log("margins shared x{} v{} (per-corner overrides above)".format(
        _fmt_len(MARGIN_X, MARGIN_X_PCT),
        _fmt_len(MARGIN_BOTTOM, MARGIN_BOTTOM_PCT)))

    last_frame = None
    last_token = None
    last_good = 0.0
    last_sensor_poll = 0.0
    last_stamp = 0.0
    last_reason = None
    last_rendered = None
    size = None
    last_fetch_started = time.monotonic()
    in_placeholder = False
    placeholder_since = 0.0
    waited_s = None

    while True:
        now = time.monotonic()
        img, token, timing = fetch_image()

        if img is not None:
            recovered = last_reason is not None
            if token != last_token or recovered:
                last_frame = img
                last_token = token
                last_good = now
                size = img.size
                waited_s = now - last_fetch_started
                if recovered:
                    log("frame source recovered")
                    last_reason = None
        else:
            if token != last_reason:
                log("frame unusable: {}".format(token))
                last_reason = token

        if now - last_sensor_poll >= SENSOR_POLL_SECONDS:
            for sensor in sensors.values():
                sensor.poll()
            last_sensor_poll = now

        stale = (now - last_good) >= STALE_SECONDS
        if stale:
            if not in_placeholder:
                if last_token is None:
                    log("showing placeholder: no usable frame yet")
                else:
                    log("showing placeholder: no new frame for {:.0f}s, "
                        "threshold is {:.0f}s".format(
                            now - last_good, STALE_SECONDS))
                in_placeholder = True
                placeholder_since = now
            if now - last_stamp >= 1:
                write(make_placeholder(size or (352, 200)))
                last_stamp = now
            last_rendered = None
        elif last_frame is not None:
            if in_placeholder:
                log("live again after {:.0f}s on the placeholder".format(
                    now - placeholder_since))
                in_placeholder = False
            values = {c: s.value() for c, s in sensors.items()} \
                if TEXT_ENABLED else {}
            render_key = (last_token, tuple(sorted(values.items())))
            if render_key != last_rendered:
                frame = last_frame.copy()
                draw_overlays(frame, values)
                render_started = time.monotonic()
                write(frame)
                last_rendered = render_key
                if DIAGNOSE and timing:
                    report_diagnosis(timing, waited_s,
                                     time.monotonic() - render_started)

        time.sleep(POLL_SECONDS)
        last_fetch_started = time.monotonic()


if __name__ == "__main__":
    while True:
        try:
            main()
            break
        except KeyboardInterrupt:
            break
        except Exception:
            import traceback
            log("compositor crashed, restarting:\n{}".format(traceback.format_exc()))
            time.sleep(5)
