# Reporting

Phase 12 reports persist generated payloads with content hashes.

Supported initial report types are generic daily/weekly/monthly/risk/execution/operations payloads. Report schedules are API-preview only and not yet a durable scheduler.
# Phase 12.1 Reporting Update

Report schedules are now persisted in `phase12_report_schedules`. The schedule execution worker remains a mandatory completion gate before reporting is fully complete.
