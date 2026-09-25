// Storage backends and the WhatsApp session in them.
// Postgres tests run when TEST_DATABASE_URL is set (a throwaway database: tables are dropped first).
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { after, describe, test } from 'node:test';
import { initAuthCreds } from 'baileys';
import { useKvAuthState } from '../src/auth.js';
import { Store } from '../src/core.js';
import { FileKV, MemoryKV, PgKV } from '../src/storage.js';

const KZ = '77011234567';
const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), 'wagate-kv-'));

async function contract(makeKv, reopen) {
  const kv = await makeKv();
  assert.equal(await kv.get('missing'), null);
  await kv.set('a', '1');
  await kv.set('a', '2');
  assert.equal(await kv.get('a'), '2');
  await kv.del('a');
  assert.equal(await kv.get('a'), null);
  await kv.append('incoming', '{"x":1}');
  await kv.append('incoming', '{"x":2}');
  assert.deepEqual(await kv.readLog('incoming'), ['{"x":1}', '{"x":2}']);
  assert.deepEqual(await kv.readLog('outgoing'), []);

  // The whole point: a restart (new process, new connection) sees the same session and messages.
  const store = await Store.open(kv);
  store.allow([KZ]);
  store.addIncoming({ idMessage: 'm1', timestamp: Math.floor(Date.now() / 1000), typeMessage: 'textMessage',
    chatId: `${KZ}@c.us`, textMessage: 'Больше не пишите' });
  const auth = await useKvAuthState(kv);
  auth.state.creds.me = { id: `${KZ}:1@s.whatsapp.net` };
  await auth.saveCreds();
  await auth.state.keys.set({ 'pre-key': { 1: { public: Buffer.from([1, 2, 3]), private: Buffer.from([4, 5]) } } });
  await store.flush();
  await kv.close();

  const kv2 = await reopen();
  const store2 = await Store.open(kv2);
  assert.ok(store2.stop.has(KZ), 'stop list survives');
  assert.equal(store2.incoming.length, 1);
  assert.ok(store2.contacted.has(KZ));
  const auth2 = await useKvAuthState(kv2);
  assert.equal(auth2.state.creds.me.id, `${KZ}:1@s.whatsapp.net`, 'session survives');
  const { 1: key } = await auth2.state.keys.get('pre-key', ['1']);
  assert.ok(Buffer.isBuffer(key.public) && key.public.equals(Buffer.from([1, 2, 3])), 'binary keys survive');
  await auth2.state.keys.set({ 'pre-key': { 1: null } });
  assert.deepEqual(await auth2.state.keys.get('pre-key', ['1']), { 1: null });
  await auth2.clear();
  assert.equal(await kv2.get('auth:creds'), null);
  await kv2.close();
}

describe('storage', () => {
  test('MemoryKV', async () => {
    const kv = new MemoryKV();
    await contract(async () => kv, async () => kv);
  });

  test('FileKV', async () => {
    const dir = tmp();
    await contract(async () => new FileKV(dir), async () => new FileKV(dir));
  });

  test('a fresh session gets new credentials', async () => {
    const auth = await useKvAuthState(new MemoryKV());
    assert.equal(typeof auth.state.creds.registrationId, 'number');
    assert.notDeepEqual(auth.state.creds.noiseKey, initAuthCreds().noiseKey);
  });

  const url = process.env.TEST_DATABASE_URL;
  test('PgKV (Postgres, e.g. Neon)', { skip: !url && 'TEST_DATABASE_URL не задан' }, async () => {
    const { default: pg } = await import('pg');
    const c = new pg.Client({ connectionString: url });
    await c.connect();
    await c.query('DROP TABLE IF EXISTS wagate_kv, wagate_log');
    await c.end();
    await contract(() => PgKV.open(url), () => PgKV.open(url));
  });

  test('PgKV drops message log older than keepDays', { skip: !url && 'TEST_DATABASE_URL не задан' }, async () => {
    const kv = await PgKV.open(url);
    await kv.append('old', 'x');
    await kv.pool.query("UPDATE wagate_log SET at = now() - interval '40 days' WHERE stream = 'old'");
    await kv.close();
    const kv2 = await PgKV.open(url, { keepDays: 30 });
    assert.deepEqual(await kv2.readLog('old'), []);
    await kv2.close();
  });
});

after(() => {});
