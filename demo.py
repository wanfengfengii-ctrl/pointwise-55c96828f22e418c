import json
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.readback import read
from archive.storage import ArchiveStore
from capture.custody import CustodyStore
from capture.intake import StepState, step
from decision.sealing import seal
from device.recording import attach_recording, read_current
from device.release import ReleaseFault
from device.replay_state import ReplayState
from device.slot import Outlet
from jobs.registration import initialize_jobs, open as open_job


SAMPLES = Path(__file__).resolve().parent / 'samples'


def _result(volume):
    return {
        'a': volume.a.hex(),
        'b': volume.b.hex(),
        'support': [
            [[source.kind.value, source.receipt.outlet.value, source.receipt.ordinal]
             for source in tape.sources]
            for tape in volume.provenance
        ],
        'modes': [tape.mode for tape in volume.provenance],
        'receipt': volume.receipt.receipt_id,
    }


def main():
    with TemporaryDirectory(prefix='line-scan-') as directory:
        root = Path(directory)
        archive_path = root / 'archive.sqlite'
        field_path = root / 'field.sqlite'
        archive = ArchiveStore(archive_path)
        archive.initialize()
        with closing(sqlite3.connect(field_path)) as connection:
            initialize_jobs(connection)
            ReplayState(connection).initialize()
            CustodyStore(connection).initialize()
            open_job(connection, 'J1', 'coil-01', 1, 1)
            attach_recording(connection, 'J1', SAMPLES / 'direct.json')
            step(connection, 'J1', Outlet.MAIN)
            step(connection, 'J1', Outlet.BACKUP)
            direct = seal(connection, archive, 'J1')
            assert _result(direct) == {
                'a': '07', 'b': '0b', 'support': [[['A', 'main', 0]], [['B', 'backup', 0]]],
                'modes': ['direct', 'direct'], 'receipt': 'FIRST-VOL-J1',
            }
            open_job(connection, 'J2', 'coil-02', 3, 3)
            attach_recording(connection, 'J2', SAMPLES / 'rescue.json')
            original = read_current(connection, 'J2', Outlet.MAIN).receipt
            pending = step(connection, 'J2', Outlet.MAIN, original, release_fault=ReleaseFault.AFTER)
            assert pending.state is StepState.PENDING
            step(connection, 'J2', Outlet.BACKUP)
        with closing(sqlite3.connect(field_path)) as connection:
            assert step(connection, 'J2', Outlet.MAIN, original).state is StepState.RELEASED
            assert read_current(connection, 'J2', Outlet.MAIN).kind == 'A'
            step(connection, 'J2', Outlet.MAIN)
            rescue = seal(connection, archive, 'J2')
        offline = ArchiveStore(archive_path)
        assert read(offline, direct.number) == direct
        received = read(offline, rescue.number)
        expected = {
            'a': '0700ff', 'b': '0bff80',
            'support': [[['A', 'main', 1]], [['A', 'main', 1], ['P', 'main', 0]]],
            'modes': ['direct', 'recovered'], 'receipt': 'FIRST-VOL-J2',
        }
        actual = _result(rescue)
        downstream = _result(received)
        assert actual == expected
        assert received == rescue and downstream == expected
        observation = {
            'case_id': 'v2.byte_exact',
            'input': {'main': ['P=0cff7f', 'A=0700ff'], 'backup': ['P=0cff7f'],
                      'release_reply': 'lost after main P release'},
            'expected': expected,
            'actual': actual,
            'downstream_expected': expected,
            'downstream_actual': downstream,
            'code_paths': ['capture/intake.py', 'capture/reopen.py', 'decision/completeness.py',
                           'decision/recovery.py', 'decision/provenance.py', 'archive/readback.py'],
            'oracle': '手算 07 XOR 0C = 0B、00 XOR FF = FF、FF XOR 7F = 80；主出口槽序确定支持收据。',
        }
        print('POINTWISE_SCENARIO ' + json.dumps(observation, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
