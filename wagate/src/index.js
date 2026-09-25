// Entry point: node src/index.js (settings in .env or environment).
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pino from 'pino';
import { Store, loadConfig } from './core.js';
import { createServer } from './server.js';
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
const store = new Store(path.resolve(root, cfg.dataDir));

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
  dataDir: path.resolve(root, cfg.dataDir),
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
