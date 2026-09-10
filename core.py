"""Persistent per-destination queue; independent of AstrBot and Instagram."""
import json
import re
import sqlite3
import time
from urllib.parse import urlparse


def username(value):
    value = value.strip()
    if value.startswith(('https://', 'http://')):
        url = urlparse(value)
        if url.hostname not in ('instagram.com', 'www.instagram.com'):
            raise ValueError('请提供 Instagram 用户名或账号主页链接。')
        value = url.path.strip('/')
    value = value.lstrip('@').lower()
    if not re.fullmatch(r'[a-z0-9._]{1,30}', value) or value in {
        'p', 'reel', 'reels', 'stories', 'explore', 'accounts', 'direct'
    }:
        raise ValueError('用户名格式不正确，请提供账号名而非帖子链接。')
    return value


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.executescript('''
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY, origin TEXT NOT NULL, account TEXT NOT NULL,
                UNIQUE(origin, account));
            CREATE TABLE IF NOT EXISTS streams (
                sub INTEGER REFERENCES subscriptions(id) ON DELETE CASCADE,
                source TEXT, PRIMARY KEY(sub, source));
            CREATE TABLE IF NOT EXISTS deliveries (
                sub INTEGER REFERENCES subscriptions(id) ON DELETE CASCADE,
                key TEXT, payload TEXT, position INTEGER NOT NULL DEFAULT 0,
                done INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(sub, key));
        ''')
        columns = {row[1] for row in self.db.execute('PRAGMA table_info(deliveries)')}
        with self.db:
            for column in ('attempts', 'retry_at'):
                if column not in columns:
                    self.db.execute(f'ALTER TABLE deliveries ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0')

    def add(self, origin, account):
        with self.db:
            return self.db.execute(
                'INSERT OR IGNORE INTO subscriptions(origin,account) VALUES (?,?)',
                (origin, account)).rowcount > 0

    def remove(self, origin, account):
        with self.db:
            return self.db.execute(
                'DELETE FROM subscriptions WHERE origin=? AND account=?',
                (origin, account)).rowcount > 0

    def set_accounts(self, origin, value):
        accounts = {username(part) for part in value.replace('，', ',').split(',')}
        existing = {row[2] for row in self.subscriptions(origin)}
        with self.db:
            for account in existing - accounts:
                self.db.execute('DELETE FROM subscriptions WHERE origin=? AND account=?',
                                (origin, account))
            for account in accounts - existing:
                self.db.execute('INSERT INTO subscriptions(origin,account) VALUES (?,?)',
                                (origin, account))
        return sorted(accounts)

    def subscriptions(self, origin=None):
        if origin is None:
            return self.db.execute('SELECT id,origin,account FROM subscriptions').fetchall()
        return self.db.execute(
            'SELECT id,origin,account FROM subscriptions WHERE origin=?', (origin,)).fetchall()

    def ingest(self, sub, source, items):
        """Only call for successfully fetched streams; failed streams cannot baseline."""
        initialized = self.db.execute(
            'SELECT 1 FROM streams WHERE sub=? AND source=?', (sub, source)).fetchone()
        with self.db:
            for item in items:
                payload = json.dumps(item, ensure_ascii=False)
                self.db.execute('''INSERT INTO deliveries(sub,key,payload,done)
                    VALUES (?,?,?,?) ON CONFLICT(sub,key) DO UPDATE SET
                    payload=CASE WHEN deliveries.done=0 THEN excluded.payload
                    ELSE deliveries.payload END''',
                    (sub, item['key'], payload, int(not initialized)))
            self.db.execute('INSERT OR IGNORE INTO streams VALUES (?,?)', (sub, source))

    def pending(self, sub, limit=10, include_deferred=False):
        rows = self.db.execute(
            'SELECT key,payload,position FROM deliveries WHERE sub=? AND done=0 '
            'AND (retry_at<=? OR ?)',
            (sub, time.time(), int(include_deferred))).fetchall()
        items = [(key, json.loads(payload), pos) for key, payload, pos in rows]
        return sorted(items, key=lambda row: row[1]['time'])[:limit]

    def defer(self, sub, key):
        with self.db:
            self.db.execute('UPDATE deliveries SET attempts=attempts+1, '
                            'retry_at=?+min(21600, 3600*(1 << min(attempts,3))) '
                            'WHERE sub=? AND key=?', (time.time(), sub, key))

    def advance(self, sub, key, position, done):
        with self.db:
            self.db.execute('UPDATE deliveries SET position=?,done=? WHERE sub=? AND key=?',
                            (position, int(done), sub, key))

    def close(self):
        self.db.close()
