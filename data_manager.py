import json
import os
import sqlite3
import logging

logger = logging.getLogger(__name__)

class DataManager:
    def __init__(self, db_path="data.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                chat_id TEXT PRIMARY KEY,
                steam_id INTEGER,
                last_match_id INTEGER,
                last_mmr INTEGER
            )
        ''')
        conn.commit()
        conn.close()

    def get_user(self, chat_id):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT steam_id, last_match_id, last_mmr FROM users WHERE chat_id = ?', (str(chat_id),))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {"steam_id": row[0], "last_match_id": row[1], "last_mmr": row[2]}
        return None

    def get_all_users(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT chat_id, steam_id, last_match_id, last_mmr FROM users')
        rows = cursor.fetchall()
        conn.close()
        users = {}
        for row in rows:
            users[row[0]] = {"steam_id": row[1], "last_match_id": row[2], "last_mmr": row[3]}
        return users

    def set_user(self, chat_id, steam_id, last_match_id=None, last_mmr=None):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO users (chat_id, steam_id, last_match_id, last_mmr)
            VALUES (?, ?, ?, ?)
        ''', (str(chat_id), steam_id, last_match_id, last_mmr))
        conn.commit()
        conn.close()

    def update_match(self, chat_id, last_match_id, last_mmr):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE users SET last_match_id = ?, last_mmr = ?
            WHERE chat_id = ?
        ''', (last_match_id, last_mmr, str(chat_id)))
        conn.commit()
        conn.close()
