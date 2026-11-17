"""Fresh, small Postgres schema for the lean app (database `bensim`)."""
from __future__ import annotations

import psycopg

from .config import DB_URL

SCHEMA = """
create table if not exists candles (
    symbol text not null, tf text not null, ts timestamptz not null,
    open double precision not null, high double precision not null, low double precision not null,
    close double precision not null, tick_volume integer not null default 0, source text not null,
    primary key (symbol, tf, ts)
);
create table if not exists economic_events (
    id bigserial primary key, ts timestamptz not null, currency text not null, impact text not null,
    name text not null, actual text, forecast text, previous text, source text not null,
    unique (ts, currency, name)
);
create index if not exists economic_events_ts on economic_events (ts);
create table if not exists data_audit (
    symbol text not null, tf text not null, source text not null, first_ts timestamptz, last_ts timestamptz,
    bars integer, holes_over_2h integer, missing_days double precision, checked_at timestamptz default now(),
    notes text, primary key (symbol, tf, source)
);
"""


def connect(autocommit: bool = False) -> psycopg.Connection:
    return psycopg.connect(DB_URL, autocommit=autocommit)


def init() -> None:
    with connect(autocommit=True) as c:
        c.execute(SCHEMA)


if __name__ == "__main__":
    init()
    print("schema ready")
