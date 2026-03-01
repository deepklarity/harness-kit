#!/usr/bin/env python3
"""Create the taskit database and app user in PostgreSQL.

Connects to the 'postgres' maintenance DB using admin credentials (via CLI args),
then creates the database and user specified in .env.

Usage:
    python scripts/create_db.py                          # auto-detects admin user
    python scripts/create_db.py --user admin --password secret
"""
import argparse
import getpass
import os
import sys
from pathlib import Path

import psycopg2
from psycopg2 import sql
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def main():
    default_admin = getpass.getuser()  # macOS/Postgres.app: admin is your OS user
    parser = argparse.ArgumentParser(description="Create the taskit database")
    parser.add_argument("--user", default=default_admin, help=f"PostgreSQL admin user (default: {default_admin})")
    parser.add_argument("--password", default="", help="PostgreSQL admin password (default: none)")
    args = parser.parse_args()

    db_host = os.environ.get("DB_HOST", "localhost")
    db_port = os.environ.get("DB_PORT", "5432")
    db_name = os.environ.get("DB_NAME", "taskit")
    db_user = os.environ.get("DB_USER", "taskit")
    db_password = os.environ.get("DB_PASSWORD", "taskit")

    # Connect to maintenance DB with admin credentials
    conn_params = dict(
        dbname="postgres",
        user=args.user,
        host=db_host,
        port=db_port,
    )
    if args.password:
        conn_params["password"] = args.password
    conn = psycopg2.connect(**conn_params)
    conn.autocommit = True
    cur = conn.cursor()

    # Create app user if different from admin
    if db_user != args.user:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (db_user,))
        if not cur.fetchone():
            cur.execute(
                sql.SQL("CREATE USER {} WITH PASSWORD %s").format(sql.Identifier(db_user)),
                (db_password,),
            )
            print(f"Created user '{db_user}'")
        else:
            print(f"User '{db_user}' already exists")

    # Create database
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
    if not cur.fetchone():
        owner = db_user
        cur.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(db_name),
                sql.Identifier(owner),
            )
        )
        print(f"Created database '{db_name}' (owner: {owner})")
    else:
        print(f"Database '{db_name}' already exists")

    cur.close()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except psycopg2.OperationalError as e:
        print(f"Error connecting to PostgreSQL: {e}", file=sys.stderr)
        sys.exit(1)
