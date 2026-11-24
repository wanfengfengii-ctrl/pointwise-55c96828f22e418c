import sqlite3
from contextlib import closing

from capture.custody import CustodyState, CustodyStore
from capture.evidence import snapshot_custody
from capture.intake import StepState, step
from device.release import ReleaseFault, query_release
from device.slot import Outlet, SlotState
from tests import JOB, WorkflowCase, frame


class CustodyTests(WorkflowCase):
    def test_before_release(self):
        self.load([frame('P', '0c'), frame('A', '07')], [])
        original = self.current(Outlet.MAIN)
        self.conn.execute('''
            CREATE TRIGGER refuse_custody BEFORE INSERT ON custody
            BEGIN SELECT RAISE(ABORT, 'custody unavailable'); END
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'custody unavailable'):
            self.take(Outlet.MAIN)
        self.assertEqual(query_release(self.conn, original.receipt), SlotState.CURRENT)
        self.assertEqual(snapshot_custody(self.conn, JOB).records, ())
        self.conn.execute('DROP TRIGGER refuse_custody')
        pending = self.take(Outlet.MAIN, release_fault=ReleaseFault.BEFORE)
        self.assertEqual(pending.state, StepState.PENDING)
        with closing(sqlite3.connect(self.state_path)) as observer:
            record = CustodyStore(observer).get(original.receipt)
            self.assertEqual(record.state, CustodyState.PENDING)
            self.assertEqual(record.fragment.payload, b'\x0c')
            self.assertEqual(query_release(observer, original.receipt), SlotState.CURRENT)

    def test_release_not_applied(self):
        self.load([frame('P', '0c'), frame('A', '07')], [])
        original = self.current(Outlet.MAIN).receipt
        first = self.take(Outlet.MAIN, release_fault=ReleaseFault.BEFORE)
        self.assertEqual(first.state, StepState.PENDING)
        self.reopen()
        self.assertEqual(query_release(self.conn, original), SlotState.CURRENT)
        self.assertEqual(self.current(Outlet.MAIN).receipt, original)
        retried = step(self.conn, JOB, Outlet.MAIN, original, release_fault=ReleaseFault.AFTER)
        self.assertEqual(retried.state, StepState.PENDING)
        self.assertEqual(query_release(self.conn, original), SlotState.RELEASED)
        second = step(self.conn, JOB, Outlet.MAIN, original)
        self.assertEqual(second.state, StepState.RELEASED)
        self.assertEqual(second.record.fragment, first.record.fragment)
        self.assertEqual(self.current(Outlet.MAIN).kind, 'A')
        self.assertEqual(len(snapshot_custody(self.conn, JOB).records), 1)
