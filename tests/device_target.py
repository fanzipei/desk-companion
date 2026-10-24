# Copyright 2026 fanzipei
# SPDX-License-Identifier: Apache-2.0
"""Explicit hardware-test target; no developer's device address is committed."""
import json
import os
from pathlib import Path
from bleak import BleakScanner


async def device_address():
    address=os.environ.get("DESK_COMPANION_ADDRESS","").strip()
    state=Path(__file__).resolve().parents[1]/"data"/"state.json"
    if not address and state.exists():
        address=json.loads(state.read_text(encoding="utf-8"))["config"].get("device_address","").strip()
    if address:
        return address
    device=await BleakScanner.find_device_by_name("DeskCompanion",timeout=10)
    if device is None:
        raise RuntimeError("未发现测试设备；可通过 DESK_COMPANION_ADDRESS 指定蓝牙地址。")
    return device


def sdk_python_path():
    return Path(os.environ.get("IDF_PYTHON_ENV_PATH",Path.home()/"esp/tools/python_env/idf5.3_py3.12_env"))
