// HTTP API compatible with the Green-API methods our scripts use:
//   /waInstance{id}/{method}/{token}
// getStateInstance, qr, getAuthorizationCode, sendMessage, checkWhatsapp,
// lastIncomingMessages, lastOutgoingMessages, receiveNotification, deleteNotification/{receiptId}
// plus wagateLimits (our own: today's counters and rules).
import crypto from 'node:crypto';
import http from 'node:http';
import { checkSend, dailyNewChatLimit, digits, phoneFromChatId, toChatId } from './core.js';

const MAX_BODY = 64 * 1024;

function safeEqual(a, b) {
  const x = Buffer.from(String(a)), y = Buffer.from(String(b));
  return x.length === y.length && crypto.timingSafeEqual(x, y);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let size = 0, over = false; const chunks = [];
    req.on('data', (c) => {
      if (over) return;   // drain the rest without keeping it, so the client still gets the 413
      size += c.length;
      if (size > MAX_BODY) { over = true; chunks.length = 0; reject(Object.assign(new Error('too large'), { status: 413 })); return; }
      chunks.push(c);
    });
    req.on('end', () => {
      if (over) return;
      const raw = Buffer.concat(chunks).toString('utf8');
      if (!raw) return resolve({});
      try { resolve(JSON.parse(raw)); } catch { reject(Object.assign(new Error('bad json'), { status: 400 })); }
    });
    req.on('error', reject);
  });
}

/**
 * wa: { state(): 'authorized'|'notAuthorized'|'starting'|'blocked', qr(): string|null (base64 png),
 *       pairingCode(phone): Promise<string>, send(phone, text): Promise<string idMessage>,
 *       exists(phone): Promise<boolean> }
 */
export function createHandler({ cfg, wa, store, now = () => new Date(), log = () => {} }) {
  // Sends go one at a time: two parallel requests must not both pass the daily limit check.
  let sendLock = Promise.resolve();
  const checks = { day: '', n: 0 };
  const serial = (fn) => {
    const run = sendLock.then(fn, fn);
    sendLock = run.catch(() => {});
    return run;
  };
  const route = new RegExp(`^/waInstance${cfg.id}/([A-Za-z]+)/([^/?]+)(?:/([^/?]+))?$`);

  async function handle(req, res) {
    const url = new URL(req.url, 'http://x');
    const send = (status, body) => {
      res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify(body));
    };
    if (url.pathname === '/health') return send(200, { ok: true, state: wa.state() });

    const m = url.pathname.match(route);
    if (!m) return send(404, { error: 'not found' });
    const [, method, rawToken, extra] = m;
    // Render's "Generate" can put "/", "+" or "=" in the token: clients send it percent-encoded.
    let token = rawToken;
    try { token = decodeURIComponent(rawToken); } catch { /* malformed escape: compared as is */ }
    if (!safeEqual(token, cfg.token)) return send(401, { error: 'bad token' });

    const needAuth = () => {
      if (wa.state() === 'authorized') return false;
      send(503, { error: 'whatsapp not authorized', stateInstance: wa.state() });
      return true;
    };

    switch (method) {
      case 'getStateInstance':
        return send(200, { stateInstance: wa.state() });

      case 'qr': {
        if (wa.state() === 'authorized') return send(200, { type: 'alreadyLogged', message: 'instance account already authorized' });
        const qr = wa.qr();
        return qr ? send(200, { type: 'qrCode', message: qr }) : send(200, { type: 'error', message: 'QR ещё не готов, повторите через пару секунд' });
      }

      case 'getAuthorizationCode': {
        if (req.method !== 'POST') return send(405, { error: 'POST' });
        const body = await readBody(req);
        const phone = digits(body.phoneNumber);
        if (phone.length < 10) return send(400, { error: 'phoneNumber' });
        return send(200, { status: true, code: await wa.pairingCode(phone) });
      }

      case 'sendMessage': {
        if (req.method !== 'POST') return send(405, { error: 'POST' });
        const body = await readBody(req);
        const phone = phoneFromChatId(body.chatId);
        if (!String(body.chatId || '').endsWith('@c.us')) return send(400, { error: 'chatId must be <phone>@c.us' });
        if (needAuth()) return;
        return serial(async () => {
        const verdict = checkSend({ phone, text: body.message, store, cfg, now: now() });
        if (!verdict.ok) {
          log('send refused', phone, verdict.reason);
          return send(verdict.status, { error: verdict.reason, ...verdict, ok: undefined, status: undefined });
        }
        if (verdict.newChat && !(await wa.exists(phone))) return send(404, { error: 'no_whatsapp' });
        let idMessage;
        try {
          idMessage = await wa.send(phone, body.message);
        } catch (e) {
          log('send failed', phone, e.message);
          if (!verdict.newChat) return send(502, { error: 'send_failed' });
          const until = now().getTime() + cfg.pauseHours * 3600e3;
          store.pauseNewChats(until);
          return send(502, { error: 'send_failed', newChatsPausedUntil: new Date(until).toISOString() });
        }
        store.addOutgoing({
          type: 'outgoing', idMessage, timestamp: Math.floor(now().getTime() / 1000), typeMessage: 'textMessage',
          chatId: toChatId(phone), textMessage: body.message, newChat: verdict.newChat,
        });
        return send(200, { idMessage });
        });
      }

      case 'checkWhatsapp': {
        if (req.method !== 'POST') return send(405, { error: 'POST' });
        const body = await readBody(req);
        const phone = digits(body.phoneNumber);
        if (phone.length < 10) return send(400, { error: 'phoneNumber' });
        if (needAuth()) return;
        // WhatsApp forbids automated harvesting of numbers (faq.whatsapp.com/361005896189245):
        // check only numbers about to be messaged, a few dozen a day.
        const day = now().toISOString().slice(0, 10);
        if (checks.day !== day) Object.assign(checks, { day, n: 0 });
        if (checks.n >= cfg.checkLimit) return send(429, { error: 'check_limit', limit: cfg.checkLimit });
        checks.n += 1;
        return send(200, { existsWhatsapp: await wa.exists(phone) });
      }

      case 'lastIncomingMessages':
      case 'lastOutgoingMessages': {
        const minutes = Math.min(Number(url.searchParams.get('minutes') || 1440), 14 * 1440);
        return send(200, store.last(method === 'lastIncomingMessages' ? 'incoming' : 'outgoing', minutes, now().getTime()));
      }

      case 'receiveNotification': {
        const n = store.queue[0];
        return send(200, n || null);
      }

      case 'deleteNotification': {
        const id = Number(extra);
        const i = store.queue.findIndex((n) => n.receiptId === id);
        if (i >= 0) store.queue.splice(i, 1);
        return send(200, { result: i >= 0 });
      }

      case 'wagateAllow': {
        // Our own method: numbers of leads the owner already messaged by hand. Only these (and numbers
        // the gateway wrote to) are read; everything else is treated as the owner's private chats.
        if (req.method !== 'POST') return send(405, { error: 'POST' });
        const body = await readBody(req);
        if (!Array.isArray(body.phones) || body.phones.length > 1000) return send(400, { error: 'phones: array' });
        return send(200, { added: store.allow(body.phones.map(String)), leads: store.contacted.size });
      }

      case 'wagateLimits': {
        const dayStart = new Date(now()); dayStart.setUTCHours(0, 0, 0, 0);
        const newToday = store.outgoing.filter((o) => o.newChat && o.timestamp >= dayStart.getTime() / 1000).length;
        return send(200, {
          newChatsToday: newToday, dailyNewChats: dailyNewChatLimit(store, cfg, now()), dailyNewChatsMax: cfg.dailyNewChats,
          linkedAt: store.linkedAt ? new Date(store.linkedAt).toISOString() : null, replies30d: store.replyStats(30, now().getTime()), minIntervalSec: cfg.minIntervalSec,
          enforceHours: cfg.enforceHours, workHours: cfg.workHours, workDays: cfg.workDays, stopList: store.stop.size, leads: store.contacted.size,
          newChatsPausedUntil: store.pausedUntil > now().getTime() ? new Date(store.pausedUntil).toISOString() : null,
          checksToday: checks.day === now().toISOString().slice(0, 10) ? checks.n : 0, checkLimit: cfg.checkLimit,
        });
      }

      default:
        return send(404, { error: `method ${method} is not supported` });
    }
  }

  return async (req, res) => {
    try {
      await handle(req, res);
    } catch (e) {
      log('error', e.message);
      if (!res.headersSent) {
        res.writeHead(e.status || 500, { 'Content-Type': 'application/json', Connection: 'close' });
        res.end(JSON.stringify({ error: e.status ? e.message : 'internal error' }));
      }
    }
  };
}

export const createServer = (opts) => http.createServer(createHandler(opts));
