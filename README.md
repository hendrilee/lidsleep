# lidsleep

A tiny macOS daemon that watches your MacBook's **lid-angle sensor** and does the right thing the instant you close the lid:

- **Lid closed + external display attached** → turns off the built-in screen and **keeps your Mac awake** (clamshell mode) — no need to plug in power or pair a keyboard.
- **Lid closed + no external display** → puts the Mac to sleep **immediately**, instead of waiting on macOS's own delay.
- **Lid reopened** → re-enables the built-in screen automatically.

It reads the actual hinge angle from the hardware sensor, so it reacts to a real "lid is basically shut" gesture rather than guessing.

## Why?

macOS only allows clamshell mode (lid closed, external display, machine awake) when the laptop is plugged into power *and* has an external keyboard/mouse. `lidsleep` gives you clamshell behavior on battery and without peripherals, and makes closing the lid with no external display sleep instantly.

It also rescues Macs whose built-in lid detection is **physically broken** — see [Works around a broken lid sensor](#works-around-a-broken-lid-sensor) below.

## How it works

| Situation | Action |
|---|---|
| Lid near closed (`~315°`) for N consecutive reads + external display | Disable built-in display, stay awake |
| Lid near closed + **no** external display | `pmset sleepnow` |
| Lid opened again | Re-enable built-in display |

Display control is done through CoreGraphics / SkyLight (`CGSConfigureDisplayEnabled`), the same private API used by tools like DisplayDeck. The lid angle comes from [`pybooklid`](https://pypi.org/project/pybooklid/).

## Works around a broken lid sensor

Your Mac actually has **two independent lid sensors**:

- a **Hall-effect (magnet) sensor** — a magnet in the lid and a sensor in the chassis. This is the one macOS uses to decide *"lid closed → sleep / clamshell."*
- a **lid-angle (hinge) sensor** — reports the exact opening angle in degrees.

`lidsleep` reads the **angle sensor**. macOS's own sleep-on-close logic relies on the **Hall sensor**. They are completely separate pieces of hardware.

When the Hall sensor's magnet drifts out of alignment — a common aging fault — macOS starts reading the **wrong lid position**. A typical failure looks like this: the lid is fully shut but macOS thinks it's *open*, and the trigger fires instead when the lid is only partway open. Symptoms:

- the Mac **won't sleep** when you close the lid (it believes the lid is still open), and/or
- the screen blanks or the Mac tries to clamshell when the lid is only **partly open**.

You can confirm the fault by watching the Hall sensor while you move the lid:

```bash
ioreg -r -k AppleClamshellState -d 4 | grep AppleClamshellState
```

Close the lid fully and re-run it. If `AppleClamshellState` still reads `= No` (i.e. "open") when the lid is genuinely shut, your magnet is misaligned and macOS is reading the wrong position.

Because `lidsleep` makes its own decisions from the **angle sensor** and issues the sleep / display commands directly, it **bypasses the faulty Hall sensor entirely**. That restores correct sleep-on-close and clamshell behavior purely in software — no teardown, no re-seating the magnet, no external-magnet trick.

## Requirements

- A MacBook with a **lid-angle sensor** (introduced on the 2019 16" MacBook Pro and present on most MacBook Pro models since; some MacBook Air / 13" models do **not** expose it — verify below).
- macOS.
- Python 3.
- [`pybooklid`](https://pypi.org/project/pybooklid/)

Verify your Mac actually exposes the angle sensor before going further:

```bash
hidutil list --matching '{"VendorID":0x5ac,"ProductID":0x8104,"PrimaryUsagePage":32,"PrimaryUsage":138}'
```

If it lists a device, you're good. If it returns nothing, your model doesn't expose the sensor and this tool won't work on it.

## Install

```bash
git clone https://github.com/<your-username>/lid-close-sensor.git
cd lid-close-sensor
pip3 install pybooklid
```

## Usage

Run it in a terminal to watch live:

```bash
python3 lidsleep.py
```

When attached to a terminal it prints a live status line:

```
  angle 312.4°  [CLOSED]  streak 2/3  intDisabled=False
```

When run headless (e.g. as a service) it logs discrete events only:

```
[18:42:01] lidsleep started — closed≈315° ±12°, need 3 reads.
[18:42:09] lid closed + external → built-in disabled (clamshell)
[18:43:30] lid opened → built-in re-enabled
```

## Configuration

Tunables live at the top of `lidsleep.py`:

| Setting | Default | Meaning |
|---|---|---|
| `CLOSED_REF` | `315.0` | Hinge angle (degrees) that counts as "closed" |
| `TOLERANCE` | `12.0` | Degrees of slack around `CLOSED_REF` |
| `POLL_INTERVAL` | `0.4` | Seconds between sensor reads |
| `DEBOUNCE` | `3` | Consecutive "closed" reads required before acting |
| `DISABLE_INTERNAL_WHEN_CLOSED` | `True` | Enable clamshell behavior when an external display is present |

> **Tip — find your `CLOSED_REF`:** the angle reading at "fully shut" varies between machines, and some units use a rotated reference frame where the value climbs toward closed and wraps through `360° → 0°` rather than sitting near `0°`. Run the script, watch the live `angle` readout as you slowly close the lid, and set `CLOSED_REF` to whatever it shows when nearly shut. The matching logic uses circular distance, so wraparound near 360°/0° is handled automatically.

## Run at login (optional)

Create a launch agent at `~/Library/LaunchAgents/com.user.lidsleep.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>            <string>com.user.lidsleep</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/absolute/path/to/lidsleep.py</string>
  </array>
  <key>RunAtLoad</key>        <true/>
  <key>KeepAlive</key>        <true/>
  <key>StandardOutPath</key>  <string>/tmp/lidsleep.log</string>
  <key>StandardErrorPath</key><string>/tmp/lidsleep.err</string>
</dict>
</plist>
```

Then load it:

```bash
launchctl load ~/Library/LaunchAgents/com.user.lidsleep.plist
```

> It must run as a **LaunchAgent** (per-user, in your GUI session), not a LaunchDaemon — the display-control API only works from within the logged-in session.

## Managing the service

```bash
# confirm it's running (a PID in the first column = alive)
launchctl list | grep lidsleep

# view recent events
cat /tmp/lidsleep.log

# reload after editing the script or plist
launchctl unload ~/Library/LaunchAgents/com.user.lidsleep.plist
launchctl load   ~/Library/LaunchAgents/com.user.lidsleep.plist

# stop it
launchctl unload ~/Library/LaunchAgents/com.user.lidsleep.plist
```

## Uninstall

```bash
launchctl unload ~/Library/LaunchAgents/com.user.lidsleep.plist
rm ~/Library/LaunchAgents/com.user.lidsleep.plist
```

## Troubleshooting

**`Sensor not available` / no angle readings.** Your model may not expose the lid-angle sensor — run the `hidutil` check in [Requirements](#requirements). If it lists nothing, the tool can't work on that machine.

**`CGSConfigureDisplayEnabled` not found.** The script falls back from CoreGraphics to `SkyLight.framework` automatically. If both fail on your macOS version, open an issue with your exact macOS build and the error text.

**Works in Terminal but not under launchd.** launchd runs with a minimal environment. Make sure `ProgramArguments` points at the exact interpreter from `which python3`, and that `pybooklid` is installed for *that* Python. If `/tmp/lidsleep.err` shows a `library not loaded` (hidapi) error, add the Homebrew lib path to the plist:

```xml
  <key>EnvironmentVariables</key>
  <dict>
    <key>DYLD_LIBRARY_PATH</key><string>/opt/homebrew/lib</string>
  </dict>
```

**Built-in screen didn't come back.** Re-opening the lid past the closed band re-enables it automatically. As a manual fallback, plug/unplug the external display or toggle displays in System Settings.

## Caveats

- Uses **private/undocumented** CoreGraphics/SkyLight APIs. They work today but Apple could change them in a future macOS release.
- Tested on Apple Silicon MacBooks. Behavior on Intel models with the sensor may vary.
- `pmset sleepnow` triggers a real system sleep — make sure that's what you want.

## Acknowledgements

- [`pybooklid`](https://pypi.org/project/pybooklid/) for lid-angle sensor access.
- [DisplayDeck](https://github.com/oabdrabo/DisplayDeck) for the `CGSConfigureDisplayEnabled` display-control approach.
- Sam Henri Gold's [LidAngleSensor](https://github.com/samhenrigold/LidAngleSensor) reverse-engineering of the lid-angle HID interface.

## License

MIT — see [LICENSE](LICENSE).