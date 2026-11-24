import sqlite3
from contextlib import closing

from archive.readback import VolumeNotAvailable, read
from capture.custody import CustodyStore
from capture.intake import accept_shared_member, step
from capture.validation import InvalidFragment
from decision.completeness import RecordingUnfinished
from decision.sealing import seal
from device.recording import (
    attach_recording, attach_shared_slot, read_shared_member, rewrite_shared_member,
)
from device.release import query_shared_release, release_shared_slot, release_slot
from device.slot import Outlet
from tests import JOB, VOLUME, WorkflowCase, frame, recording
from zlib import crc32

SLOT = 'carrier-1'


def members(x='07', y='09', *, x_checksum=None, y_checksum=None):
    return [
        {'job_id': 'X', 'recording_id': 'rx',
         'main': frame('A', x, checksum=x_checksum),
         'backup': {'frames': [frame('P', '0c')], 'end': True}},
        {'job_id': 'Y', 'recording_id': 'ry',
         'main': frame('A', y, checksum=y_checksum),
         'backup': {'frames': [frame('P', '0c')], 'end': True}},
    ]


class SharedSlotCase(WorkflowCase):
    def setUp(self):
        super().setUp()
        self.open_job('X', 'coil-x')
        self.open_job('Y', 'coil-y')

    def open_job(self, job, volume, length=1):
        from jobs.registration import open as open_job
        open_job(self.conn, job, volume, length, length)

    def attach(self, data=None, slot=SLOT):
        return attach_shared_slot(self.conn, slot, members() if data is None else data)

    def drain_backups(self, *jobs):
        for job in jobs:
            self.assertEqual(step(self.conn, job, 'backup').state.value, 'released')

    def accept(self, job, packet, **options):
        return accept_shared_member(self.conn, job, packet, **options)


class SharedAttachTests(SharedSlotCase):
    def test_two_members_bind_volume_and_outlets(self):
        record = self.attach()
        self.assertEqual([member.job_id for member in record.members], ['X', 'Y'])
        self.assertEqual({member.volume_tag for member in record.members}, {'coil-x', 'coil-y'})
        self.assertEqual(query_shared_release(self.conn, SLOT), 'current')
        self.assertIsNone(read_shared_member(self.conn, SLOT, 'missing'))
        self.assertIsNone(read_shared_member(self.conn, 'other', 'X'))
        self.assertEqual(step(self.conn, 'X', 'backup').state.value, 'released')
        self.assertIsNone(read_shared_member(self.conn, SLOT, 'X') and None)

    def test_rejects_bad_attachments(self):
        self.attach()
        with self.assertRaises(ValueError):
            self.attach(slot=SLOT)
        with self.assertRaises(ValueError):
            self.attach(slot='carrier-2')
        with self.assertRaises(ValueError):
            attach_shared_slot(self.conn, 'carrier-3', members()[:1])
        with self.assertRaises(ValueError):
            attach_shared_slot(self.conn, 'carrier-3', members() + [members()[0]])
        with self.assertRaises(ValueError):
            attach_shared_slot(self.conn, 'carrier-3',
                               [members()[0], dict(members()[1], job_id='unknown')])
        with self.assertRaises(ValueError):
            attach_shared_slot(self.conn, '', members())

    def test_jobs_keep_their_binding(self):
        self.attach()
        with self.assertRaises(ValueError):
            attach_recording(self.conn, 'X', recording([frame('A', '07')], []))
        with self.assertRaises(ValueError):
            attach_shared_slot(self.conn, 'carrier-2', members())
        self.load_plain('plain')
        with self.assertRaises(ValueError):
            attach_shared_slot(self.conn, 'carrier-2',
                               [dict(members()[0], job_id='plain'), members()[1]])

    def load_plain(self, job):
        from jobs.registration import open as open_job
        open_job(self.conn, job, 'plain-coil', 1, 1)
        attach_recording(self.conn, job, recording([frame('A', '07')], [], volume='plain-coil'))


class SharedRewriteTests(SharedSlotCase):
    def test_stale_and_equal_rewrites(self):
        self.attach()
        first = read_shared_member(self.conn, SLOT, 'Y')
        equal = rewrite_shared_member(self.conn, first, frame('A', '09'))
        self.assertEqual(equal['payload'], first['payload'])
        self.assertNotEqual(equal['write_id'], first['write_id'])
        changed = rewrite_shared_member(self.conn, equal, frame('A', '0a'))
        self.assertNotEqual(changed['write_id'], equal['write_id'])
        with self.assertRaises(InvalidFragment):
            self.accept('Y', first)
        with self.assertRaises(InvalidFragment):
            self.accept('Y', equal)
        self.assertEqual(read_shared_member(self.conn, SLOT, 'X')['payload'], b'\x07')

    def test_rewrite_rejections(self):
        self.attach()
        packet = read_shared_member(self.conn, SLOT, 'X')
        with self.assertRaises(ValueError):
            rewrite_shared_member(self.conn, dict(packet, checksum=0), frame('A', '07'))
        with self.assertRaises(ValueError):
            rewrite_shared_member(self.conn, dict(packet, job_id='Y'), frame('A', '09'))
        with self.assertRaises(ValueError):
            rewrite_shared_member(self.conn, {'write_id': packet['write_id']}, frame('A', '07'))
        self.accept('X', packet)
        with self.assertRaises(ValueError):
            rewrite_shared_member(self.conn, packet, frame('A', '07'))


class SharedAcceptTests(SharedSlotCase):
    def test_acceptance_checks_identity_and_bytes(self):
        self.attach()
        x = read_shared_member(self.conn, SLOT, 'X')
        y = read_shared_member(self.conn, SLOT, 'Y')
        with self.assertRaises(InvalidFragment):
            self.accept('Y', x)
        with self.assertRaises(InvalidFragment):
            self.accept('X', dict(x, slot_id='other'))
        altered = dict(x, payload=b'\x08', checksum=crc32(b'\x08'))
        with self.assertRaises(InvalidFragment):
            self.accept('X', altered)
        with self.assertRaises(InvalidFragment):
            self.accept('X', dict(x, checksum=0))
        with self.assertRaises(InvalidFragment):
            self.accept('X', dict(x, proof='forged'))
        with self.assertRaises(InvalidFragment):
            self.accept('X', dict(x, volume_tag='coil-y'))
        self.assertEqual(CustodyStore(self.conn).list_for_job('X'), ())
        accepted = self.accept('X', x)
        self.assertEqual(accepted, self.accept('X', x))
        self.assertEqual(set(accepted), {'slot_id', 'job_id', 'recording_id', 'write_id'})
        self.assertEqual((accepted['slot_id'], accepted['job_id']), (SLOT, 'X'))
        self.assertEqual(accepted['write_id'], x['write_id'])
        self.assertEqual(self.accept('Y', y)['write_id'], y['write_id'])
        self.assertEqual(release_shared_slot(self.conn, SLOT), 'released')

    def test_commit_failure_rolls_back_and_resumes(self):
        self.attach()
        self.drain_backups('X', 'Y')
        self.accept('X', read_shared_member(self.conn, SLOT, 'X'))
        y = read_shared_member(self.conn, SLOT, 'Y')

        def refuse():
            raise RuntimeError('no commit')

        with self.assertRaises(RuntimeError):
            self.accept('Y', y, before_commit=refuse)
        mains = [record for record in CustodyStore(self.conn).list_for_job('Y')
                 if record.receipt.outlet is Outlet.MAIN]
        self.assertEqual(mains, [])
        self.assertEqual(release_shared_slot(self.conn, SLOT), 'current')
        self.reopen()
        self.initialize()
        self.assertEqual(query_shared_release(self.conn, SLOT), 'current')
        with self.assertRaises(RecordingUnfinished):
            seal(self.conn, self.archive, 'Y')
        self.accept('Y', y)
        self.assertEqual(release_shared_slot(self.conn, SLOT), 'released')


class SharedReleaseAndSealTests(SharedSlotCase):
    def test_release_needs_both_members(self):
        self.attach()
        self.drain_backups('X', 'Y')
        x = read_shared_member(self.conn, SLOT, 'X')
        y = read_shared_member(self.conn, SLOT, 'Y')
        self.accept('X', x)
        self.assertEqual(release_shared_slot(self.conn, SLOT), 'current')
        self.assertEqual(query_shared_release(self.conn, SLOT), 'current')
        self.accept('Y', y)
        self.assertEqual(release_shared_slot(self.conn, SLOT), 'released')
        self.assertEqual(release_shared_slot(self.conn, SLOT), 'released')
        self.assertEqual(query_shared_release(self.conn, SLOT), 'released')
        with self.assertRaises(ValueError):
            release_shared_slot(self.conn, 'missing')
        self.assertEqual(query_shared_release(self.conn, 'missing'), 'unknown')
        self.reopen()
        self.assertEqual(query_shared_release(self.conn, SLOT), 'released')
        self.assertEqual(self.accept('Y', y), {'slot_id': SLOT, 'job_id': 'Y',
                                               'recording_id': 'ry', 'write_id': y['write_id']})

    def test_ordinary_main_cannot_bypass_carrier(self):
        self.attach()
        x = read_shared_member(self.conn, SLOT, 'X')
        with self.assertRaises(InvalidFragment):
            step(self.conn, 'X', 'main')
        with self.assertRaises(ValueError):
            release_slot(self.conn, self.receipt('X'))
        self.assertEqual(query_shared_release(self.conn, SLOT), 'current')
        self.accept('X', x)
        self.assertEqual(release_shared_slot(self.conn, SLOT), 'current')

    def receipt(self, job):
        from device.slot import SlotReceipt
        member = 'rx' if job == 'X' else 'ry'
        return SlotReceipt(job, member, Outlet.MAIN, 0)

    def test_seal_settled_volume_while_carrier_held(self):
        self.attach()
        self.drain_backups('X', 'Y')
        x = read_shared_member(self.conn, SLOT, 'X')
        y = read_shared_member(self.conn, SLOT, 'Y')
        self.accept('X', x)
        signed_x = seal(self.conn, self.archive, 'X')
        self.assertEqual((signed_x.a, signed_x.b), (b'\x07', b'\x0b'))
        self.assertEqual(release_shared_slot(self.conn, SLOT), 'current')
        with self.assertRaises(RecordingUnfinished):
            seal(self.conn, self.archive, 'Y')
        with self.assertRaises(VolumeNotAvailable):
            read(self.archive, 'VOL-Y')
        self.accept('Y', y)
        self.assertEqual(release_shared_slot(self.conn, SLOT), 'released')
        signed_y = seal(self.conn, self.archive, 'Y')
        self.assertEqual((signed_y.a, signed_y.b), (b'\x09', b'\x05'))
        self.assertEqual(seal(self.conn, self.archive, 'X'), signed_x)
        self.assertEqual(read(self.archive, 'VOL-X'), signed_x)
        self.assertEqual(read(self.archive, 'VOL-Y'), signed_y)

        main = [source for tape in signed_y.provenance for source in tape.sources
                if source.receipt.outlet is Outlet.MAIN]
        self.assertEqual(len(main), 2)
        for source in main:
            self.assertEqual(source.receipt.job_id, 'Y')
            self.assertEqual(source.receipt.recording_id, 'ry')
            self.assertEqual(source.slot_id, SLOT)
            self.assertEqual(source.write_id, y['write_id'])
            self.assertIn('slot_id', source.to_data())
            self.assertIn('write_id', source.to_data())

    def test_old_volume_signed_by_backup_only(self):
        self.attach()
        x = read_shared_member(self.conn, SLOT, 'X')
        y = read_shared_member(self.conn, SLOT, 'Y')
        self.accept('X', x)
        with self.assertRaises(RecordingUnfinished):
            seal(self.conn, self.archive, 'X')
        step(self.conn, 'X', 'backup')
        volume = seal(self.conn, self.archive, 'X')
        self.assertEqual((volume.a, volume.b), (b'\x07', b'\x0b'))
        self.accept('Y', y)
        self.assertEqual(release_shared_slot(self.conn, SLOT), 'released')

    def test_other_shared_rewrite_keeps_acceptance(self):
        self.attach()
        x = read_shared_member(self.conn, SLOT, 'X')
        y = read_shared_member(self.conn, SLOT, 'Y')
        accepted = self.accept('X', x)
        rewrite_shared_member(self.conn, y, frame('A', '0a'))
        self.assertEqual(self.accept('X', x), accepted)
