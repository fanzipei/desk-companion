# Copyright 2026 fanzipei
# SPDX-License-Identifier: Apache-2.0
"""Read actual LCD framebuffers over BLE and validate display preferences safely."""
import asyncio
import json
from pathlib import Path
import time
import uuid

from tests.device_target import device_address, sdk_python_path
from bleak import BleakClient
from PIL import Image, ImageChops, ImageOps
from pc.protocol import Protocol


def image_from_frame(frame):
    raw=bytes.fromhex(frame["pixels"])
    width,height,stride=frame["width"],frame["height"],frame["stride"]
    one_is_white=frame.get("one_is_white",False)
    image=Image.new("L",(width,height),0 if one_is_white else 255)
    pixels=image.load()
    for y in range(height):
        for x in range(width):
            if raw[(y//8)*stride+x] & (1<<(y%8)):
                pixels[x,y]=255 if one_is_white else 0
    return image.transpose(Image.Transpose.ROTATE_90)


async def main():
    checks=[]
    owned="display-"+uuid.uuid4().hex[:28]
    async with BleakClient(await device_address(),timeout=15,winrt={"use_cached_services":False}) as client:
        p=Protocol(client,lambda event:None)
        await p.start()
        await asyncio.sleep(.3)
        initial=await p.request({"op":"state"})
        previous={key:initial["display"][key] for key in ("theme","show_hints","font_size","line_spacing")}
        Path("backups/pre-display-settings.json").write_text(json.dumps(previous),encoding="utf-8")
        if len(initial["notes"])>=initial["note_limit"]:
            raise RuntimeError("No free note slot for a safe display fixture; user notes left unchanged")
        try:
            added=await p.request({"op":"add","id":owned,"created":int(time.time()),"text":
                "显示设置测试。\n白底黑字与黑底白字。\n字体与行间距可以调整。\nBOOT 下一条，KEY 上一条。\n状态栏显示电池电量。"})
            assert added["ok"] and not added.get("evicted"), added
            await asyncio.sleep(2.3)
            async def configure(**options):
                reply=await p.request({"op":"display_config",**options})
                assert reply["ok"], reply
                for key,value in options.items():
                    assert reply["display"][key]==value
                return reply["display"]
            async def capture(name):
                frame=await p.request({"op":"frame_get"},timeout=90)
                assert frame["ok"]
                image=image_from_frame(frame)
                assert image.size==(400,300)
                image.save(Path("logs")/f"display-{name}.png")
                return image
            await configure(theme="light",show_hints=True,font_size=16,line_spacing=7)
            light=await capture("light-16")
            await configure(theme="dark")
            dark=await capture("dark-16")
            # Ignore the live battery number; the entire reading area must invert exactly.
            diff=ImageChops.difference(light.crop((0,50,400,260)),ImageOps.invert(dark.crop((0,50,400,260))))
            assert diff.getbbox() is None, "Theme did not invert the reading area"
            checks.append("actual light/dark framebuffer pixels are inverse")
            await configure(theme="light",show_hints=False)
            hidden=await capture("no-hints")
            assert hidden.crop((0,262,400,300)).getextrema()==(255,255), "Operation hints remain visible"
            state=await p.request({"op":"state"})
            no_hints_lines=state["display"]["visible_lines"]
            await configure(show_hints=True)
            assert no_hints_lines>=(await p.request({"op":"state"}))["display"]["visible_lines"]
            checks.append("hints hidden and reading space expanded without changing buttons")
            pages={}; fonts={}
            for size in (12,16,24,32):
                await configure(font_size=size,line_spacing=4,show_hints=False,theme="light")
                state=await p.request({"op":"state"})
                pages[size]=state["content_pages"]
                image=await capture(f"font-{size}")
                fonts[size]=image
                assert image.crop((12,53,388,260)).getextrema()==(0,255), "Body text missing"
            for size,base in ((24,12),(32,16)):
                expected=fonts[base].crop((16,52,200,52+base)).resize((368,size),Image.Resampling.NEAREST)
                actual=fonts[size].crop((16,53,384,53+size))
                assert ImageChops.difference(expected,actual).getbbox() is None, "Scaled font lost glyph pixels"
            assert pages[32]>=pages[12]
            await configure(theme="dark",font_size=32,line_spacing=16,show_hints=False)
            await capture("dark-large")
            for invalid in ({"font_size":20},{"line_spacing":17},{"show_hints":"false"},{"theme":"invalid"}):
                rejected=await p.request({"op":"display_config",**invalid})
                assert not rejected["ok"]
            state=await p.request({"op":"state"})
            assert state["display"]["font_size"]==32 and state["display"]["line_spacing"]==16
            checks.append("four font sizes and spacing preserve paged text; invalid settings are rejected")
            await p.request({"op":"mode","mode":1})
            await configure(theme="light",font_size=24,line_spacing=7,show_hints=False)
            await capture("news-24")
            assert (await p.request({"op":"state"}))["news_count"]==initial["news_count"]
            battery=state["battery"]
            assert battery["valid"] and battery["calibrated"]
            assert isinstance(battery["percent"],int) and 0<=battery["percent"]<=100
            assert 2500<=battery["millivolts"]<=4450
            checks.append("calibrated battery voltage and integer percent read from real hardware")
        finally:
            await p.request({"op":"delete","id":owned})
            await p.request({"op":"display_config",**previous})
            await p.request({"op":"mode","mode":initial["mode"]})
        final=await p.request({"op":"state"})
        assert final["notes"]==initial["notes"]
        assert final["news_meta"]==initial["news_meta"]
        assert all(final["display"][key]==value for key,value in previous.items())
        checks.append("original notes/news/mode/display preferences restored")
    report={"passed":True,"checks":checks,"sample_battery":battery,"timestamp":time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    Path("logs/display-device-test.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=="__main__":
    asyncio.run(main())
