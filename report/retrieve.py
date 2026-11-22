import sqlite3

from report.issue import PublishedReport
from report.plan import load_plan
from report.provenance import read_report_parents
from report.receipt import load_report_receipt
from report.store import load_report


def read(db: sqlite3.Connection, report_id: int) -> PublishedReport | None:
    record = load_report(db, report_id)
    if record is None:
        return None
    parents = read_report_parents(db, report_id)
    receipt = load_report_receipt(db, report_id)
    return PublishedReport(record, parents, receipt)


def read_plan(db: sqlite3.Connection, plan_id: str) -> dict | None:
    signed = load_plan(db, plan_id)
    return None if signed is None else signed.as_dict()
