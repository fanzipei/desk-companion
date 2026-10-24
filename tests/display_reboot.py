# Copyright 2026 fanzipei
# SPDX-License-Identifier: Apache-2.0
"""Verify screen preferences persist across a hardware reset; restore originals."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from tests.device_target import device_address, sdk_python_path
from bleak import BleakClient
from pc.protocol import Protocol



async def main():
    target={"theme":"dark","show_hints":False,"font_size":24,"line_spacing":12}
    async with BleakClient(await device_address(),timeout=15,winrt={"use_cached_services":False}) as client:
        p=Protocol(client,lambda event:None)
        await p.start()
        await asyncio.sleep(.3)
        initial=await p.request({"op":"state"})
        original={key:initial["display"][key] for key in target}
        assert (await p.request({"op":"display_config",**target}))["ok"]
    try:
        env=os.environ.copy()
        env["PYTHONPATH"]=str(sdk_python_path()/"Lib/site-packages")
        reset="import esptool; e=esptool.detect_chip('COM6',baud=115200); e.hard_reset(); e._port.close()"
        subprocess.run([sys._base_executable,"-c",reset],env=env,check=True,capture_output=True,timeout=20)
        await asyncio.sleep(2)
        async with BleakClient(await device_address(),timeout=15,winrt={"use_cached_services":False}) as client:
            p=Protocol(client,lambda event:None)
            await p.start()
            await asyncio.sleep(.3)
            after=await p.request({"op":"state"})
            assert all(after["display"][key]==value for key,value in target.items())
            assert after["notes"]==initial["notes"] and after["news_meta"]==initial["news_meta"]
    finally:
        async with BleakClient(await device_address(),timeout=15,winrt={"use_cached_services":False}) as client:
            p=Protocol(client,lambda event:None)
            await p.start()
            await asyncio.sleep(.3)
            assert (await p.request({"op":"display_config",**original}))["ok"]
    report={"preferences_survived_hardware_reset":True,"user_notes_news_preserved":True,
            "original_preferences_restored":True}
    Path("logs/display-reboot-test.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report))


if __name__=="__main__":
    asyncio.run(main())
