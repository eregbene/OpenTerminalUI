from __future__ import annotations

import sqlite3
from pathlib import Path


def main() -> None:
    db_path = Path("data/openterminalui.db")
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    print(f"db={db_path}")
    for (name,) in cur.execute("select name from sqlite_master where type='table' order by name"):
        print(name)


if __name__ == "__main__":
    main()
