from capture.intake import StepState, step
from device.recording import read_current, recording_status
from device.release import ReleaseFault, query_release
from device.slot import Outlet, SlotState
from tests import JOB, WorkflowCase, frame


class DeviceTests(WorkflowCase):
    def test_independent_outlets(self):
        self.load([frame('P', '0c'), frame('A', '07')], [frame('P', '0c')])
        main_first = self.current(Outlet.MAIN).receipt
        backup_first = self.current(Outlet.BACKUP).receipt
        self.assertEqual(main_first.ordinal, backup_first.ordinal)
        self.assertNotEqual(main_first, backup_first)
        self.assertEqual(self.take(Outlet.MAIN, release_fault=ReleaseFault.AFTER).state, StepState.PENDING)
        self.reopen()
        self.assertEqual(query_release(self.conn, main_first), SlotState.RELEASED)
        self.assertEqual(query_release(self.conn, backup_first), SlotState.CURRENT)
        next_main = self.current(Outlet.MAIN)
        self.assertEqual(next_main.receipt.ordinal, 1)
        self.assertEqual(step(self.conn, JOB, Outlet.MAIN, main_first).state, StepState.RELEASED)
        self.assertEqual(self.current(Outlet.MAIN), next_main)
        self.assertEqual(self.take(Outlet.BACKUP).state, StepState.RELEASED)
        self.assertEqual(query_release(self.conn, backup_first), SlotState.RELEASED)
        self.assertEqual(query_release(self.conn, next_main.receipt), SlotState.CURRENT)
        self.assertIsNone(read_current(self.conn, JOB, Outlet.BACKUP))

    def test_exhaustion_without_end_event(self):
        self.load([frame('A', '07')], [], main_end=False)
        self.assertEqual(self.take(Outlet.MAIN).state, StepState.RELEASED)
        self.assertIsNone(read_current(self.conn, JOB, Outlet.MAIN))
        self.assertEqual(step(self.conn, JOB, Outlet.MAIN).state, StepState.WAITING)
        self.assertEqual(step(self.conn, JOB, Outlet.BACKUP).state, StepState.ENDED)
        self.assertFalse(recording_status(self.conn, JOB).ended)
