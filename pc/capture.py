# Copyright 2026 fanzipei
# SPDX-License-Identifier: Apache-2.0
"""User-dragged screen region capture; the full desktop stays in memory only."""
import ctypes
import tkinter as tk
from PIL import ImageGrab
from .windows import user32


class RegionCapture:
    def __init__(self, root, on_image, on_close):
        self.on_image,self.on_close=on_image,on_close
        self.left,self.top=user32.GetSystemMetrics(76),user32.GetSystemMetrics(77)
        self.width,self.height=user32.GetSystemMetrics(78),user32.GetSystemMetrics(79)
        self.image=ImageGrab.grab(all_screens=True)
        self.window=tk.Toplevel(root)
        self.window.withdraw()
        self.window.title("框选要识别的区域 · Esc 取消")
        self.window.overrideredirect(True)
        self.window.attributes("-topmost",True)
        self.window.attributes("-alpha",0.25)
        self.canvas=tk.Canvas(self.window,bg="black",cursor="crosshair",highlightthickness=0)
        self.canvas.pack(fill="both",expand=True)
        self.start=None
        self.rectangle=None
        self.canvas.bind("<ButtonPress-1>",self.begin)
        self.canvas.bind("<B1-Motion>",self.drag)
        self.canvas.bind("<ButtonRelease-1>",self.finish)
        self.window.bind("<Escape>",lambda event:self.close())
        self.window.geometry(f"{self.width}x{self.height}")
        self.window.deiconify()
        self.window.update_idletasks()
        hwnd=user32.GetAncestor(self.window.winfo_id(),2)
        user32.SetWindowPos(hwnd,ctypes.c_void_p(-1),self.left,self.top,self.width,self.height,0x0040)
        self.window.focus_force()
        self.window.grab_set()

    def begin(self,event):
        self.start=(event.x,event.y)
        self.rectangle=self.canvas.create_rectangle(event.x,event.y,event.x,event.y,outline="#7effaa",width=3)

    def drag(self,event):
        if self.start:
            self.canvas.coords(self.rectangle,*self.start,event.x,event.y)

    def finish(self,event):
        if not self.start:
            return
        x0,x1=sorted((self.start[0],max(0,min(self.width,event.x))))
        y0,y1=sorted((self.start[1],max(0,min(self.height,event.y))))
        selected=self.image.crop((x0,y0,x1,y1)) if x1-x0>=8 and y1-y0>=8 else None
        self.close()
        if selected is not None:
            self.on_image(selected)

    def close(self):
        self.image=None
        if self.window.winfo_exists():
            self.window.grab_release()
            self.window.destroy()
        self.on_close()
