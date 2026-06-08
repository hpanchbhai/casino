"""
Milestone 1 — Verify Python can talk to local Postgres.

psycopg2 vocabulary you need to know:
  connection  = the TCP session to the DB server (like opening a file)
  cursor      = a handle for sending SQL and reading results
                (one connection can have many cursors)
  fetchone()  = pull the next row as a tuple
  fetchall()  = pull all remaining rows as a list of tuples
  close()     = always close cursor then connection when done
"""

import psycopg2

# --- Connection string components ---
# host="localhost"   → Postgres is running on this machine
# dbname="stocks"    → the DB we just created with createdb
# user=""            → empty string = use your macOS username (Postgres default)
# password=""        → no password set in local dev
DB_PARAMS = {
    "host": "localhost",
    "dbname": "stocks",
    "user": "",          # will be filled from your OS username automatically
    "password": "",
}

def main():
    # Open connection — this is a blocking network call
    conn = psycopg2.connect(**DB_PARAMS)

    # Cursors are cheap; open one per logical operation
    cur = conn.cursor()

    # version() is a built-in Postgres function — good sanity check
    cur.execute("SELECT version();")
    row = cur.fetchone()   # returns a 1-tuple: ("PostgreSQL 18.4 ...",)
    print("Connected to:", row[0])

    # Show all databases on this server — pg_database is a system catalog table
    cur.execute("""
        SELECT datname
        FROM   pg_database
        WHERE  datistemplate = false   -- exclude postgres internal template DBs
        ORDER  BY datname;
    """)
    dbs = cur.fetchall()   # list of 1-tuples
    print("\nDatabases on this server:")
    for (name,) in dbs:    # unpack the 1-tuple directly in the loop
        print(" •", name)

    # Always close in reverse order: cursor first, then connection
    cur.close()
    conn.close()
    print("\nDone — connection closed cleanly.")

if __name__ == "__main__":
    main()
