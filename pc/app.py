# Copyright 2026 fanzipei
# SPDX-License-Identifier: Apache-2.0
"""A tray icon, one small input flyout, and an explicitly opened settings dialog."""
import argparse
import ctypes
from ctypes import wintypes
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk
import webbrowser

import pystray
from PIL import Image, ImageDraw

from .bridge import Bridge
from .core import Repository, MAX_NOTES, MAX_NEWS
from .secrets import ApiKeyStore
from .summarizer import api_endpoint
from .display import display_packet, FONT_SIZES
from .ai import AiClient
from .capture import RegionCapture
from .reminders import ReminderStore, propose as propose_reminder
from .news_network import PROXY_MODES,validate_proxy
from .windows import Hotkeys, parse_hotkey, voice_typing, place_by_tray, ime_composing

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
LOG = logging.getLogger(__name__)


def icon_image(connected=False):
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((9, 8, 55, 53), 10, fill="#edf3ee", outline="#9caaa4", width=2)
    draw.line((20, 23, 44, 23), fill="#637b70", width=3)
    draw.line((20, 32, 41, 32), fill="#637b70", width=3)
    draw.ellipse((43, 42, 60, 59), fill="#5bb990" if connected else "#9aa39f",
                 outline="#202824", width=2)
    return image


class App:
    def __init__(self, initial_action=None):
        self.repository = Repository(DATA)
        self.api_keys = ApiKeyStore(DATA)
        self.ai=AiClient(self.api_keys,DATA)
        self.reminders=ReminderStore(DATA)
        self.region_capture=None
        self.reminder_window=None
        self.reminder_token=None
        self.reminders_due_at=0
        self.events = queue.Queue()
        self.bridge = Bridge(self.repository, self.events, self.reminders)
        self.device_reminder_ids=set()
        self.connected = False
        self.closed = False
        self.last_command = time.time_ns()
        self.pending_settings = None
        self.input_window = None
        self.settings_window = None
        self.note_window = None
        self.error = ""
        self.voice_guard = 0
        self.root = tk.Tk()
        self.root.withdraw()  # Never map a main window, including the first frame.
        self.root.report_callback_exception = lambda kind, value, trace: LOG.error(
            "UI callback failed", exc_info=(kind, value, trace))
        self.root.title("桌面小伴侣后台")
        self.scale = max(1.0, self.root.winfo_fpixels("1i") / 96)
        self.hotkeys = Hotkeys(self.repository.snapshot()["config"], self.events)
        self.tray = pystray.Icon("DeskCompanion", icon_image(), "桌面小伴侣", pystray.Menu(
            pystray.MenuItem("设置…", lambda: self.events.put(("settings", None))),
            pystray.MenuItem("退出", lambda: self.events.put(("quit", None))),
        ))
        self.tray.run_detached()
        self.hotkeys.start()
        self.bridge.start()
        self.update_tray()
        self.root.after(80, self.poll)
        if initial_action:
            self.root.after(200, self.show_settings if initial_action == "settings" else self.show_input)

    def px(self, value):
        return max(1, int(value*self.scale))

    def update_tray(self):
        snapshot = self.repository.snapshot()
        state = "已连接" if self.connected else "等待设备"
        pending = len(snapshot["outbox"])
        detail = f"待发送 {pending} 条" if pending else f"便签 {len(snapshot['notes'])}/{snapshot['config']['note_limit']}"
        title = f"桌面小伴侣 · {state} · {detail}"
        if self.error:
            title += " · " + self.error.replace("\n", " ")
        self.tray.title = title[:120]

    def record_error(self, value, *, input_feedback=False):
        self.error = str(value)
        LOG.warning("%s", value)
        self.update_tray()
        if input_feedback and self.input_window and self.input_window.winfo_viewable():
            self.input_hint.configure(text=self.error[:48], fg="#e3a4a1")

    def build_input(self):
        window = tk.Toplevel(self.root)
        window.withdraw()
        window.title("桌面小伴侣输入")
        window.overrideredirect(True)
        window.attributes("-toolwindow", True)
        window.attributes("-topmost", True)
        window.configure(bg="#3a4842")
        outer = tk.Frame(window, bg="#202923")
        outer.pack(fill="both", expand=True, padx=1, pady=1)
        self.input_window = window
        self.text = tk.Text(outer, height=2, wrap="word", undo=True, borderwidth=0,
                            highlightthickness=0, relief="flat", font=("Microsoft YaHei UI", 12),
                            bg="#202923", fg="#edf2ee", insertbackground="#a4d4b5",
                            selectbackground="#426a51", padx=self.px(15), pady=self.px(12))
        self.text.pack(fill="both", expand=True)
        self.placeholder = tk.Label(self.text, text="记点什么…", bg="#202923", fg="#8a9c90",
                                    font=("Microsoft YaHei UI", 12), cursor="xterm")
        self.placeholder.place(x=self.px(15), y=self.px(12))
        self.placeholder.bind("<Button-1>", lambda event: self.text.focus_set())
        self.text.bind("<<Modified>>", self.input_modified)
        footer = tk.Frame(outer, bg="#202923")
        footer.pack(fill="x", padx=self.px(15), pady=(0, self.px(10)))
        voice = tk.Label(footer, text="语音", font=("Microsoft YaHei UI", 9), cursor="hand2",
                         bg="#202923", fg="#a3c8b0")
        voice.pack(side="left")
        voice.bind("<Button-1>", lambda event: self.voice())
        self.input_hint = tk.Label(footer, text="Enter 发送 · Esc 收起", font=("Microsoft YaHei UI", 8),
                                   bg="#202923", fg="#8a9c90", anchor="e")
        self.input_hint.pack(side="right")
        self.text.bind("<Return>", self.send_input)
        self.text.bind("<Shift-Return>", lambda event: self.insert_newline())
        self.text.bind("<Escape>", lambda event: self.hide_input())
        window.bind("<Escape>", lambda event: self.hide_input())
        window.bind("<FocusOut>", lambda event: self.root.after(200, self.dismiss_unfocused))
        window.protocol("WM_DELETE_WINDOW", self.hide_input)

    def input_modified(self, event=None):
        if self.text.edit_modified():
            if self.text.get("1.0", "end-1c"):
                self.placeholder.place_forget()
            else:
                self.placeholder.place(x=self.px(15), y=self.px(12))
            self.text.edit_modified(False)
            self.input_hint.configure(text="Enter 发送 · Esc 收起", fg="#8a9c90")

    def show_input(self):
        if self.input_window is None or not self.input_window.winfo_exists():
            self.build_input()
        self.input_hint.configure(text="Enter 发送 · Esc 收起", fg="#8a9c90")
        place_by_tray(self.input_window, self.tray, self.px(360), self.px(112), self.px(10))
        self.text.focus_force()

    def hide_input(self):
        if self.input_window:
            self.input_window.withdraw()
        return "break"

    def dismiss_unfocused(self):
        if not self.input_window or not self.input_window.winfo_viewable():
            return
        if time.monotonic() < self.voice_guard:
            self.root.after(500, self.dismiss_unfocused)
            return
        try:
            focus = self.root.focus_displayof()
            if focus is None or focus.winfo_toplevel() != self.input_window:
                self.hide_input()
        except tk.TclError:
            self.hide_input()

    def insert_newline(self):
        self.text.insert("insert", "\n")
        return "break"

    def voice(self):
        self.voice_guard = time.monotonic()+5
        self.text.focus_force()
        def start():
            try:
                voice_typing()
            except Exception as exc:
                self.record_error(exc, input_feedback=True)
        self.root.after(160, start)

    def enqueue(self, value):
        self.repository.enqueue_note(value)
        self.error = ""
        self.bridge.wake()
        self.update_tray()

    def send_input(self, event=None):
        # Enter while a Chinese IME is composing commits characters, never a note.
        if ime_composing():
            return None
        value = self.text.get("1.0", "end-1c")
        if not value.strip():
            return self.hide_input()
        try:
            self.enqueue(value)
        except Exception as exc:
            self.record_error(exc, input_feedback=True)
            return "break"
        self.text.delete("1.0", "end")
        self.hide_input()
        return "break"

    def show_settings(self):
        self.hide_input()
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.deiconify()
            self.settings_window.lift()
            return
        window = tk.Toplevel(self.root)
        window.withdraw()
        window.title("设置")
        window.configure(bg="#f5f7f4")
        window.resizable(False, False)
        self.settings_window = window
        frame = tk.Frame(window, bg="#f5f7f4", padx=self.px(23), pady=self.px(20))
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text="设置", font=("Microsoft YaHei UI", 15, "bold"),
                 bg="#f5f7f4", fg="#243b2e").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, self.px(18)))
        config = self.repository.snapshot()["config"]
        self.settings = {}
        tabs = ttk.Notebook(frame)
        tabs.grid(row=1, column=0, columnspan=2, sticky="nsew")
        panels = {}
        for key, title in (("general", "常规"), ("screen", "显示"), ("tools", "工具"), ("news", "新闻"), ("api", "大模型")):
            panel = tk.Frame(tabs, bg="#f5f7f4", padx=self.px(12), pady=self.px(14))
            tabs.add(panel, text=title)
            panels[key] = panel
        fields = (("general", 0, "capture_hotkey", "划选发送"), ("general", 1, "compose_hotkey", "唤醒输入框"),
                  ("general", 2, "note_limit", "便签上限"), ("general", 3, "device_address", "蓝牙地址"),
                  ("news", 0, "news_limit", "新闻上限"), ("news", 1, "gdelt_query", "GDELT 查询"),
                  ("news", 2, "news_proxy_mode", "新闻代理"), ("news", 3, "news_proxy_url", "代理地址"),
                  ("api", 1, "llm_base_url", "API 基础地址"), ("api", 2, "llm_model", "模型名称"),
                  ("api", 3, "llm_api_key", "API 密钥"))
        fields += (("screen", 0, "screen_theme", "显示风格"),
                   ("screen", 1, "screen_font_size", "字体大小"),
                   ("screen", 2, "screen_line_spacing", "行间距"))
        fields += (("tools",0,"screenshot_hotkey","截图识别"),("tools",1,"reminder_hotkey","设置提醒"))
        self.settings["llm_enabled"] = tk.BooleanVar(value=config["llm_enabled"])
        ttk.Checkbutton(panels["api"], text="启用中文新闻简讯", variable=self.settings["llm_enabled"]).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, self.px(10)))
        for panel_key, row, field, label in fields:
            panel = panels[panel_key]
            tk.Label(panel, text=label, font=("Microsoft YaHei UI", 10),
                     bg="#f5f7f4", fg="#48574d").grid(row=row, column=0, sticky="w", padx=(0, self.px(12)), pady=self.px(6))
            value = config.get(field, "")
            if field == "screen_theme":
                value = "黑底白字" if value == "dark" else "白底黑字"
            elif field == "news_proxy_mode":
                value = PROXY_MODES.get(value,PROXY_MODES["auto"])
            variable = tk.StringVar(value=str(value))
            self.settings[field] = variable
            if field=="news_proxy_mode":
                entry=ttk.Combobox(panel,textvariable=variable,state="readonly",width=25,values=tuple(PROXY_MODES.values()))
            elif field in ("screen_theme", "screen_font_size"):
                entry = ttk.Combobox(panel, textvariable=variable, state="readonly", width=25,
                                     values=("白底黑字", "黑底白字") if field == "screen_theme" else FONT_SIZES)
            elif field == "screen_line_spacing":
                entry = ttk.Spinbox(panel, from_=0, to=16, textvariable=variable, width=25)
            elif field in ("note_limit", "news_limit"):
                entry = ttk.Spinbox(panel, from_=1, to=MAX_NOTES if field == "note_limit" else MAX_NEWS,
                                    textvariable=variable, width=25)
            else:
                entry = ttk.Entry(panel, textvariable=variable, width=27, show="●" if field == "llm_api_key" else "")
            entry.grid(row=row, column=1, sticky="ew", ipady=self.px(3), pady=self.px(6))
            if field=="news_proxy_url":
                self.news_proxy_entry=entry
        def proxy_changed(*args):
            self.news_proxy_entry.configure(state="normal" if self.settings["news_proxy_mode"].get()==PROXY_MODES["custom"] else "disabled")
        self.settings["news_proxy_mode"].trace_add("write",proxy_changed)
        proxy_changed()
        self.settings["screen_show_hints"] = tk.BooleanVar(value=config["screen_show_hints"])
        ttk.Checkbutton(panels["screen"], text="显示屏幕底部的操作提示", variable=self.settings["screen_show_hints"]).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=self.px(10))
        self.settings["screenshot_enabled"]=tk.BooleanVar(value=config["screenshot_enabled"])
        ttk.Checkbutton(panels["tools"],text="启用截图识别（须先验证图片 API）",variable=self.settings["screenshot_enabled"]).grid(
            row=2,column=0,columnspan=2,sticky="w",pady=self.px(10))
        self.verify_button=ttk.Button(panels["api"],text="验证 API 与图片能力",command=self.verify_api)
        self.verify_button.grid(row=4,column=0,columnspan=2,sticky="w",pady=self.px(8))
        hints = {
            "general": "便签上限 1–20 条；满额时替换最早未置顶的便签。\nEnter 发送，Shift+Enter 换行。",
            "screen": "字体大小单位为像素，行间距为文字行之间的额外像素。\n设置同时用于便签和新闻，保存后同步到设备。\n关闭操作提示可增加阅读空间，实际按键功能不变。\n顶部电池图标按电池电压估算。",
            "tools": "截图：框选区域后，所选图片会发送到所填 API，\n识别关键信息并自动保存为便签；Esc 可取消。\n提醒：AI 理解最后一条已送达便签的时间，\n由你确认或修改后保存；无时间时自行填写。",
            "news": "新闻上限 1–50 条；HN / GDELT 各每 10 分钟刷新，错开 5 分钟。\n代理用于两源接口、新闻正文和相关报道检索。\n自定义示例：http://127.0.0.1:7897（HTTP/混合端口）\n自定义代理失败时不改用直连；不影响大模型 API 连接。",
            "api": ("支持 OpenAI 兼容的 Chat Completions 接口。\n生成 60–120 个汉字的简讯，再同步到小屏幕。\n启用后会将新闻资料发送到所填 API 服务。\n" +
                    ("密钥已加密保存；留空保持原密钥。" if self.api_keys.exists() else "密钥使用 Windows 本机加密保存。"))}
        for panel_key, hint in hints.items():
            tk.Label(panels[panel_key], text=hint, justify="left", wraplength=self.px(350),
                     bg="#f5f7f4", fg="#7b877e", font=("Microsoft YaHei UI", 9)).grid(
                         row=5, column=0, columnspan=2, sticky="w", pady=(self.px(16), self.px(7)))
        frame.columnconfigure(1, weight=1)
        self.settings_status = tk.StringVar(value="")
        tk.Label(frame, textvariable=self.settings_status, wraplength=self.px(355), justify="left",
                 bg="#f5f7f4", fg="#98604d", font=("Microsoft YaHei UI", 9)).grid(
                     row=2, column=0, columnspan=2, sticky="w", pady=self.px(4))
        actions = tk.Frame(frame, bg="#f5f7f4")
        actions.grid(row=3, column=0, columnspan=2, sticky="e", pady=(self.px(12), 0))
        ttk.Button(actions, text="取消", command=window.destroy).pack(side="left", padx=(0, self.px(8)))
        self.settings_save = ttk.Button(actions, text="保存", command=self.save_settings)
        self.settings_save.pack(side="left")
        window.bind("<Escape>", lambda event: window.destroy())
        window.bind("<Return>", lambda event: self.save_settings())
        window.update_idletasks()
        width, height = max(self.px(415), window.winfo_reqwidth()), max(self.px(365), window.winfo_reqheight())
        x, y = max(0, (window.winfo_screenwidth()-width)//2), max(0, (window.winfo_screenheight()-height)//2)
        window.geometry(f"{width}x{height}+{x}+{y}")
        window.deiconify()
        window.lift()
        window.focus_force()

    def save_settings(self):
        if self.pending_settings is not None:
            return
        settings = {key: variable.get().strip() if isinstance(variable.get(), str) else variable.get()
                    for key, variable in self.settings.items()}
        try:
            settings["note_limit"] = int(settings["note_limit"])
            settings["news_limit"] = int(settings["news_limit"])
            settings["screen_theme"] = {"白底黑字": "light", "黑底白字": "dark"}[settings["screen_theme"]]
            settings["screen_font_size"] = int(settings["screen_font_size"])
            settings["screen_line_spacing"] = int(settings["screen_line_spacing"])
            settings["news_proxy_mode"]={label:mode for mode,label in PROXY_MODES.items()}[settings["news_proxy_mode"]]
            validate_proxy(settings)
            display_packet(settings)
            api_key = settings.pop("llm_api_key")
            if not 1 <= settings["news_limit"] <= MAX_NEWS:
                raise ValueError(f"新闻上限应为 1–{MAX_NEWS}。")
            if settings["llm_enabled"]:
                api_endpoint(settings["llm_base_url"])
                if not settings["llm_model"] or not (api_key or self.api_keys.exists()):
                    raise ValueError("请填写模型名称和 API 密钥。")
            shortcuts=[settings[key] for key in ("capture_hotkey","compose_hotkey","reminder_hotkey")]
            if settings["screenshot_enabled"]:
                shortcuts.append(settings["screenshot_hotkey"])
                client,candidate=self.settings_ai()
                if not client.verified(candidate,"vision"):
                    raise ValueError("请先点击“大模型 → 验证 API 与图片能力”。")
            if len({parse_hotkey(key) for key in shortcuts})!=len(shortcuts):
                raise ValueError("快捷键必须互不相同。")
            if not 1 <= settings["note_limit"] <= MAX_NOTES:
                raise ValueError("便签上限应为 1–20。")
            snapshot = self.repository.snapshot()
            minimum = max(len(snapshot["notes"]), sum(op["op"] == "add" for op in snapshot["outbox"]))
            if settings["note_limit"] < minimum:
                raise ValueError(f"已有 {minimum} 条便签或待发送内容，请先删除后再降低上限。")
            if settings["news_limit"] < len(snapshot["news_pins"]):
                raise ValueError("请先取消部分新闻的置顶，再降低新闻上限。")
            if not settings["gdelt_query"] or len(settings["gdelt_query"]) > 512:
                raise ValueError("GDELT 查询应为 1–512 个字符。")
            if settings["device_address"]:
                import re
                if not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", settings["device_address"]):
                    raise ValueError("蓝牙地址格式应为 AA:BB:CC:DD:EE:FF；留空可自动发现设备。")
            self.pending_settings = settings
            self.pending_api_key = api_key
            self.settings_save.configure(state="disabled")
            self.settings_status.set("正在保存…")
            self.hotkeys.reload(settings)
        except Exception as exc:
            self.settings_status.set(str(exc))

    def settings_done(self, error=None):
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_save.configure(state="normal")
            self.settings_status.set(str(error or ""))
            if error is None:
                self.settings["llm_api_key"].set("")
                self.settings_window.destroy()

    def settings_ai(self):
        config=self.repository.snapshot()["config"]
        for field in ("llm_base_url","llm_model"):
            config[field]=self.settings[field].get().strip()
        key=self.settings["llm_api_key"].get().strip()
        if key:
            from types import SimpleNamespace
            return AiClient(SimpleNamespace(load=lambda:key),DATA),config
        return self.ai,config

    def background_ai(self,kind,work):
        def run():
            try:
                self.events.put((kind,work()))
            except Exception as exc:
                self.events.put((kind+"_error",str(exc)))
        threading.Thread(target=run,daemon=True).start()

    def verify_api(self):
        try:
            client,config=self.settings_ai()
            self.verify_button.configure(state="disabled")
            self.settings_status.set("正在验证文字接口和合成测试图…")
            self.background_ai("api_verified",lambda:client.verify(config))
        except Exception as exc:
            self.settings_status.set(str(exc))

    def start_screenshot(self):
        config=self.repository.snapshot()["config"]
        if not config["screenshot_enabled"] or not self.ai.verified(config,"vision"):
            self.show_settings()
            self.settings_status.set("请先在大模型页验证 API 的图片能力，并启用截图识别。")
            return
        if self.region_capture:
            return
        self.hide_input()
        try:
            self.region_capture=RegionCapture(self.root,
                lambda image:self.background_ai("screenshot_note",lambda:self.ai.screenshot_note(config,image)),
                lambda:setattr(self,"region_capture",None))
        except Exception as exc:
            self.record_error(exc)

    def start_reminder(self):
        note=self.repository.last_sent_note()
        if not note:
            self.record_error("还没有已发送的便签。")
            return
        if self.reminder_window and self.reminder_window.winfo_exists():
            self.reminder_window.destroy()
        window=tk.Toplevel(self.root)
        window.title("设置提醒")
        window.resizable(False,False)
        self.reminder_window=window
        token=time.time_ns()
        self.reminder_token=token
        panel=ttk.Frame(window,padding=18)
        panel.pack(fill="both",expand=True)
        ttk.Label(panel,text=note["text"][:240],wraplength=360,justify="left").pack(anchor="w",pady=(0,12))
        self.reminder_time=tk.StringVar()
        ttk.Label(panel,text="提醒时间（年-月-日 时:分）").pack(anchor="w")
        entry=ttk.Entry(panel,textvariable=self.reminder_time,width=35)
        entry.pack(fill="x",pady=7)
        self.reminder_hint=tk.StringVar(value="正在理解便签中的时间；也可以自行填写。")
        ttk.Label(panel,textvariable=self.reminder_hint,wraplength=360).pack(anchor="w",pady=7)
        merge_hint=tk.StringVar(value="同一分钟的多条便签会自动合并为一次提醒。")
        ttk.Label(panel,textvariable=merge_hint,wraplength=360).pack(anchor="w",pady=4)
        def update_merge(*args):
            count=self.reminders.pending_count(self.reminder_time.get(),note["id"])
            merge_hint.set(f"该时间已有 {count} 条便签，保存后合并为 {count+1} 条便签的一次提醒。" if count else
                           "同一分钟的多条便签会自动合并为一次提醒。")
        self.reminder_time.trace_add("write",update_merge)
        def confirm():
            try:
                self.reminders.confirm(note,self.reminder_time.get())
                self.bridge.wake()
                window.destroy()
            except Exception as exc:
                self.reminder_hint.set(str(exc))
        row=ttk.Frame(panel); row.pack(anchor="e",pady=(8,0))
        ttk.Button(row,text="取消",command=window.destroy).pack(side="left",padx=6)
        ttk.Button(row,text="确认提醒",command=confirm).pack(side="left")
        window.bind("<Escape>",lambda event:window.destroy())
        window.bind("<Return>",lambda event:confirm())
        window.lift(); window.focus_force(); entry.focus_set()
        config=self.repository.snapshot()["config"]
        if self.ai.verified(config,"text"):
            self.background_ai("reminder_proposal",lambda:(token,propose_reminder(self.ai,config,note)))
        else:
            self.reminder_hint.set("请先验证 API，或自行填写提醒时间后确认。")

    def show_note(self, note):
        self.hide_input()
        if self.note_window and self.note_window.winfo_exists():
            self.note_window.destroy()
        window = tk.Toplevel(self.root)
        window.withdraw()
        window.title("桌面小伴侣便签")
        window.overrideredirect(True)
        window.attributes("-toolwindow", True)
        window.configure(bg="#202923")
        self.note_window = window
        header = tk.Frame(window, bg="#202923")
        header.pack(fill="x", padx=self.px(16), pady=(self.px(12), 0))
        tk.Label(header, text="置顶便签" if note.get("pinned") else "便签", bg="#202923", fg="#a3c8b0",
                 font=("Microsoft YaHei UI", 10)).pack(side="left")
        close = tk.Label(header, text="×", bg="#202923", fg="#a3c8b0", cursor="hand2",
                         font=("Microsoft YaHei UI", 14))
        close.pack(side="right")
        close.bind("<Button-1>", lambda event: window.destroy())
        body = tk.Text(window, wrap="word", borderwidth=0, highlightthickness=0, font=("Microsoft YaHei UI", 12),
                       bg="#202923", fg="#edf2ee", selectbackground="#426a51", padx=self.px(16), pady=self.px(12))
        body.pack(fill="both", expand=True)
        body.insert("1.0", note["text"])
        body.configure(state="disabled")
        window.bind("<Escape>", lambda event: window.destroy())
        place_by_tray(window, self.tray, self.px(440), self.px(320), self.px(10))
        body.focus_force()

    def notify_reminder(self,reminder,send=False):
        self.show_note(reminder["note"])
        try:
            self.tray.notify(reminder["note"]["text"][:180],"便签提醒")
        except Exception:
            LOG.info("Tray notification unavailable; reminder preview is open")
        if send:
            existing={note["id"] for note in self.repository.snapshot()["notes"]}
            ids=[note["id"] for note in reminder["notes"] if note["id"] in existing]
            self.bridge.command({"op":"remind","ids":ids,"text":reminder["notes"][0]["text"]})

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "quit":
                    self.quit()
                    return
                if kind == "compose":
                    self.show_input()
                elif kind == "settings":
                    self.show_settings()
                elif kind=="screenshot":
                    self.start_screenshot()
                elif kind=="reminder":
                    self.start_reminder()
                elif kind=="device_reminders":
                    self.device_reminder_ids=value
                elif kind=="reminder_notice":
                    self.notify_reminder(value)
                elif kind=="screenshot_note":
                    try:
                        self.enqueue(value)
                    except Exception as exc:
                        self.record_error(exc)
                elif kind=="api_verified":
                    if self.settings_window and self.settings_window.winfo_exists():
                        self.verify_button.configure(state="normal")
                        client,config=self.settings_ai()
                        if value["signature"]==client.signature(config):
                            self.settings["screenshot_enabled"].set(True)
                            self.settings_status.set("文字与图片验证通过。保存后启用截图快捷键。")
                        else:
                            self.settings_status.set("配置已变化，请重新验证。")
                elif kind=="api_verified_error":
                    if self.settings_window and self.settings_window.winfo_exists():
                        self.verify_button.configure(state="normal")
                        self.settings_status.set(value)
                elif kind=="reminder_proposal":
                    token,proposal=value
                    if self.reminder_window and self.reminder_window.winfo_exists() and token==self.reminder_token:
                        if proposal["time"] and not self.reminder_time.get():
                            self.reminder_time.set(proposal["time"])
                        self.reminder_hint.set((proposal["reason"]+"；请确认时间是否合适。") if proposal["time"] else "未识别到明确的未来时间，请自行填写。")
                elif kind=="reminder_proposal_error":
                    if self.reminder_window and self.reminder_window.winfo_exists():
                        self.reminder_hint.set("AI 识别失败，请自行填写提醒时间。")
                elif kind == "capture":
                    try:
                        self.enqueue(value)  # Never opens a window or toast.
                    except Exception as exc:
                        self.record_error(exc)
                elif kind in ("capture_error", "error", "status", "brief_error","screenshot_note_error"):
                    self.record_error(value)
                elif kind == "connection":
                    changed = self.connected != bool(value[0])
                    self.connected = bool(value[0])
                    if self.connected:
                        self.error = ""
                    if changed:
                        self.tray.icon = icon_image(self.connected)
                    self.update_tray()
                elif kind in ("delivered", "changed", "settings_synced", "brief_ready"):
                    self.error = ""
                    self.update_tray()
                elif kind == "open_url":
                    webbrowser.open(value)
                elif kind == "open_note":
                    self.show_note(value)
                elif kind == "news_status":
                    LOG.info("%s", value)
                elif kind == "news_updated":
                    LOG.info("%s", value)
                elif kind == "hotkeys_ready" and self.pending_settings is not None:
                    settings, self.pending_settings = self.pending_settings, None
                    try:
                        if self.pending_api_key:
                            self.api_keys.save(self.pending_api_key)
                        self.pending_api_key = ""
                        self.repository.configure(**settings)
                        self.bridge.settings_changed()
                        LOG.info("Settings saved")
                        self.settings_done()
                        self.update_tray()
                    except Exception as exc:
                        self.hotkeys.reload(self.repository.snapshot()["config"])
                        self.settings_done(exc)
                elif kind == "hotkeys_error":
                    self.pending_settings = None
                    self.pending_api_key = ""
                    self.settings_done(value)
                    self.record_error(value)
        except queue.Empty:
            pass
        request = DATA / "control.request"
        try:
            stamp = request.stat().st_mtime_ns
            if stamp > self.last_command:
                self.last_command = stamp
                action = json.loads(request.read_text(encoding="utf-8")).get("action")
                LOG.info("Tray action: %s", action)
                if action == "settings":
                    self.show_settings()
                elif action == "compose":
                    self.show_input()
                elif action == "quit":
                    self.quit()
                    return
        except (OSError, ValueError):
            pass
        if hasattr(self,"reminders") and time.monotonic()>=self.reminders_due_at:
            self.reminders_due_at=time.monotonic()+1
            excluded=self.device_reminder_ids if self.connected else set()
            for reminder in self.reminders.take_due(exclude_ids=excluded):
                self.notify_reminder(reminder,send=self.connected)
        if not self.closed:
            self.root.after(80, self.poll)

    def quit(self):
        self.closed = True
        self.hotkeys.stop()
        self.bridge.stop()
        self.tray.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--background", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--settings", action="store_true", help="Open settings explicitly.")
    parser.add_argument("--input", action="store_true", help="Open the tray input explicitly.")
    args = parser.parse_args()
    action = "settings" if args.settings else "compose" if args.input else None
    DATA.mkdir(exist_ok=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    kernel.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel.CreateMutexW(None, False, "Local\\DeskCompanion_44B176BE8446")
    if ctypes.get_last_error() == 183:
        if action:
            (DATA / "control.request").write_text(json.dumps({"action": action}), encoding="utf-8")
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle(handle)
        return  # A normal second launch is silent as well.
    (ROOT / "logs").mkdir(exist_ok=True)
    handler = RotatingFileHandler(ROOT / "logs/pc-app.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler], format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        try:
            from .windows import user32
            user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:
            pass
        App(action).run()
    except Exception:
        LOG.exception("Application failed")  # No surprise error dialog.
    finally:
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle(handle)


if __name__ == "__main__":
    main()
