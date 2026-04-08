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
        conn.commit()
        
        # Migrations for users table
        migrations = [
            "ALTER TABLE users ADD COLUMN manual_mmr INTEGER",
            "ALTER TABLE users ADD COLUMN matches_since_calibration INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN win_streak INTEGER DEFAULT 0",
        ]
        for m in migrations:
            try:
                cursor.execute(m)
                conn.commit()
            except Exception:
                conn.rollback()
        
        # MMR history table for /graph
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS mmr_history (
                id SERIAL PRIMARY KEY,
                chat_id TEXT NOT NULL,
                match_id TEXT,
                mmr INTEGER NOT NULL,
                is_win BOOLEAN,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''' if self.database_url else '''
            CREATE TABLE IF NOT EXISTS mmr_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                match_id TEXT,
                mmr INTEGER NOT NULL,
                is_win BOOLEAN,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Match log table for daily/weekly summaries
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS match_log (
                id SERIAL PRIMARY KEY,
                chat_id TEXT NOT NULL,
                match_id TEXT,
                hero_id INTEGER,
                is_win BOOLEAN,
                kills INTEGER DEFAULT 0,
                deaths INTEGER DEFAULT 0,
                assists INTEGER DEFAULT 0,
                mmr_after INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''' if self.database_url else '''
            CREATE TABLE IF NOT EXISTS match_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                match_id TEXT,
                hero_id INTEGER,
                is_win BOOLEAN,
                kills INTEGER DEFAULT 0,
                deaths INTEGER DEFAULT 0,
                assists INTEGER DEFAULT 0,
                mmr_after INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
            
        conn.commit()
        cursor.close()
        conn.close()

    def get_user(self, chat_id):
        conn = self._get_connection()
        cursor = conn.cursor()
        query = 'SELECT steam_id, last_match_id, last_mmr, manual_mmr, matches_since_calibration, win_streak FROM users WHERE chat_id = %s' if self.database_url else 'SELECT steam_id, last_match_id, last_mmr, manual_mmr, matches_since_calibration, win_streak FROM users WHERE chat_id = ?'
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
                "matches_since_calibration": row[4] or 0,
                "win_streak": row[5] or 0
            }
        return None

    def get_all_users(self):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT chat_id, steam_id, last_match_id, last_mmr, manual_mmr, matches_since_calibration, win_streak FROM users')
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
                "matches_since_calibration": row[5] or 0,
                "win_streak": row[6] or 0
            }
        return users

    def set_user(self, chat_id, steam_id, last_match_id=None, last_mmr=None):
        conn = self._get_connection()
        cursor = conn.cursor()
        if self.database_url:
            query = '''
                INSERT INTO users (chat_id, steam_id, last_match_id, last_mmr, manual_mmr, matches_since_calibration, win_streak)
                VALUES (%s, %s, %s, %s, NULL, 0, 0)
                ON CONFLICT (chat_id) DO UPDATE 
                SET steam_id = EXCLUDED.steam_id, 
                    last_match_id = EXCLUDED.last_match_id, 
                    last_mmr = EXCLUDED.last_mmr
            '''
            cursor.execute(query, (str(chat_id), steam_id, last_match_id, last_mmr))
        else:
            query = '''
                INSERT INTO users (chat_id, steam_id, last_match_id, last_mmr, manual_mmr, matches_since_calibration, win_streak)
                VALUES (?, ?, ?, ?, NULL, 0, 0)
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

    def update_match_and_mmr(self, chat_id, last_match_id, new_manual_mmr, matches_count, win_streak=0):
        conn = self._get_connection()
        cursor = conn.cursor()
        placeholder = "%s" if self.database_url else "?"
        cursor.execute(f'''
            UPDATE users 
            SET last_match_id = {placeholder}, 
                manual_mmr = {placeholder},
                matches_since_calibration = {placeholder},
                win_streak = {placeholder}
            WHERE chat_id = {placeholder}
        ''', (last_match_id, new_manual_mmr, matches_count, win_streak, str(chat_id)))
        conn.commit()
        cursor.close()
        conn.close()

    def update_match(self, chat_id, last_match_id, last_mmr, win_streak=0):
        """Update just the match ID and streak (for non-ranked games)."""
        conn = self._get_connection()
        cursor = conn.cursor()
        placeholder = "%s" if self.database_url else "?"
        cursor.execute(f'''
            UPDATE users 
            SET last_match_id = {placeholder},
                last_mmr = {placeholder},
                win_streak = {placeholder}
            WHERE chat_id = {placeholder}
        ''', (last_match_id, last_mmr, win_streak, str(chat_id)))
        conn.commit()
        cursor.close()
        conn.close()

    # --- MMR History (for /graph) ---
    
    def add_mmr_history(self, chat_id, match_id, mmr, is_win):
        conn = self._get_connection()
        cursor = conn.cursor()
        placeholder = "%s" if self.database_url else "?"
        cursor.execute(f'''
            INSERT INTO mmr_history (chat_id, match_id, mmr, is_win)
            VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder})
        ''', (str(chat_id), str(match_id), mmr, is_win))
        conn.commit()
        cursor.close()
        conn.close()

    def get_mmr_history(self, chat_id, limit=20):
        conn = self._get_connection()
        cursor = conn.cursor()
        placeholder = "%s" if self.database_url else "?"
        cursor.execute(f'''
            SELECT mmr, is_win, created_at FROM mmr_history 
            WHERE chat_id = {placeholder}
            ORDER BY created_at DESC
            LIMIT {placeholder}
        ''', (str(chat_id), limit))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        # Reverse so oldest is first (for graph left-to-right)
        return list(reversed(rows))

    # --- Match Log (for daily/weekly summaries) ---
    
    def log_match(self, chat_id, match_id, hero_id, is_win, kills, deaths, assists, mmr_after):
        conn = self._get_connection()
        cursor = conn.cursor()
        placeholder = "%s" if self.database_url else "?"
        cursor.execute(f'''
            INSERT INTO match_log (chat_id, match_id, hero_id, is_win, kills, deaths, assists, mmr_after)
            VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
        ''', (str(chat_id), str(match_id), hero_id, is_win, kills, deaths, assists, mmr_after))
        conn.commit()
        cursor.close()
        conn.close()

    def get_matches_since(self, chat_id, since_hours=24):
        conn = self._get_connection()
        cursor = conn.cursor()
        placeholder = "%s" if self.database_url else "?"
        if self.database_url:
            cursor.execute(f'''
                SELECT match_id, hero_id, is_win, kills, deaths, assists, mmr_after, created_at 
                FROM match_log 
                WHERE chat_id = {placeholder} AND created_at >= NOW() - INTERVAL '{since_hours} hours'
                ORDER BY created_at ASC
            ''', (str(chat_id),))
        else:
            cursor.execute(f'''
                SELECT match_id, hero_id, is_win, kills, deaths, assists, mmr_after, created_at 
                FROM match_log 
                WHERE chat_id = {placeholder} AND created_at >= datetime('now', '-{since_hours} hours')
                ORDER BY created_at ASC
            ''', (str(chat_id),))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return rows

