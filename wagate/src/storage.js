// Where the gateway keeps its state: the WhatsApp session, incoming/outgoing messages, the stop list.
// FileKV — files in DATA_DIR (a VPS with a disk). PgKV — Postgres (free hosting whose disk is wiped on
// every restart, e.g. Render + Neon). MemoryKV — tests.
import fs from 'node:fs';
import path from 'node:path';

const safe = (key) => key.replace(/[^\w.@-]/g, '_');

export class FileKV {
  constructor(dir) {
    this.dir = dir;
    fs.mkdirSync(dir, { recursive: true });
  }

  async get(key) {
    try { return fs.readFileSync(path.join(this.dir, safe(key)), 'utf8'); } catch { return null; }
  }

  async set(key, value) {
    const p = path.join(this.dir, safe(key));
    fs.writeFileSync(p + '.tmp', value);
    fs.renameSync(p + '.tmp', p);   // a crash mid-write must not leave a half-written session file
  }

  async del(key) {
    try { fs.unlinkSync(path.join(this.dir, safe(key))); } catch { /* already gone */ }
  }

  async append(stream, line) {
    fs.appendFileSync(path.join(this.dir, `${safe(stream)}.jsonl`), line + '\n');
  }

  async readLog(stream) {
    try {
      return fs.readFileSync(path.join(this.dir, `${safe(stream)}.jsonl`), 'utf8').split('\n').filter(Boolean);
    } catch { return []; }
  }

  async close() {}
}

export class MemoryKV {
  constructor() { this.kv = new Map(); this.logs = new Map(); }
  async get(key) { return this.kv.has(key) ? this.kv.get(key) : null; }
  async set(key, value) { this.kv.set(key, value); }
  async del(key) { this.kv.delete(key); }
  async append(stream, line) { (this.logs.get(stream) || this.logs.set(stream, []).get(stream)).push(line); }
  async readLog(stream) { return [...(this.logs.get(stream) || [])]; }
  async close() {}
}

export class PgKV {
  /** keepDays: message log older than this is deleted on start (free databases are small). */
  static async open(url, { keepDays = 30 } = {}) {
    const { default: pg } = await import('pg');
    const ssl = /sslmode=disable/.test(url) || /@(localhost|127\.0\.0\.1)[:/]/.test(url) ? false : { rejectUnauthorized: false };
    const pool = new pg.Pool({ connectionString: url, ssl, max: 4 });
    await pool.query(`CREATE TABLE IF NOT EXISTS wagate_kv (
      key text PRIMARY KEY, value text NOT NULL, updated_at timestamptz NOT NULL DEFAULT now())`);
    await pool.query(`CREATE TABLE IF NOT EXISTS wagate_log (
      id bigserial PRIMARY KEY, stream text NOT NULL, line text NOT NULL, at timestamptz NOT NULL DEFAULT now())`);
    await pool.query('CREATE INDEX IF NOT EXISTS wagate_log_stream ON wagate_log (stream, id)');
    await pool.query(`DELETE FROM wagate_log WHERE at < now() - make_interval(days => $1)`, [keepDays]);
    return new PgKV(pool);
  }

  constructor(pool) { this.pool = pool; }

  async get(key) {
    const r = await this.pool.query('SELECT value FROM wagate_kv WHERE key = $1', [key]);
    return r.rows[0]?.value ?? null;
  }

  async set(key, value) {
    await this.pool.query(`INSERT INTO wagate_kv (key, value) VALUES ($1, $2)
      ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()`, [key, value]);
  }

  async del(key) {
    await this.pool.query('DELETE FROM wagate_kv WHERE key = $1', [key]);
  }

  async append(stream, line) {
    await this.pool.query('INSERT INTO wagate_log (stream, line) VALUES ($1, $2)', [stream, line]);
  }

  async readLog(stream) {
    const r = await this.pool.query('SELECT line FROM wagate_log WHERE stream = $1 ORDER BY id', [stream]);
    return r.rows.map((x) => x.line);
  }

  async close() { await this.pool.end(); }
}

/** DATABASE_URL set → Postgres, otherwise files in dataDir. */
export async function openKV({ databaseUrl, dataDir }) {
  return databaseUrl ? PgKV.open(databaseUrl) : new FileKV(dataDir);
}
