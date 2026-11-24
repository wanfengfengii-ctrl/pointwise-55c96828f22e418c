import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path


class ArchiveStore:
    def __init__(self, path):
        self.path = Path(path)

    def _connect(self, mode):
        uri = f'{self.path.resolve().as_uri()}?mode={mode}'
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys = ON')
        return connection

    def initialize(self):
        with closing(self._connect('rwc')) as connection:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS archive_volumes (
                    number TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL UNIQUE,
                    tape_a BLOB NOT NULL CHECK (length(tape_a) > 0),
                    tape_b BLOB NOT NULL CHECK (length(tape_b) = length(tape_a)),
                    provenance TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS archive_receipts (
                    receipt_id TEXT PRIMARY KEY,
                    number TEXT NOT NULL UNIQUE REFERENCES archive_volumes (number)
                );
            ''')

    @contextmanager
    def transaction(self):
        with closing(self._connect('rw')) as connection:
            connection.execute('BEGIN IMMEDIATE')
            with connection:
                yield connection

    def insert_volume(self, connection, number, job_id, a, b, provenance):
        connection.execute('''
            INSERT INTO archive_volumes (number, job_id, tape_a, tape_b, provenance)
            VALUES (?, ?, ?, ?, ?)
        ''', (number, job_id, a, b, json.dumps(provenance, separators=(',', ':'))))

    def insert_receipt(self, connection, receipt_id, number):
        connection.execute('''
            INSERT INTO archive_receipts (receipt_id, number) VALUES (?, ?)
        ''', (receipt_id, number))

    def _fetch(self, column, value, connection):
        # column comes from the two fixed lookup methods below.
        statement = f'''
            SELECT v.number, v.job_id, v.tape_a, v.tape_b, v.provenance, r.receipt_id
            FROM archive_volumes AS v JOIN archive_receipts AS r ON r.number = v.number
            WHERE v.{column} = ?
        '''
        if connection is not None:
            return connection.execute(statement, (value,)).fetchone()
        with closing(self._connect('ro')) as reader:
            return reader.execute(statement, (value,)).fetchone()

    def fetch_by_number(self, number, connection=None):
        return self._fetch('number', number, connection)

    def fetch_by_job(self, job_id, connection=None):
        return self._fetch('job_id', job_id, connection)
