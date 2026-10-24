# Copyright 2026 fanzipei
# SPDX-License-Identifier: Apache-2.0
"""Pure placement logic shared by the tray flyout and its tests."""


def popup_position(work, icon, width, height, gap=10):
    left, top, right, bottom = work
    if icon is None:
        x, y = right-width-gap, bottom-height-gap
    else:
        il, it, ir, ib = icon
        if it >= bottom:  # Bottom taskbar.
            x, y = ir-width, bottom-height-gap
        elif ib <= top:  # Top taskbar.
            x, y = ir-width, top+gap
        elif il >= right:  # Right taskbar.
            x, y = right-width-gap, ib-height
        elif ir <= left:  # Left taskbar.
            x, y = left+gap, ib-height
        else:  # Overflow panel or a floating tray.
            x, y = ir-width, it-height-gap
    x = min(max(left+gap, x), max(left+gap, right-width-gap))
    y = min(max(top+gap, y), max(top+gap, bottom-height-gap))
    return int(x), int(y)
