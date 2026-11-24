from archive.history import find_by_job
from archive.publish import publish_volume
from decision.completeness import require_settled_evidence
from decision.equations import collect_constraints
from decision.provenance import build_provenance
from decision.recovery import recover_tapes
from jobs.registration import get_job


def seal(connection, archive, job_id: str, *, before_commit=None):
    job = get_job(connection, job_id)
    original = find_by_job(archive, job_id)
    if original is not None:
        return original
    evidence = require_settled_evidence(connection, job)
    constraints = collect_constraints(evidence.custody)
    tapes = recover_tapes(constraints)
    provenance = build_provenance(tapes)
    return publish_volume(archive, job_id, tapes, provenance, before_commit=before_commit)
