# Copyright 2026 fanzipei
# SPDX-License-Identifier: Apache-2.0
"""Safe real-device clock / autonomous reminder acceptance with owned fixtures."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from tests.device_target import device_address, sdk_python_path
from bleak import BleakClient
from pc.protocol import Protocol
from pc.reminders import LOCAL
from tests.display_device import image_from_frame



@asynccontextmanager
async def connect():
    async with BleakClient(await device_address(),timeout=20,winrt={"use_cached_services":False}) as client:
        p=Protocol(client,lambda event:None)
        await p.start()
        await asyncio.sleep(.3)
        yield p


async def main():
    checks=[]
    owned=["clock-"+uuid.uuid4().hex[:30] for _ in range(2)]
    fixture_id=uuid.uuid4().hex
    async with connect() as p:
        initial=await p.request({"op":"state"})
        schedules=(await p.request({"op":"schedule_get"}))["items"]
        Path("backups/pre-clock-device-state.json").write_text(json.dumps(initial,ensure_ascii=False),encoding="utf-8")
        Path("backups/pre-clock-device-schedules.json").write_text(json.dumps(schedules),encoding="utf-8")
        if schedules or initial["reminder_active"]:
            raise RuntimeError("Pending user reminders: do not replace their schedules for a fixture")
        if len(initial["notes"])+2>initial["note_limit"]:
            raise RuntimeError("Insufficient free slots; do not evict user notes")
        display={key:initial["display"][key] for key in ("theme","show_hints","font_size","line_spacing")}
    try:
        async with connect() as p:
            assert (await p.request({"op":"time_sync","unix":int(time.time())}))["ok"]
            state=await p.request({"op":"state"})
            clock=state["clock"]
            assert clock["valid"] and clock["rtc_available"] and clock["source"]=="pc",clock
            assert abs(clock["unix"]-time.time())<3,clock
            assert clock["local"]==datetime.fromtimestamp(clock["unix"],LOCAL).strftime("%Y-%m-%d %H:%M:%S")
            assert not (await p.request({"op":"time_sync","unix":0}))["ok"]
            checks.append("PC epoch and Beijing display match; onboard RTC is available; invalid time rejected")
            for i,note_id in enumerate(owned):
                reply=await p.request({"op":"add","id":note_id,"created":int(time.time()),"text":f"时钟与提醒测试 {i+1}\n这是临时测试便签，测试后自动清理。"})
                assert reply["ok"] and not reply.get("evicted"),reply
            assert (await p.request({"op":"display_config","theme":"light","font_size":16,"show_hints":True,"line_spacing":7}))["ok"]
            await asyncio.sleep(2.5)
            frame=await p.request({"op":"frame_get"},timeout=90)
            image_from_frame(frame).save("logs/clock-status-header.png")
            await p.request({"op":"mode","mode":1})
            await p.request({"op":"remind","ids":owned,"text":"fallback"})
            started=time.monotonic(); phases=set()
            while time.monotonic()-started<3:
                state=await p.request({"op":"state"})
                assert state["mode"]==0 and state["reminder_count"]==2 and state["current_note_id"]==owned[0]
                phases.add(state["reminder_flash"])
                await asyncio.sleep(.15)
            assert phases=={False,True},phases
            while time.monotonic()-started<5.4:
                await asyncio.sleep(.2)
            state=await p.request({"op":"state"})
            assert state["current_note_id"]==owned[1],state["current_note_id"]
            await asyncio.sleep(2)
            assert not (await p.request({"op":"state"}))["reminder_flash"]
            await p.request({"op":"navigate","action":"next"})
            state=await p.request({"op":"state"})
            assert not state["reminder_active"] and state["current_note_id"]==owned[1]
            checks.append("reminder switches news to exact note, flashes, rotates merged notes, and dismisses without deleting")
            groups=[{"id":uuid.uuid4().hex,"due":int(time.time())+3600+i,"ids":owned} for i in range(64)]
            reply=await p.sync_reminders(groups)
            assert reply["ok"] and len(reply["ids"])==64,reply
            assert (await p.request({"op":"schedule_get"}))["items"]==groups
            token=uuid.uuid4().hex
            assert (await p.request({"op":"schedule_begin","token":token,"count":1}))["ok"]
            assert not (await p.request({"op":"schedule_commit","token":token}))["ok"]
            assert (await p.request({"op":"schedule_get"}))["items"]==groups
            checks.append("64 persistent schedule groups fit alongside user content; incomplete transaction leaves active schedules intact")
            due=int(time.time())+20
            group={"id":fixture_id,"due":due,"ids":owned}
            assert (await p.sync_reminders([group]))["ok"]
            await p.request({"op":"mode","mode":1})
        env=os.environ.copy()
        env["PYTHONPATH"]=str(sdk_python_path()/"Lib/site-packages")
        reset="import esptool; e=esptool.detect_chip('COM6',baud=115200); e.hard_reset(); e._port.close()"
        subprocess.run([sys._base_executable,"-c",reset],env=env,capture_output=True,check=True,timeout=20)
        await asyncio.sleep(2)
        async with connect() as p:
            rebooted=await p.request({"op":"state"})
            assert rebooted["clock"]["source"]=="rtc" and abs(rebooted["clock"]["unix"]-time.time())<3,rebooted["clock"]
            assert rebooted["scheduled_reminders"]==1 and not rebooted["reminder_active"]
            assert (await p.request({"op":"schedule_get"}))["items"]==[group]
        checks.append("hardware reset restores correct time from RTC and pending schedule from flash without PC time sync")
        while time.time()<due+2:
            await asyncio.sleep(min(.5,due+2-time.time()))
        async with connect() as p:
            state=await p.request({"op":"state"})
            assert state["reminder_active"] and state["mode"]==0 and state["current_note_id"] in owned
            assert state["reminder_count"]==2 and fixture_id in state["reminders_completed"] and state["scheduled_reminders"]==0
            await p.request({"op":"navigate","action":"next"})
            reply=await p.sync_reminders([group])
            assert reply["ok"] and not reply["ids"] and fixture_id in reply["completed"]
            assert not (await p.request({"op":"state"}))["reminder_active"]
            checks.append("disconnected device independently triggers and focuses merged notes at due time; reconnection does not replay fired group")
    finally:
        async with connect() as p:
            state=await p.request({"op":"state"})
            if state["reminder_active"]:
                await p.request({"op":"navigate","action":"next"})
            assert (await p.sync_reminders(schedules))["ok"]
            for note_id in owned:
                await p.request({"op":"delete","id":note_id})
            await p.request({"op":"display_config",**display})
            await p.request({"op":"mode","mode":initial["mode"]})
            final=await p.request({"op":"state"})
            assert final["notes"]==initial["notes"] and final["news_meta"]==initial["news_meta"]
            assert all(final["display"][k]==v for k,v in display.items())
            checks.append("owned fixtures removed; original notes/news/display/mode and schedules preserved")
    report={"passed":True,"checks":checks,"clock":final["clock"],"notes_preserved":len(final["notes"]),"news_preserved":final["news_count"]}
    Path("logs/clock-reminder-device-test.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=="__main__":
    asyncio.run(main())
