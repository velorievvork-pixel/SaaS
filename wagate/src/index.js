// Entry point: node src/index.js (settings in .env or environment).
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pino from 'pino';
import { Store, loadConfig } from './core.js';
import { createServer } from './server.js';
import { openKV } from './storage.js';
import { WhatsApp } from './whatsapp.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const envFile = path.join(root, '.env');
if (fs.existsSync(envFile)) {
  for (const line of fs.readFileSync(envFile, 'utf8').split('\n')) {
    const m = line.match(/^\s*([A-Z_]+)\s*=\s*(.*)\s*$/);
    if (m && process.env[m[1]] === undefined) process.env[m[1]] = m[2];
  }
}

const cfg = loadConfig();
const logger = pino({ level: process.env.LOG_LEVEL || 'info' });
const kv = await openKV({ databaseUrl: cfg.databaseUrl, dataDir: path.resolve(root, cfg.dataDir) });
logger.info(cfg.databaseUrl ? 'хранилище: Postgres (DATABASE_URL)' : `хранилище: файлы в ${cfg.dataDir}`);
const store = await Store.open(kv, { onError: (e) => logger.error(e, 'запись в хранилище не удалась') });

async function webhook(g) {
  if (!cfg.webhookUrl) return;
  const body = JSON.stringify({ typeWebhook: 'incomingMessageReceived', instanceData: { idInstance: cfg.id }, ...g });
  for (let i = 0; i < 3; i++) {
    try {
      const r = await fetch(cfg.webhookUrl, {
        method: 'POST', body,
        headers: { 'Content-Type': 'application/json', ...(cfg.webhookToken && { Authorization: `Bearer ${cfg.webhookToken}` }) },
        signal: AbortSignal.timeout(10_000),
      });
      if (r.ok) return;
    } catch { /* retry */ }
    await new Promise((ok) => setTimeout(ok, 2000 * (i + 1)));
  }
  logger.warn({ id: g.idMessage }, 'вебхук не принял сообщение; оно доступно через lastIncomingMessages');
}

const wa = new WhatsApp({
  kv,
  pairPhone: cfg.pairPhone,
  logger,
  onIncoming: (g) => {
    if (store.addIncoming(g)) {
      logger.info({ from: g.chatId, type: g.typeMessage }, 'входящее');
      webhook(g);
    }
  },
});

createServer({ cfg, wa, store, log: (...a) => logger.info(a.join(' ')) }).listen(cfg.port, cfg.host, () => {
  logger.info(`API: http://${cfg.host}:${cfg.port}/waInstance${cfg.id}/{method}/{token}`);
});
await wa.start();

// Free Render sleeps after 15 minutes without inbound HTTP, which would drop the WhatsApp connection.
// A request to our own public address every 10 minutes counts as inbound traffic and keeps it awake.
const publicUrl = (process.env.KEEPALIVE_URL || process.env.RENDER_EXTERNAL_URL || '').replace(/\/$/, '');
if (publicUrl) {
  setInterval(() => {
    fetch(`${publicUrl}/health`, { signal: AbortSignal.timeout(30_000) }).catch(() => {});
  }, 10 * 60_000).unref();
  logger.info(`keepalive: ${publicUrl}/health каждые 10 минут`);
}
