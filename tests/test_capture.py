from capture.custody import CustodyState, CustodyStore
from capture.intake import StepState, step
from capture.validation import InvalidFragment
from device.release import ReleaseFault, query_release
from device.slot import Outlet, SlotState
from tests import JOB, WorkflowCase, frame


class CaptureTests(WorkflowCase):
    def test_bad_checksum(self):
        self.load([frame('A', '07', checksum=0)], [frame('B', '0b')])
        current = self.current(Outlet.MAIN)
        backup = self.current(Outlet.BACKUP)
        with self.assertRaisesRegex(InvalidFragment, 'CRC32'):
            self.take(Outlet.MAIN)
        self.assertEqual(CustodyStore(self.conn).list_for_job(JOB), ())
        self.assertEqual(self.current(Outlet.MAIN), current)
        self.assertEqual(self.current(Outlet.BACKUP), backup)

    def test_wrong_volume(self):
        self.load([frame('A', '07', volume_tag='another-coil')], [])
        original = self.current(Outlet.MAIN)
        with self.assertRaisesRegex(InvalidFragment, 'volume tag'):
            self.take(Outlet.MAIN)
        self.assertEqual(self.current(Outlet.MAIN), original)
        self.assertIsNone(CustodyStore(self.conn).get(original.receipt))

    def test_invalid_shape(self):
        for index, payload in enumerate((frame('A', ''), frame('A', '0700'), frame('Q', '07'))):
            job = f'shape-{index}'
            with self.subTest(payload=payload):
                self.load([payload], [], job=job)
                original = self.current(Outlet.MAIN, job)
                with self.assertRaises(InvalidFragment):
                    self.take(Outlet.MAIN, job)
                self.assertEqual(self.current(Outlet.MAIN, job), original)
                self.assertEqual(CustodyStore(self.conn).list_for_job(job), ())

    def test_wrong_outlet(self):
        self.load([frame('P', '0c')], [frame('P', '0c')])
        main = self.current(Outlet.MAIN)
        backup = self.current(Outlet.BACKUP)
        with self.assertRaises(InvalidFragment):
            step(self.conn, JOB, Outlet.BACKUP, main.receipt)
        self.assertEqual(self.current(Outlet.MAIN), main)
        self.assertEqual(self.current(Outlet.BACKUP), backup)
        self.assertEqual(CustodyStore(self.conn).list_for_job(JOB), ())

    def test_release_reply_lost(self):
        self.load([frame('P', '0c'), frame('A', '07')], [])
        original = self.current(Outlet.MAIN)
        pending = self.take(Outlet.MAIN, release_fault=ReleaseFault.AFTER)
        self.assertEqual(pending.state, StepState.PENDING)
        self.assertEqual(pending.record.receipt, original.receipt)
        self.reopen()
        self.assertEqual(query_release(self.conn, original.receipt), SlotState.RELEASED)
        self.assertEqual(CustodyStore(self.conn).get(original.receipt).fragment.payload, b'\x0c')
        next_slot = self.current(Outlet.MAIN)
        self.assertEqual(next_slot.kind, 'A')
        first = step(self.conn, JOB, Outlet.MAIN, original.receipt)
        second = step(self.conn, JOB, Outlet.MAIN, original.receipt)
        self.assertEqual(first.state, StepState.RELEASED)
        self.assertEqual(first.record.state, CustodyState.VERIFIED)
        self.assertEqual(first.record.fragment, pending.record.fragment)
        self.assertEqual(first, second)
        self.assertEqual(self.current(Outlet.MAIN), next_slot)
