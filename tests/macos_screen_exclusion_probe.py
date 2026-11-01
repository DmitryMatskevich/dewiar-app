"""Manual native probe: briefly display two fixture windows, verify compositor exclusion.

Requires an unlocked macOS desktop and Screen Recording permission. Does not save desktop pixels.
"""
import io
import sys
import threading
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from AppKit import NSApplication,NSWindow,NSColor,NSMakeRect,NSFloatingWindowLevel
from Foundation import NSRunLoop,NSDate
from PIL import Image
from macsys import screen_capture
import numpy as np

app=NSApplication.sharedApplication()
app.setActivationPolicy_(1);app.finishLaunching();app.activateIgnoringOtherApps_(True)
windows=[]
for title,rgb in [('OCR fixture',(0.11,0.79,0.27)),('Dewiar',(0.93,0.07,0.69))]:
    window=NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(NSMakeRect(250,250,360,220),0,2,False)
    window.setReleasedWhenClosed_(False);window.setTitle_(title);window.setOpaque_(True)
    window.setBackgroundColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(*rgb,1))
    window.setLevel_(NSFloatingWindowLevel);window.orderFrontRegardless();windows.append(window)
result={}

def work():
    try:
        time.sleep(1)
        for exclude in [False,True]:
            pixels=np.asarray(Image.open(io.BytesIO(screen_capture(exclude_dewiar=exclude))).convert('RGB'))
            magenta=((pixels[:,:,0]>210)&(pixels[:,:,1]<110)&(pixels[:,:,2]>140)&(pixels[:,:,2]<210)).sum()
            green=((pixels[:,:,0]<130)&(pixels[:,:,1]>170)&(pixels[:,:,2]>30)&(pixels[:,:,2]<130)).sum()
            result[exclude]=(int(magenta),int(green))
    except Exception as error:result['error']=str(error)
    finally:result['done']=True

threading.Thread(target=work,daemon=True).start()
try:
    until=time.monotonic()+40
    while not result.get('done') and time.monotonic()<until:
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(.05))
finally:
    for window in windows:window.orderOut_(None);window.close()
assert 'error' not in result and result.get('done'),result
assert result[False][0]>100000,result
assert result[True][0]<result[False][0]/5,result
assert result[True][1]>100000,result
print('PASS: assistant excluded, actual underlying window captured; no blanking/cropping')
