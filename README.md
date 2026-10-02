# imgstream

Turns a still JPEG into an MJPEG camera stream, with a Home Assistant sensor value drawn onto the frame.

![A camera frame with sensor values in all four corners](images/overlay-corners.jpg)

One sensor per corner:

![The same frame with a single sensor in the lower left](images/overlay.jpg)


## What it does

Two processes per container:

- A Python compositor polls your image URL, checks the image is complete, draws the sensor text on it,
  and writes the result to a local file.
- FFmpeg loops that file and serves it as MJPEG.


## Why

To turn a still image into a camera with the ability to put Home Assistant data over the top.


## Requirements

- Docker with Docker Compose
- An image source reachable over HTTP. Any web server will do, including Home Assistant's
  `/local/` folder.
- Optionally Home Assistant, if you want a sensor drawn on the frame.


## Setup

### 1. Create a Home Assistant token

Only needed if you want the sensor overlay.

A token inherits that user's permissions. If you want read-only access, make a non-admin user and
create the token from there.


### 2. Deploy

Save this as `docker-compose.yml`.

```yaml
services:
  camera1:
    image: ghcr.io/sonomagts/imgstream:latest
    restart: unless-stopped
    ports:
      - "8084:8084"
    environment:
      IMAGE_URL: "http://HOST:PORT/PATH-TO-YOUR-IMAGE.jpg"
      STREAM_PORT: "8084"
      TZ: "Etc/UTC"
```

Start it:

```sh
docker compose up -d
```

Then point your viewer at `http://<host>:<port>`. `TZ` is your timezone; see below if the placeholder
timestamp looks wrong.

To add a sensor overlay, add the settings from the next section.


## Configuration

Required:

| Variable | What it does |
| --- | --- |
| `IMAGE_URL` | The image to pull. Blank and the container refuses to start. |
| `STREAM_PORT` | Port the stream answers on. Must match the number in `ports:`. |

Home Assistant, for the text overlay:

| Variable | Default | What it does |
| --- | --- | --- |
| `HA_URL` | | Home Assistant address, no trailing slash |
| `HA_TOKEN` | | Long-lived access token |
| `SENSOR_POLL_SECONDS` | `60` | How often to poll Home Assistant |
| `SENSOR_HOLD_SECONDS` | `1800` | Seconds of an unreadable sensor before showing `unavailable` |


### Sensors

Two ways to do this. Pick one.

**One sensor, one corner.** The short form:

```sh
SENSOR_ENTITY: "sensor.outdoor_temperature"
SENSOR_UNIT: "°F"
TEXT_CORNER: "bottom-left"
```

**One sensor per corner.** Each corner takes its own entity and unit, so four different values can
share the frame:

```sh
SENSOR_ENTITY_BOTTOM_LEFT: "sensor.outdoor_temperature"
SENSOR_UNIT_BOTTOM_LEFT: "°F"
SENSOR_ENTITY_BOTTOM_RIGHT: "sensor.outdoor_humidity"
SENSOR_UNIT_BOTTOM_RIGHT: "%"
SENSOR_ENTITY_TOP_LEFT: "sensor.indoor_temperature"
SENSOR_UNIT_TOP_LEFT: "°F"
SENSOR_ENTITY_TOP_RIGHT: "sensor.pool_temperature"
SENSOR_UNIT_TOP_RIGHT: "°F"
```

`SENSOR_UNIT_` is optional per corner, and each one is free to use a different unit. Leave an entity
blank to leave that corner empty.

Text placement:

| Variable | Default | What it does |
| --- | --- | --- |
| `TEXT_ENABLED` | `true` | Draw the sensor on the frame. Must be exactly `true` or `false` |
| `TEXT_CORNER` | `bottom-left` | `bottom-left`, `bottom-right`, `top-left`, `top-right`. Which corner the short form draws in |
| `MARGIN_X` | `6` | Pixels from the left or right edge, or a percentage like `2%` |
| `MARGIN_BOTTOM` | `8` | Pixels from the bottom or top edge, or a percentage |
| `BASE_FRAC` | `0.10` | Text size as a fraction of image height |
| `MAX_WIDTH_FRAC` | `0.40` | Long values shrink to fit this fraction of image width |
| `STAMP_SIZE` | `17` | Placeholder timestamp size in pixels |
| `TEXT_COLOR` | `#FFFFFF` | color of the sensor text |
| `UNAVAILABLE_TEXT` | `unavailable` | Shown instead of a number when the sensor dies |

colors are hex, with or without the leading `#`. `#fff` works as well as `#ffffff`, and case does not
matter. The sensor text keeps a fixed black outline whatever `TEXT_COLOR` is set to, which is what
keeps it readable against a bright or washed-out picture.

`MARGIN_X_TOP_RIGHT` and the other per-corner margin variables override just that corner.

The default margins are tuned for a small frame, around 352x200, so on anything larger they look
cramped. That is why the example sets `MARGIN_X: "1.7%"` and `MARGIN_BOTTOM: "4%"`, which give the
same gap at any resolution.

Timing and the stale screen:

| Variable | Default | What it does |
| --- | --- | --- |
| `STALE_SECONDS` | `60` | No new image for this long shows the grey screen |
| `POLL_SECONDS` | `5` | How often the image URL is fetched |
| `HTTP_TIMEOUT` | `10` | Seconds before giving up on a stalled request |
| `PLACEHOLDER_COLOR` | `#6e6e6e` | Grey screen color |
| `TZ` | `Etc/UTC` | Timezone for the placeholder timestamp |


### Timezone

The grey screen's timestamp is the only thing `TZ` affects, and it is worth setting. Containers run
on UTC by default, so on a machine that isn't set to UTC the placeholder shows a time hours away from
the real one.

`TZ` takes an IANA name such as `America/New_York` or `Australia/Sydney`.

Alternatively, to follow the host's timezone without naming it:

```yaml
volumes:
  - /etc/localtime:/etc/localtime:ro
```


## Behaviour worth knowing

**Grey screen.** After `STALE_SECONDS` with no usable image, you get a grey frame with the current date
and time, so you can see how stale the feed is. No sensor value is shown on it.

![A grey screen showing the current date and time](images/placeholder.jpg)

**A half-written image is discarded.** An FTP upload in progress produces a truncated JPEG. It's
detected and skipped, and the previous frame stays up.

**A dead sensor doesn't kill the feed.** A short Home Assistant outage holds the last reading. After
`SENSOR_HOLD_SECONDS` it shows `unavailable` instead. The timer resets as soon as a good reading
arrives.

**Text scales with resolution.** `BASE_FRAC` is a fraction of image height, so the same setting looks
the same on a 352x200 frame and a 1080p one. Margins are pixels unless you add a `%`.


## Things to watch for

| Setting | Symptom if you get it wrong |
| --- | --- |
| `POLL_SECONDS` higher than `STALE_SECONDS` | A healthy camera stuck on the grey screen permanently |
| `STALE_SECONDS` lower than your source's update interval | Grey screen on a working camera for most of each cycle |
| `TEXT_ENABLED` misspelled | Silently no text. Only `true` or `false` count, so `yes` means false |
| Pixel margins on a large frame | Text jammed into the corner, hard to read |
| A color without the `#`, or as `r,g,b` | Logged as unreadable and falls back to the default |

The second is the one most likely to bite. If your image updates every 20 minutes, `STALE_SECONDS`
needs to be well above that.


## Limitations

**Sensor values show as they are.** No unit conversion, no rounding. Whatever Home Assistant reports is
what gets drawn. If you want `72°F`, have the sensor report `72` and set `SENSOR_UNIT` to `°F`.


## Output format

MJPEG over HTTP, using `multipart/x-mixed-replace`. Every frame is a standalone JPEG, so a dropped
packet costs one frame rather than breaking the stream.

Stream settings:

| Variable | Default | What it does |
| --- | --- | --- |
| `STREAM_FPS` | `2` | Frames per second served. This is a loop over a still image, so higher values cost CPU without adding information |
| `STREAM_QUALITY` | `5` | JPEG quality, FFmpeg's `-q:v` scale. Lower is better quality and more CPU, and the usable range is roughly 2 to 8 |

Neither is usually worth changing. The frame only changes when a new image arrives, so a higher frame
rate shows the same picture more often.


## Troubleshooting

**Grey screen with a timestamp.** No usable image for `STALE_SECONDS`. Open `IMAGE_URL` in a browser.
If it loads there, the source is fine and the problem is between it and the container.

**The placeholder timestamp is hours out.** The container is on UTC. Set `TZ`, or mount
`/etc/localtime`.

**Nothing at all on the port.** Check the container is running and look at its logs:

```sh
docker compose logs
```

The first line of the log says what it's serving and on which port, which is the quickest way to
confirm the settings were picked up.

**No text on the frame.** `TEXT_ENABLED` only accepts `true` or `false`, so `yes` or `True` counts as
false and there is no error. Also check the token is valid; a bad one fails silently for the first
`SENSOR_HOLD_SECONDS`.

**No text, and both sensor forms are set.** A blank per-corner variable counts as set, and overrides
the short form. Delete one of the two.

**Text in the wrong place.** `MARGIN_X` and `MARGIN_BOTTOM` are pixels, not percentages, so they look
right at one resolution and cramped at another. Use a trailing `%` to make them scale. The startup
log says which it read, in pixels or percent.


## License

MIT. See [LICENSE](LICENSE).

The bundled font, `fonts/RobotoCondensed.ttf`, is Roboto Condensed from Google Fonts, licensed under
the Apache License 2.0.
