#!/usr/bin/env python3
import ctypes, ctypes.util, time, subprocess, sys
from datetime import datetime
from pybooklid import read_lid_angle

# ---- tunables ----
CLOSED_REF   = 315.0
TOLERANCE    = 12.0
POLL_INTERVAL = 0.4
DEBOUNCE     = 3
POST_SLEEP_GRACE = 5    # secs before checking display state after sleepnow
DISABLE_INTERNAL_WHEN_CLOSED = True   # clamshell: lid shut + external → kill built-in
# ------------------

# ---- CoreGraphics / SkyLight display control (ported from DisplayDeck) ----
cg = ctypes.CDLL(ctypes.util.find_library("CoreGraphics") or
    "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
try:
    _CGSConfigureDisplayEnabled = cg.CGSConfigureDisplayEnabled
except AttributeError:
    sl = ctypes.CDLL("/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight")
    _CGSConfigureDisplayEnabled = sl.CGSConfigureDisplayEnabled
cid = ctypes.c_uint32
_CGSConfigureDisplayEnabled.argtypes = [ctypes.c_void_p, cid, ctypes.c_bool]
_CGSConfigureDisplayEnabled.restype  = ctypes.c_int32
for nm, a, r in [
    ("CGGetOnlineDisplayList",[ctypes.c_uint32,ctypes.POINTER(cid),ctypes.POINTER(ctypes.c_uint32)],ctypes.c_int32),
    ("CGGetActiveDisplayList",[ctypes.c_uint32,ctypes.POINTER(cid),ctypes.POINTER(ctypes.c_uint32)],ctypes.c_int32),
    ("CGDisplayIsBuiltin",[cid],ctypes.c_int32),
    ("CGDisplayIsAsleep",[cid],ctypes.c_int32),
    ("CGBeginDisplayConfiguration",[ctypes.POINTER(ctypes.c_void_p)],ctypes.c_int32),
    ("CGCompleteDisplayConfiguration",[ctypes.c_void_p,ctypes.c_uint32],ctypes.c_int32),
    ("CGCancelDisplayConfiguration",[ctypes.c_void_p],ctypes.c_int32)]:
    f=getattr(cg,nm); f.argtypes=a; f.restype=r; globals()[nm]=f
kCGConfigureForSession = 2

def _display_list(fn):
    cnt=ctypes.c_uint32(0); fn(0,None,ctypes.byref(cnt))
    arr=(cid*cnt.value)(); got=ctypes.c_uint32(0)
    fn(cnt.value,arr,ctypes.byref(got))
    return [arr[i] for i in range(got.value)]

def _online_displays():
    return _display_list(CGGetOnlineDisplayList)

def builtin_id():
    return next((d for d in _online_displays() if CGDisplayIsBuiltin(d)), None)

def builtin_active():
    """True if the built-in display is actually rendering (in the active list)."""
    return any(CGDisplayIsBuiltin(d) for d in _display_list(CGGetActiveDisplayList))

def external_attached():
    return any(not CGDisplayIsBuiltin(d) for d in _online_displays())

def displays_asleep():
    """True while the system is asleep / dark-waking (all displays off).
    Uses WindowServer, NOT the HID sensor, so it cannot wake the Mac."""
    ds = _online_displays()
    return bool(ds) and all(CGDisplayIsAsleep(d) for d in ds)

def set_internal(enabled):
    bid = builtin_id()
    if bid is None: return False
    cfg=ctypes.c_void_p()
    if CGBeginDisplayConfiguration(ctypes.byref(cfg))!=0: return False
    if _CGSConfigureDisplayEnabled(cfg,bid,enabled)!=0: CGCancelDisplayConfiguration(cfg); return False
    if CGCompleteDisplayConfiguration(cfg,kCGConfigureForSession)!=0: CGCancelDisplayConfiguration(cfg); return False
    return True

def enable_internal(retries=5, delay=0.3):
    """Re-enable the built-in display and VERIFY it is active before returning."""
    for _ in range(retries):
        set_internal(True)
        time.sleep(delay)          # let the display config commit
        if builtin_active():
            return True
    return builtin_active()
# ---------------------------------------------------------------------------

def circ_dist(a,b):
    d=abs(a-b)%360; return min(d,360-d)

def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)

log(f"lidsleep started — closed≈{CLOSED_REF:.0f}° ±{TOLERANCE:.0f}°, need {DEBOUNCE} reads.")

closed = 0
internal_disabled = False

# Startup recovery: a previous run may have died with the built-in disabled.
if builtin_id() is not None and not builtin_active():
    log("built-in was left disabled by a previous run → re-enabling")
    enable_internal()

def main_loop():
  global closed, internal_disabled
  while True:
    # Safety net: if we ever disabled the built-in but the external is gone,
    # bring the built-in back IMMEDIATELY — regardless of lid angle or debounce.
    # This is the unplug-while-closed case that used to leave a black screen.
    if internal_disabled and not external_attached():
        if enable_internal():
            internal_disabled = False
            log("external unplugged → built-in re-enabled")
        else:
            log("WARNING: could not re-enable built-in display; retrying")
            time.sleep(POLL_INTERVAL); continue

    angle = read_lid_angle()
    if angle is None:
        time.sleep(POLL_INTERVAL); continue

    near = circ_dist(angle, CLOSED_REF) <= TOLERANCE

    if near:
        closed += 1
    else:
        closed = 0
        if internal_disabled:                 # lid opened → restore screen
            if enable_internal():
                internal_disabled = False
                log("lid opened → built-in re-enabled")

    if sys.stdout.isatty():
        st = "CLOSED" if near else "open"
        print(f"  angle {angle:5.1f}°  [{st}]  streak {closed}/{DEBOUNCE}  "
              f"intDisabled={internal_disabled}        ", end="\r", flush=True)

    if closed >= DEBOUNCE:
        if external_attached():
            if DISABLE_INTERNAL_WHEN_CLOSED and not internal_disabled:
                if set_internal(False):
                    internal_disabled = True
                    print() if sys.stdout.isatty() else None
                    log("lid closed + external → built-in disabled (clamshell)")
            # stay awake; keep polling so we notice the lid opening
        else:
            if internal_disabled:              # restore before sleeping
                if enable_internal():
                    internal_disabled = False
                else:
                    # Never sleep while the built-in is still disabled — that is
                    # exactly what causes the black screen on wake.
                    log("WARNING: built-in still disabled; NOT sleeping")
                    time.sleep(POLL_INTERVAL); continue
            print() if sys.stdout.isatty() else None
            log("lid closed, no external → sleeping now")
            subprocess.run(["pmset", "sleepnow"])
            closed = 0
            time.sleep(POST_SLEEP_GRACE)
            # CRITICAL: do not touch the lid-angle HID sensor while the system
            # is asleep or dark-waking — that registers as "HID Activity" and
            # promotes the dark wake to a full wake. Wait for the display to
            # come back (a real wake) before resuming polling.
            while displays_asleep():
                time.sleep(2)
            log("full wake detected → resuming lid polling")

    time.sleep(POLL_INTERVAL)

try:
    main_loop()
finally:
    # Never exit (Ctrl-C, kill, crash) leaving the built-in display dead.
    if internal_disabled:
        enable_internal()
        log("exiting → built-in re-enabled")