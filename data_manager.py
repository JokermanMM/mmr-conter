import os
import sqlite3
import logging
import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

class DataManager:
    def __init__(self, db_path="data.db"):
        self.db_path = db_path
        self.database_url = os.environ.get("DATABASE_URL")
        self._init_db()

    def _get_connection(self):
        if self.database_url:
            # Use PostgreSQL (Supabase)
            return psycopg2.connect(self.database_url)
        else:
            # Use local SQLite
            return sqlite3.connect(self.db_path)

    def _init_db(self):
        conn = self._get_connection()
        cursor = conn.cursor()
        
        query = '''
            CREATE TABLE IF NOT EXISTS users (
                chat_id TEXT PRIMARY KEY,
                steam_id BIGINT,
                last_match_id BIGINT,
                last_mmr INTEGER
            )
        '''
        cursor.execute(query)
        
        # Migrations
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN manual_mmr INTEGER")
        except Exception:
            pass
            
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN matches_since_calibration INTEGER DEFAULT 0")
        except Exception:
            pass
            
        conn.commit()
        cursor.close()
        conn.close()

    def get_user(self, chat_id):
        conn = self._get_connection()
        cursor = conn.cursor()
        query = 'SELECT steam_id, last_match_id, last_mmr, manual_mmr, matches_since_calibration FROM users WHERE chat_id = %s' if self.database_url else 'SELECT steam_id, last_match_id, last_mmr, manual_mmr, matches_since_calibration FROM users WHERE chat_id = ?'
        cursor.execute(query, (str(chat_id),))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        if row:
            return {
                "steam_id": row[0], 
                "last_match_id": row[1], 
                "last_mmr": row[2],
                "manual_mmr": row[3],
                "matches_since_calibration": row[4] or 0
            }
        return None

    def get_all_users(self):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT chat_id, steam_id, last_match_id, last_mmr, manual_mmr, matches_since_calibration FROM users')
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        users = {}
        for row in rows:
            users[row[0]] = {
                "steam_id": row[1], 
                "last_match_id": row[2], 
                "last_mmr": row[3],
                "manual_mmr": row[4],
                "matches_since_calibration": row[5] or 0
            }
        return users

    def set_user(self, chat_id, steam_id, last_match_id=None, last_mmr=None):
        conn = self._get_connection()
        cursor = conn.cursor()
        if self.database_url:
            query = '''
                INSERT INTO users (chat_id, steam_id, last_match_id, last_mmr, manual_mmr, matches_since_calibration)
                VALUES (%s, %s, %s, %s, NULL, 0)
                ON CONFLICT (chat_id) DO UPDATE 
                SET steam_id = EXCLUDED.steam_id, 
                    last_match_id = EXCLUDED.last_match_id, 
                    last_mmr = EXCLUDED.last_mmr
            '''
            cursor.execute(query, (str(chat_id), steam_id, last_match_id, last_mmr))
        else:
            query = '''
                INSERT INTO users (chat_id, steam_id, last_match_id, last_mmr, manual_mmr, matches_since_calibration)
                VALUES (?, ?, ?, ?, NULL, 0)
                ON CONFLICT(chat_id) DO UPDATE SET
                steam_id = excluded.steam_id,
                last_match_id = excluded.last_match_id,
                last_mmr = excluded.last_mmr
            '''
            cursor.execute(query, (str(chat_id), steam_id, last_match_id, last_mmr))
        conn.commit()
        cursor.close()
        conn.close()

    def set_manual_mmr(self, chat_id, mmr):
        conn = self._get_connection()
        cursor = conn.cursor()
        placeholder = "%s" if self.database_url else "?"
        cursor.execute(f'''
            UPDATE users SET manual_mmr = {placeholder}, matches_since_calibration = 0
            WHERE chat_id = {placeholder}
        ''', (mmr, str(chat_id)))
        conn.commit()
        cursor.close()
        conn.close()

    def update_match_and_mmr(self, chat_id, last_match_id, new_manual_mmr, matches_count):
        conn = self._get_connection()
        cursor = conn.cursor()
        placeholder = "%s" if self.database_url else "?"
        cursor.execute(f'''
            UPDATE users 
            SET last_match_id = {placeholder}, 
                manual_mmr = {placeholder},
                matches_since_calibration = {placeholder}
            WHERE chat_id = {placeholder}
        ''', (last_match_id, new_manual_mmr, matches_count, str(chat_id)))
        conn.commit()
        cursor.close()
        conn.close()
