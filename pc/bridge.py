import asyncio
import logging
import queue
import threading
import time

from bleak import BleakClient, BleakScanner

from .core import HackerNews, GdeltDoc
from .news_schedule import NewsSchedule
from .protocol import Protocol, SERVICE
from .secrets import ApiKeyStore
from .summarizer import Summarizer, config_signature
from .display import display_packet
from .clock_sync import ClockSync

LOG = logging.getLogger(__name__)


class Bridge:
    def __init__(self, repository, events, reminders=None):
        self.repository, self.events = repository, events
        self.reminders=reminders
        initial_config=repository.snapshot()["config"]
        self.network_signature=(initial_config.get("news_proxy_mode","auto"),initial_config.get("news_proxy_url",""))
        self.stopping = threading.Event()
        self.news_refresh = threading.Event()
        self.summary_wakeup = threading.Event()
        self.summary_settings_version = 0
        self.summarizer = Summarizer(ApiKeyStore(repository.directory))
        self.loop = self.wakeup = None
        self.retry_version = 0
        self.commands = queue.Queue()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.news_thread = threading.Thread(target=self.news_run, daemon=True)
        self.summary_thread = threading.Thread(target=self.summary_run, daemon=True)

    def start(self):
        self.thread.start()
        self.news_thread.start()
        self.summary_thread.start()

    def wake(self):
        self.retry_version += 1
        self.summary_wakeup.set()
        if self.loop and self.wakeup and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.wakeup.set)

    def refresh_news(self):
        self.news_refresh.set()

    def settings_changed(self):
        self.summary_settings_version += 1
        config=self.repository.snapshot()["config"]
        network=(config["news_proxy_mode"],config["news_proxy_url"])
        if network!=self.network_signature:
            self.network_signature=network
            self.news_refresh.set()
        self.wake()

    def command(self, value):
        self.commands.put(value)
        self.wake()

    def news_run(self):
        apis = {"hn": HackerNews(), "gdelt": GdeltDoc()}
        labels = {"hn": "Hacker News", "gdelt": "GDELT"}
        interval = int(self.repository.snapshot()["config"]["news_interval"])
        schedule = NewsSchedule(time.monotonic(), interval)
        while not self.stopping.is_set():
            if self.news_refresh.is_set():
                self.news_refresh.clear()
                schedule.reset(time.monotonic())
            source = schedule.due(time.monotonic())
            if source is None:
                self.news_refresh.wait(min(1, schedule.delay(time.monotonic())))
                continue
            schedule.started(source, time.monotonic())
            self.events.put(("news_status", f"正在获取 {labels[source]}…"))
            try:
                snapshot = self.repository.snapshot()
                config = snapshot["config"]
                options = {"config":config,**({"query": config["gdelt_query"]} if source == "gdelt" else {})}
                stories = apis[source].fetch(int(config["news_limit"]), **options)
                self.repository.update_news_source(source, stories, time.time())
                LOG.info("News refreshed source=%s count=%d", source, len(stories))
                self.events.put(("news_updated", f"{labels[source]} 已更新 {len(stories)} 条 · 每源 10 分钟，错开 5 分钟"))
                self.wake()
            except Exception as exc:
                if hasattr(exc, "retry_after"):
                    schedule.defer(source, time.monotonic(), exc.retry_after)
                LOG.warning("News refresh failed source=%s: %s", source, exc)
                self.events.put(("news_status", str(exc)))

    def summary_run(self):
        failed = {}
        provider_next = 0
        active_signature = None
        settings_version = -1
        while not self.stopping.is_set():
            config = self.repository.snapshot()["config"]
            signature = config_signature(config)
            if signature != active_signature or settings_version != self.summary_settings_version:
                active_signature, provider_next = signature, 0
                settings_version = self.summary_settings_version
                failed.clear()
            if config["llm_enabled"] and config["llm_base_url"] and config["llm_model"] and time.monotonic() >= provider_next:
                for story in self.repository.pending_summaries(signature):
                    if self.stopping.is_set():
                        return
                    if time.monotonic() < failed.get((signature, story["id"]), 0):
                        continue
                    try:
                        brief, basis = self.summarizer.summarize(story, config)
                        # Settings may have changed while the remote model was running.
                        current = self.repository.snapshot()["config"]
                        if config_signature(current) != signature or not current["llm_enabled"]:
                            break
                        self.repository.save_summary(story["id"], brief, basis, signature)
                        self.events.put(("brief_ready", "中文简讯已生成"))
                        self.wake()
                    except Exception as exc:
                        failed[(signature, story["id"])] = time.monotonic()+300
                        provider_next = time.monotonic()+60
                        self.events.put(("brief_error", str(exc)))
                        LOG.warning("News brief failed: %s", type(exc).__name__)
                        # Authentication/provider outages should not issue one request per story.
                        break
                    if self.stopping.wait(0.5):
                        return
            self.summary_wakeup.wait(5)
            self.summary_wakeup.clear()

    def event(self, value):
        if value.get("event")=="reminder_due" and self.reminders:
            for item in self.reminders.mark_device_fired({value.get("id")}):
                self.events.put(("reminder_notice",item))
            self.wake()
        elif value.get("event") == "open_note":
            note = self.repository.note(value.get("id"))
            if note:
                self.events.put(("open_note", note))
        elif value.get("event") == "open_news":
            url = self.repository.news_url(value.get("id"))
            if url:
                self.events.put(("open_url", url))
            else:
                self.events.put(("status", "这条新闻已不在 PC 缓存中，请刷新新闻后重试。"))
        else:
            self.repository.apply_event(value)
            self.events.put(("changed", None))
            self.wake()

    def run(self):
        try:
            asyncio.run(self.main())
        except Exception:
            LOG.exception("BLE worker stopped")
            self.events.put(("error", "蓝牙后台程序已停止，请重新启动程序。"))

    async def main(self):
        self.loop, self.wakeup = asyncio.get_running_loop(), asyncio.Event()
        while not self.stopping.is_set():
            self.events.put(("connection", (False, "正在连接小伴侣…")))
            try:
                config = self.repository.snapshot()["config"]
                address = config["device_address"].strip()
                if not address:
                    device = await BleakScanner.find_device_by_name("DeskCompanion", timeout=8)
                    if device is None:
                        raise RuntimeError("没有发现 DeskCompanion，请开机并靠近电脑。")
                else:
                    device = address
                async with BleakClient(device, timeout=15, winrt={"use_cached_services": False}) as client:
                    if client.services.get_service(SERVICE) is None:
                        raise RuntimeError("设备尚未运行桌面小伴侣固件。")
                    protocol = Protocol(client, self.event)
                    await protocol.start()
                    clock_sync=ClockSync()
                    if await clock_sync.update(protocol):
                        LOG.info("Device clock synchronized")
                    state = await protocol.request({"op": "state"})
                    if not state.get("ok"):
                        raise RuntimeError("设备状态读取失败。")
                    self.repository.apply_state(state)
                    if self.reminders:
                        for item in self.reminders.mark_device_fired(set(state.get("reminders_completed",[]))):
                            self.events.put(("reminder_notice",item))
                    cached_news = await protocol.request({"op": "news_get"}, timeout=90)
                    if cached_news.get("ok"):
                        self.repository.adopt_device_news(cached_news["items"])
                    self.events.put(("connection", (True, "小伴侣已连接")))
                    self.events.put(("changed", None))
                    news_sent = None
                    blocked = None
                    attempted_limit = None
                    active_limit = None
                    limit_retry_version = -1
                    attempted_display = None
                    display_retry_version = -1
                    display_synced = False
                    scheduled=None
                    while client.is_connected and not self.stopping.is_set():
                        clock_result=await clock_sync.update(protocol)
                        if clock_result is True:
                            LOG.info("Device clock synchronized")
                        elif clock_result is False:
                            LOG.warning("Device clock sync will retry")
                        if self.reminders:
                            existing={note["id"] for note in self.repository.snapshot()["notes"]}
                            groups=self.reminders.device_groups(existing)
                            if groups!=scheduled:
                                reply=await protocol.sync_reminders(groups)
                                if not reply.get("ok"):
                                    raise RuntimeError("设备提醒日程同步失败。")
                                scheduled=groups
                                for item in self.reminders.mark_device_fired(set(reply.get("completed",[]))):
                                    self.events.put(("reminder_notice",item))
                                self.events.put(("device_reminders",set(reply.get("ids",[item["id"] for item in groups]))))
                        screen = display_packet(self.repository.snapshot()["config"])
                        if screen != attempted_display or (not display_synced and display_retry_version != self.retry_version):
                            attempted_display = screen
                            display_retry_version = self.retry_version
                            reply = await protocol.request(screen)
                            display_synced = bool(reply.get("ok"))
                            self.events.put(("settings_synced", "display") if display_synced else
                                            ("status", "屏幕设置未同步：" + str(reply.get("error", "连接异常"))))
                        desired_limit = int(self.repository.snapshot()["config"]["note_limit"])
                        if desired_limit != attempted_limit:
                            attempted_limit = desired_limit
                            reply = await protocol.request({"op": "config", "note_limit": desired_limit})
                            if reply.get("ok"):
                                active_limit = desired_limit
                                self.events.put(("settings_synced", desired_limit))
                            else:
                                active_limit = None
                                message = (f"设备已有 {reply.get('minimum', 0)} 条便签，请提高上限或先删除便签。"
                                           if reply.get("error") == "limit_below_count" else "便签上限未同步，请检查连接。")
                                self.events.put(("status", message))
                        elif active_limit is None:
                            # A physical deletion can make a previously rejected limit valid.
                            # Retry after a wake/event instead of leaving the outbox blocked.
                            if limit_retry_version != self.retry_version:
                                attempted_limit = None
                        limit_retry_version = self.retry_version
                        try:
                            command = self.commands.get_nowait()
                        except queue.Empty:
                            command = None
                        if command:
                            reply = await protocol.request(command)
                            if not reply.get("ok"):
                                self.events.put(("status", "设备操作失败：" + str(reply.get("error"))))
                            else:
                                state = await protocol.request({"op": "state"})
                                self.repository.apply_state(state)
                                self.events.put(("changed", None))
                        operation = self.repository.next_operation()
                        if operation and active_limit == desired_limit and blocked != (operation["qid"], self.retry_version):
                            packet = {k: v for k, v in operation.items() if k != "qid"}
                            reply = await protocol.request(packet)
                            if reply.get("ok") or reply.get("error") == "not_found":
                                # Refresh device state before retiring the outbox entry. A retry is idempotent.
                                state = await protocol.request({"op": "state"})
                                if not state.get("ok"):
                                    raise RuntimeError("同步设备状态失败。")
                                self.repository.apply_state(state)
                                self.repository.acknowledge(operation["qid"])
                                self.events.put(("delivered", operation))
                                blocked = None
                                continue
                            if reply.get("error") == "all_pinned":
                                blocked = (operation["qid"], self.retry_version)
                                self.events.put(("status", f"设备的 {desired_limit} 条便签均已置顶；请删除或取消置顶，待发送便签会继续同步。"))
                            else:
                                raise RuntimeError("设备未保存便签：" + str(reply.get("error")))
                        news = self.repository.news_packet()
                        news_revision = news["revision"]
                        waiting_for_brief = (not news["items"] and self.repository.snapshot()["config"]["llm_enabled"]
                                             and bool(self.repository.snapshot()["news"]))
                        if news_revision != news_sent and not waiting_for_brief:
                            reply = await protocol.sync_news(news)
                            if not reply.get("ok"):
                                raise RuntimeError("新闻同步失败：" + str(reply.get("error")))
                            news_sent = news_revision
                            self.events.put(("news_status", "新闻已同步到小伴侣"))
                        self.wakeup.clear()
                        try:
                            await asyncio.wait_for(self.wakeup.wait(), 1)
                        except TimeoutError:
                            pass
            except Exception as exc:
                LOG.info("BLE reconnect: %s", exc)
                self.events.put(("connection", (False, str(exc))))
            for _ in range(30):
                if self.stopping.is_set():
                    break
                await asyncio.sleep(0.1)
        self.events.put(("connection", (False, "后台已停止")))

    def stop(self):
        self.stopping.set()
        self.news_refresh.set()
        self.summary_wakeup.set()
        self.wake()
