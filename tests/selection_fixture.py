# Copyright 2026 fanzipei
# SPDX-License-Identifier: Apache-2.0
"""A disposable editor containing only owned text for real global-hotkey tests."""
from pathlib import Path
import time
import tkinter as tk

TEXT="HOTKEY-CHECK-20261024\n第一行：划选发送测试。\n第二行：换行和标点应完整保留。"


def main():
    root=tk.Tk()
    root.title("小伴侣快捷键测试 · 临时文本")
    root.geometry("700x310")
    editor=tk.Text(root,font=("Microsoft YaHei UI",15),padx=20,pady=20)
    editor.pack(fill="both",expand=True)
    editor.insert("1.0",TEXT)
    def select_all(event):
        editor.tag_add("sel","1.0","end-1c")
        return "break"
    editor.bind("<Control-a>",select_all)
    editor.bind("<Control-A>",select_all)
    started=time.time()
    def check_close():
        marker=Path("logs/selection-fixture.close")
        if time.time()-started>600 or (marker.exists() and marker.stat().st_mtime>started):
            root.destroy()
        else:
            root.after(200,check_close)
    root.after(200,check_close)
    root.mainloop()


if __name__=="__main__":
    main()
