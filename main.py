import psycopg2
from dotenv import load_dotenv
import os

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

connection = psycopg2.connect(DATABASE_URL)
cur = connection.cursor()
cur.execute("select 1;")
print("select 1 result:", cur.fetchone())
connection.close()
