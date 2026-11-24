import sqlite3

from jobs.identity import JobIdentity
from jobs.specification import JobSpec


class JobConflict(ValueError):
    pass


def initialize_jobs(connection: sqlite3.Connection):
    if connection.in_transaction:
        raise RuntimeError('initialize jobs outside a transaction')
    connection.execute('PRAGMA foreign_keys = ON')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS job_specs (
            job_id TEXT PRIMARY KEY,
            volume_tag TEXT NOT NULL,
            a_length INTEGER NOT NULL CHECK (a_length > 0),
            b_length INTEGER NOT NULL CHECK (a_length = b_length)
        )
    ''')


def get_job(connection: sqlite3.Connection, job_id: str) -> JobSpec:
    row = connection.execute('''
        SELECT job_id, volume_tag, a_length, b_length
        FROM job_specs WHERE job_id = ?
    ''', (job_id,)).fetchone()
    if row is None:
        raise KeyError(job_id)
    return JobSpec(JobIdentity(row[0], row[1]), row[2], row[3])


def open(connection: sqlite3.Connection, job_id: str, volume_tag: str,
         a_length: int, b_length: int) -> JobSpec:
    spec = JobSpec(JobIdentity(job_id, volume_tag), a_length, b_length)
    connection.execute('BEGIN IMMEDIATE')
    with connection:
        connection.execute('''
            INSERT INTO job_specs (job_id, volume_tag, a_length, b_length)
            VALUES (?, ?, ?, ?) ON CONFLICT (job_id) DO NOTHING
        ''', (job_id, volume_tag, a_length, b_length))
        original = get_job(connection, job_id)
        if original != spec:
            raise JobConflict(f'job {job_id!r} already has a different specification')
    return original
