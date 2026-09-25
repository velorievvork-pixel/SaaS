// Pure logic: phones and chat ids, Green-API message format, sending rules, storage.
// Nothing here talks to WhatsApp, so all of it is unit-tested.
// ---------- phones and chat ids ----------

/** Digits only; a Russian/Kazakh "8XXXXXXXXXX" becomes "7XXXXXXXXXX". */
export function digits(phone) {
  let d = String(phone ?? '').replace(/\D/g, '');
  if (d.length === 11 && d.startsWith('8')) d = '7' + d.slice(1);
  return d;
}

/** Green-API chat id "77011234567@c.us" or any phone → "77011234567". */
export function phoneFromChatId(chatId) {
  return digits(String(chatId ?? '').split('@')[0]);
}

export const toChatId = (phone) => `${digits(phone)}@c.us`;
export const toJid = (phone) => `${digits(phone)}@s.whatsapp.net`;

// ---------- recipient's working hours ----------

// Longest prefix wins. +7 is shared: Kazakhstan mobiles start with 70x/747/77x, the rest is Russia (Moscow time).
const ZONES = [
  ['76', 5], ['77', 5], ['7', 3],
  ['998', 5], ['996', 6], ['992', 5], ['993', 5], ['375', 3], ['374', 4], ['994', 4], ['995', 4], ['373', 3],
];

/** UTC offset in hours for the phone's country, or null if unknown. */
export function utcOffset(phone) {
  const d = digits(phone);
  let best = null;
  for (const [prefix, off] of ZONES) {
    if (d.startsWith(prefix) && (!best || prefix.length > best[0].length)) best = [prefix, off];
  }
  return best ? best[1] : null;
}

/** Is it a working hour for the recipient? hours "9-18", days "1-5" (Mon=1). */
export function inWorkingHours(phone, now = new Date(), hours = '9-18', days = '1-5') {
  const off = utcOffset(phone);
  if (off === null) return { ok: false, reason: 'unknown_country' };
  const local = new Date(now.getTime() + off * 3600e3);
  const [h0, h1] = hours.split('-').map(Number);
  const [d0, d1] = days.split('-').map(Number);
  const dow = local.getUTCDay() || 7;
  const hour = local.getUTCHours() + local.getUTCMinutes() / 60;
  if (dow < d0 || dow > d1) return { ok: false, reason: 'weekend', localTime: local.toISOString().slice(0, 16) };
  if (hour < h0 || hour >= h1) return { ok: false, reason: 'off_hours', localTime: local.toISOString().slice(0, 16) };
  return { ok: true };
}

// ---------- opt-out ----------

// A person asking not to be contacted goes on the stop list; the gateway will not write to them again.
// «Не интересует» is not here on purpose: one polite closing reply is still allowed after a refusal.
const OPT_OUT = /(не\s+пиш(и|ите)|больше\s+не\s+пиш|не\s+беспоко|отпиш(и|ите)|удалите\s+(мой\s+)?номер|^\s*стоп\s*$|^\s*stop\s*$|это\s+спам|unsubscribe|жазбаңыз)/i;
export const isOptOut = (text) => OPT_OUT.test(String(text ?? ''));

// ---------- Baileys message → Green-API format ----------

const TYPE_MAP = {
  conversation: 'textMessage', extendedTextMessage: 'extendedTextMessage', imageMessage: 'imageMessage',
  videoMessage: 'videoMessage', audioMessage: 'audioMessage', documentMessage: 'documentMessage',
  stickerMessage: 'stickerMessage', reactionMessage: 'reactionMessage', contactMessage: 'contactMessage',
  locationMessage: 'locationMessage',
};

/** Unwraps ephemeral / view-once / edited wrappers. */
function inner(message) {
  let m = message || {};
  for (let i = 0; i < 4; i++) {
    const w = m.ephemeralMessage || m.viewOnceMessage || m.viewOnceMessageV2 || m.documentWithCaptionMessage
      || m.editedMessage;
    if (!w?.message) break;
    m = w.message;
  }
  return m;
}

/**
 * Baileys WAMessage → Green-API "lastIncomingMessages" item, or null for what we do not surface
 * (our own messages, groups, statuses, protocol messages).
 */
export function toGreenMessage(msg, direction = 'incoming') {
  const key = msg?.key || {};
  if (direction === 'incoming' && key.fromMe) return null;
  // Baileys 7 may address a person by LID ("…@lid"); the phone JID then comes in remoteJidAlt.
  const jid = [key.remoteJid, key.remoteJidAlt].find((j) => j && j.endsWith('@s.whatsapp.net'));
  if (!jid) return null;
  const m = inner(msg.message);
  const kind = Object.keys(m).find((k) => TYPE_MAP[k]);
  if (!kind) return null;
  const text = m.conversation || m.extendedTextMessage?.text || m[kind]?.caption || m.reactionMessage?.text || '';
  const out = {
    type: direction,
    idMessage: key.id || '',
    timestamp: Number(msg.messageTimestamp?.low ?? msg.messageTimestamp ?? 0),
    typeMessage: TYPE_MAP[kind],
    chatId: toChatId(jid.split('@')[0]),
    senderId: toChatId(jid.split('@')[0]),
    senderName: msg.pushName || '',
  };
  if (kind === 'conversation') out.textMessage = text;
  else if (kind === 'extendedTextMessage') out.extendedTextMessage = { text };
  else if (kind === 'reactionMessage') out.extendedTextMessageData = { text };
  else if (text) out.caption = text;
  return out;
}

export const textOf = (g) => g?.textMessage ?? g?.extendedTextMessage?.text ?? g?.caption
  ?? g?.extendedTextMessageData?.text ?? '';

// ---------- storage ----------

const parseLines = (lines) => lines.flatMap((l) => { try { return [JSON.parse(l)]; } catch { return []; } });

/**
 * Messages and the stop list. Recent history lives in memory for fast checks; every change is written
 * through to the KV store (files or Postgres, see storage.js). Create with `await Store.open(kv)`.
 */
export class Store {
  static async open(kv, { keepDays = 14, onError = () => {} } = {}) {
    const incoming = parseLines(await kv.readLog('incoming'));
    const outgoing = parseLines(await kv.readLog('outgoing'));
    const stop = JSON.parse((await kv.get('stoplist')) || '[]');
    return new Store(kv, { incoming, outgoing, stop, keepDays, onError });
  }

  constructor(kv, { incoming = [], outgoing = [], stop = [], keepDays = 14, onError = () => {} } = {}) {
    this.kv = kv;
    this.onError = onError;
    const since = Date.now() / 1000 - keepDays * 86400;
    // Everyone we ever talked to, not only the last two weeks: a reply to an old chat is not a new chat.
    this.contacted = new Set([...outgoing, ...incoming].map((m) => phoneFromChatId(m.chatId)));
    this.incoming = incoming.filter((m) => m.timestamp >= since);
    this.outgoing = outgoing.filter((m) => m.timestamp >= since);
    this.seen = new Set(this.incoming.map((m) => m.idMessage));
    this.stop = new Set(stop);
    this.queue = [];      // notifications for receiveNotification / deleteNotification
    this.nextReceipt = 1;
    this.pending = Promise.resolve();
  }

  #write(fn) {
    this.pending = this.pending.then(fn).catch((e) => this.onError(e));
    return this.pending;
  }

  /** Resolves when everything written so far has reached the store. */
  flush() { return this.pending; }

  addIncoming(g) {
    if (this.seen.has(g.idMessage)) return false;   // WhatsApp resends on reconnect
    this.seen.add(g.idMessage);
    this.incoming.push(g);
    this.#write(() => this.kv.append('incoming', JSON.stringify(g)));
    this.contacted.add(phoneFromChatId(g.chatId));
    if (isOptOut(textOf(g))) this.addStop(phoneFromChatId(g.chatId));
    this.queue.push({ receiptId: this.nextReceipt++, body: { typeWebhook: 'incomingMessageReceived', ...g } });
    return true;
  }

  addOutgoing(g) {
    this.outgoing.push(g);
    this.#write(() => this.kv.append('outgoing', JSON.stringify(g)));
    this.contacted.add(phoneFromChatId(g.chatId));
  }

  addStop(phone) {
    this.stop.add(digits(phone));
    const list = JSON.stringify([...this.stop]);
    this.#write(() => this.kv.set('stoplist', list));
  }

  last(list, minutes, now = Date.now()) {
    const since = now / 1000 - minutes * 60;
    return this[list].filter((m) => m.timestamp >= since).sort((a, b) => b.timestamp - a.timestamp);
  }
}

// ---------- sending rules ----------

/**
 * Decides whether a message may go out now. The cap counts only NEW chats (numbers that never
 * wrote to us and we never wrote to): replies in a live conversation are not cold outreach.
 */
export function checkSend({ phone, text, store, cfg, now = new Date() }) {
  const d = digits(phone);
  if (d.length < 10 || d.length > 15) return { ok: false, status: 400, reason: 'bad_phone' };
  if (!String(text ?? '').trim()) return { ok: false, status: 400, reason: 'empty_text' };
  if (String(text).length > cfg.maxLength) return { ok: false, status: 400, reason: 'too_long' };
  if (store.stop.has(d)) return { ok: false, status: 403, reason: 'stop_list' };

  if (cfg.enforceHours) {
    const wh = inWorkingHours(d, now, cfg.workHours, cfg.workDays);
    if (!wh.ok) return { ok: false, status: 425, reason: wh.reason, localTime: wh.localTime };
  }

  const nowS = now.getTime() / 1000;
  const dup = store.outgoing.find((m) => phoneFromChatId(m.chatId) === d && m.textMessage === text
    && nowS - m.timestamp < 86400);
  if (dup) return { ok: false, status: 409, reason: 'duplicate_24h' };

  const isNew = !store.contacted.has(d);
  if (isNew) {
    const dayStart = new Date(now); dayStart.setUTCHours(0, 0, 0, 0);
    const newToday = store.outgoing.filter((m) => m.newChat && m.timestamp >= dayStart.getTime() / 1000).length;
    if (newToday >= cfg.dailyNewChats) return { ok: false, status: 429, reason: 'daily_new_chat_limit', limit: cfg.dailyNewChats };
    const lastNew = store.outgoing.filter((m) => m.newChat).at(-1);
    if (lastNew && nowS - lastNew.timestamp < cfg.minIntervalSec) {
      return { ok: false, status: 429, reason: 'too_soon', retryAfterSec: Math.ceil(cfg.minIntervalSec - (nowS - lastNew.timestamp)) };
    }
  }
  return { ok: true, newChat: isNew };
}

export function loadConfig(env = process.env) {
  const num = (k, d) => (env[k] === undefined || env[k] === '' ? d : Number(env[k]));
  const cfg = {
    id: env.WAGATE_ID || '1101000001',
    token: env.WAGATE_TOKEN || '',
    // On Render the platform proxy has to reach us, so listen on all interfaces there.
    host: env.HOST || (env.RENDER ? '0.0.0.0' : '127.0.0.1'),
    port: num('PORT', 3000),
    dataDir: env.DATA_DIR || 'data',
    databaseUrl: env.DATABASE_URL || '',
    webhookUrl: env.WEBHOOK_URL || '',
    webhookToken: env.WEBHOOK_TOKEN || '',
    pairPhone: digits(env.PAIR_PHONE || ''),
    dailyNewChats: num('DAILY_NEW_CHATS', 15),
    minIntervalSec: num('MIN_INTERVAL_SEC', 120),
    enforceHours: (env.ENFORCE_HOURS ?? 'true') !== 'false',
    workHours: env.WORK_HOURS || '9-18',
    workDays: env.WORK_DAYS || '1-5',
    maxLength: num('MAX_LENGTH', 2000),
  };
  if (cfg.token.length < 16) throw new Error('WAGATE_TOKEN не задан или короче 16 символов (см. .env.example)');
  return cfg;
}
