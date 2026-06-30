#!/usr/bin/env python3
import ctypes, ctypes.util, time, subprocess, sys
from datetime import datetime
from pybooklid import read_lid_angle

# ---- tunables ----
CLOSED_REF   = 315.0
TOLERANCE    = 12.0
POLL_INTERVAL = 0.4
DEBOUNCE     = 3
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
    ("CGDisplayIsBuiltin",[cid],ctypes.c_int32),
    ("CGBeginDisplayConfiguration",[ctypes.POINTER(ctypes.c_void_p)],ctypes.c_int32),
    ("CGCompleteDisplayConfiguration",[ctypes.c_void_p,ctypes.c_uint32],ctypes.c_int32),
    ("CGCancelDisplayConfiguration",[ctypes.c_void_p],ctypes.c_int32)]:
    f=getattr(cg,nm); f.argtypes=a; f.restype=r; globals()[nm]=f
kCGConfigureForSession = 2

def _online_displays():
    cnt=ctypes.c_uint32(0); CGGetOnlineDisplayList(0,None,ctypes.byref(cnt))
    arr=(cid*cnt.value)(); got=ctypes.c_uint32(0)
    CGGetOnlineDisplayList(cnt.value,arr,ctypes.byref(got))
    return [arr[i] for i in range(got.value)]

def builtin_id():
    return next((d for d in _online_displays() if CGDisplayIsBuiltin(d)), None)

def external_attached():
    return any(not CGDisplayIsBuiltin(d) for d in _online_displays())

def set_internal(enabled):
    bid = builtin_id()
    if bid is None: return False
    cfg=ctypes.c_void_p()
    if CGBeginDisplayConfiguration(ctypes.byref(cfg))!=0: return False
    if _CGSConfigureDisplayEnabled(cfg,bid,enabled)!=0: CGCancelDisplayConfiguration(cfg); return False
    if CGCompleteDisplayConfiguration(cfg,kCGConfigureForSession)!=0: CGCancelDisplayConfiguration(cfg); return False
    return True
# ---------------------------------------------------------------------------

def circ_dist(a,b):
    d=abs(a-b)%360; return min(d,360-d)

def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)

log(f"lidsleep started — closed≈{CLOSED_REF:.0f}° ±{TOLERANCE:.0f}°, need {DEBOUNCE} reads.")

closed = 0
internal_disabled = False

while True:
    angle = read_lid_angle()
    if angle is None:
        time.sleep(POLL_INTERVAL); continue

    near = circ_dist(angle, CLOSED_REF) <= TOLERANCE

    if near:
        closed += 1
    else:
        closed = 0
        if internal_disabled:                 # lid opened → restore screen
            if set_internal(True):
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
                set_internal(True); internal_disabled = False
            print() if sys.stdout.isatty() else None
            log("lid closed, no external → sleeping now")
            subprocess.run(["pmset", "sleepnow"])
            closed = 0
            time.sleep(6)

    time.sleep(POLL_INTERVAL)