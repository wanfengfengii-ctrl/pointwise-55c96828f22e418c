from archive.readback import read
from capture.custody import CustodyState, CustodyStore
from capture.intake import StepState, step
from device.recording import attach_recording, recording_status
from device.release import ReleaseFault
from device.slot import Outlet
from jobs.registration import JobConflict, get_job, open as open_job
from tests import JOB, VOLUME, WorkflowCase, frame, recording


class JobTests(WorkflowCase):
    def test_same_spec_reopen(self):
        data = self.load([frame('P', '0c'), frame('A', '07')], [])
        original = get_job(self.conn, JOB)
        first = self.take(Outlet.MAIN, release_fault=ReleaseFault.AFTER)
        attach_recording(self.conn, JOB, data)
        self.reopen()
        self.assertEqual(open_job(self.conn, JOB, VOLUME, 1, 1), original)
        self.assertEqual(CustodyStore(self.conn).get(first.record.receipt), first.record)
        self.assertEqual(self.current(Outlet.MAIN).kind, 'A')

    def test_changed_spec_reject(self):
        self.load([frame('A', '07')], [])
        current = self.current(Outlet.MAIN)
        for volume, a_length, b_length in (('other', 1, 1), (VOLUME, 2, 2)):
            with self.subTest(volume=volume, length=a_length), self.assertRaises(JobConflict):
                open_job(self.conn, JOB, volume, a_length, b_length)
        with self.assertRaises(ValueError):
            attach_recording(self.conn, JOB, recording([frame('A', '11')], []))
        with self.assertRaises(ValueError):
            attach_recording(self.conn, JOB, recording([], [], volume='other'))
        self.assertEqual(self.current(Outlet.MAIN), current)
        self.assertEqual(get_job(self.conn, JOB).identity.volume_tag, VOLUME)
        self.assertEqual(get_job(self.conn, JOB).length, 1)

    def test_bad_spec_is_not_registered(self):
        for a, b in ((0, 0), (-1, -1), (1, 2), (True, True), (1.0, 1.0), (2**63, 2**63)):
            with self.subTest(a=a, b=b), self.assertRaises(ValueError):
                open_job(self.conn, JOB, VOLUME, a, b)
        with self.assertRaises(KeyError):
            get_job(self.conn, JOB)

    def test_reinitialize_pending(self):
        self.load([frame('P', '0c'), frame('A', '07')], [])
        pending = self.take(Outlet.MAIN, release_fault=ReleaseFault.AFTER)
        self.reopen()
        self.initialize()
        self.initialize()
        self.assertEqual(recording_status(self.conn, JOB).released, {pending.record.receipt})
        self.assertEqual(CustodyStore(self.conn).get(pending.record.receipt).state, CustodyState.PENDING)
        self.assertEqual(get_job(self.conn, JOB).identity.volume_tag, VOLUME)
        result = step(self.conn, JOB, Outlet.MAIN, pending.record.receipt)
        self.assertEqual(result.state, StepState.RELEASED)
        self.assertEqual(self.current(Outlet.MAIN).kind, 'A')
