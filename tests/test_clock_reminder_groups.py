# Copyright 2026 fanzipei
# SPDX-License-Identifier: Apache-2.0
from datetime import datetime,timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock
from pc.clock_sync import ClockSync,INTERVAL
from pc.reminders import ReminderStore,LOCAL
from pc.protocol import Protocol


class ClockSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_schedule_transfer_publishes_only_after_all_chunks(self):
        protocol=object.__new__(Protocol)
        protocol.request=AsyncMock(return_value={"ok":True})
        items=[{"id":str(i),"due":1792900000,"ids":["a"]} for i in range(12)]
        await protocol.sync_reminders(items)
        packets=[call.args[0] for call in protocol.request.call_args_list]
        self.assertEqual([p["op"] for p in packets],["schedule_begin"]+["schedule_chunk"]*3+["schedule_commit"])
        self.assertEqual([p["offset"] for p in packets[1:-1]],[0,5,10])
        self.assertEqual([item for p in packets[1:-1] for item in p["items"]],items)
        self.assertEqual(len({p["token"] for p in packets}),1)
        protocol.request=AsyncMock(side_effect=[{"ok":True},{"ok":False}])
        self.assertFalse((await protocol.sync_reminders(items))["ok"])
        self.assertEqual(protocol.request.call_count,2)

    async def test_syncs_on_connect_and_approximately_every_four_hours(self):
        protocol=type("Protocol",(),{})()
        protocol.request=AsyncMock(return_value={"ok":True})
        sync=ClockSync()
        self.assertTrue(await sync.update(protocol,0,1792810000))
        self.assertIsNone(await sync.update(protocol,INTERVAL-1,1792820000))
        self.assertTrue(await sync.update(protocol,INTERVAL,1792824400))
        self.assertEqual(protocol.request.call_count,2)
        self.assertEqual(protocol.request.call_args.args[0],{"op":"time_sync","unix":1792824400})
        self.assertTrue(await ClockSync().update(protocol,INTERVAL+10,1792824410))

    async def test_failed_sync_retries_later(self):
        protocol=type("Protocol",(),{})()
        protocol.request=AsyncMock(side_effect=[{"ok":False},{"ok":True}])
        sync=ClockSync()
        self.assertFalse(await sync.update(protocol,0,1792810000))
        self.assertIsNone(await sync.update(protocol,59,1792810059))
        self.assertTrue(await sync.update(protocol,60,1792810060))


class GroupedReminderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store=ReminderStore(self.temp.name)
        self.when=(datetime.now(LOCAL)+timedelta(days=1)).strftime("%Y-%m-%d %H:%M")

    def test_same_minute_notes_merge_and_same_note_is_not_duplicated(self):
        first={"id":"a","text":"first"}; second={"id":"b","text":"second"}
        a=self.store.confirm(first,self.when)
        b=self.store.confirm(second,self.when)
        self.store.confirm(first,self.when)
        self.assertEqual(a["id"],b["id"])
        self.assertEqual(len(self.store.items),1)
        self.assertEqual([n["id"] for n in b["notes"]],["a","b"])
        self.assertIn("first",b["note"]["text"]); self.assertIn("second",b["note"]["text"])
        self.assertEqual(self.store.pending_count(self.when,"a"),1)
        due=ReminderStore(self.temp.name).take_due(b["due"]+1)
        self.assertEqual(len(due),1); self.assertEqual(len(due[0]["notes"]),2)
        self.assertFalse(ReminderStore(self.temp.name).take_due(b["due"]+2))

    def test_distinct_times_stay_separate(self):
        self.store.confirm({"id":"a","text":"a"},self.when)
        other=(datetime.strptime(self.when,"%Y-%m-%d %H:%M")+timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M")
        self.store.confirm({"id":"b","text":"b"},other)
        self.assertEqual(len(self.store.items),2)

    def test_device_ack_suppresses_pc_duplicate_and_survives_restart(self):
        item=self.store.confirm({"id":"a","text":"first"},self.when)
        self.assertFalse(self.store.take_due(item["due"]+1,exclude_ids={item["id"]}))
        self.assertEqual(len(self.store.mark_device_fired({item["id"]})),1)
        self.assertFalse(self.store.mark_device_fired({item["id"]}))
        self.assertFalse(ReminderStore(self.temp.name).take_due(item["due"]+1))
        self.assertFalse(self.store.device_groups({"a"}))

    def test_device_only_receives_existing_note_ids_with_overflow_left_for_pc(self):
        first=datetime.strptime(self.when,"%Y-%m-%d %H:%M")
        for i in range(66):
            self.store.confirm({"id":"a","text":"first"},(first+timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M"))
        self.assertFalse(self.store.device_groups({"other"}))
        groups=self.store.device_groups({"a"})
        self.assertEqual(len(groups),64)
        self.assertTrue(all(g["ids"]==["a"] for g in groups))
        due=self.store.take_due(self.store.items[-1]["due"]+1,exclude_ids={g["id"] for g in groups})
        self.assertEqual(len(due),2)

    def test_old_pending_records_merge_on_load_without_losing_snapshots(self):
        item=self.store.confirm({"id":"a","text":"a"},self.when)
        old=[{"id":"x","note":{"id":"a","text":"a"},"due":item["due"],"fired":False},
             {"id":"y","note":{"id":"b","text":"b"},"due":item["due"],"fired":False}]
        self.store.path.write_text(json.dumps(old))
        loaded=ReminderStore(self.temp.name)
        self.assertEqual(len(loaded.items),1)
        self.assertEqual(len(loaded.items[0]["notes"]),2)
