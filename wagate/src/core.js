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

// Longest prefix wins. +7 is shared: Kazakhstan numbers start with 76/77, the rest is Russia (Moscow time).
const ZONES = [
  ['76', 5, 'KZ'], ['77', 5, 'KZ'], ['7', 3, 'RU'],
  ['998', 5, 'UZ'], ['996', 6, 'KG'], ['992', 5, 'TJ'], ['993', 5, 'TM'], ['375', 3, 'BY'], ['374', 4, 'AM'],
  ['994', 4, 'AZ'], ['995', 4, 'GE'], ['373', 3, 'MD'],
];

// Non-working public holidays (weekdays only matter). Checked 25.09.2026 against official sources:
// KZ gov.kz / egov.kz 2026, RU consultant.ru (ТК ст. 112 + постановление №1466), UZ указ УП-257 от
// 24.12.2025 (afisha.uz, goldenpages.uz), KG новый Трудовой кодекс (Sputnik.kg 2026), BY mintrud.gov.by.
// The year lists include transfers and moving religious days (Курбан/Орозо айт, Радуница).
export const HOLIDAYS_BY_YEAR = {
  2026: {
    KZ: ['01-01', '01-02', '01-07', '03-09', '03-23', '03-24', '03-25', '05-01', '05-07', '05-11', '05-27', '07-06',
      '10-26', '12-16'],
    RU: ['01-01', '01-02', '01-05', '01-06', '01-07', '01-08', '01-09', '02-23', '03-09', '05-01', '05-11', '06-12',
      '11-04', '12-31'],
    UZ: ['01-01', '01-02', '03-09', '03-20', '03-23', '05-27', '05-28', '05-29', '08-31', '09-01', '10-01', '12-08'],
    KG: ['01-01', '01-02', '01-05', '01-06', '01-07', '01-08', '01-09', '03-20', '05-01', '05-04', '05-05', '05-06',
      '05-07', '05-08', '05-27', '08-31'],
    BY: ['01-01', '01-02', '01-07', '04-20', '04-21', '05-01', '07-03', '12-25'],
  },
};
// Other years: fixed dates only (no transfers, no moving religious days) — add the year above when known.
export const HOLIDAYS = {
  KZ: ['01-01', '01-02', '01-07', '03-08', '03-21', '03-22', '03-23', '05-01', '05-07', '05-09', '07-06', '10-25',
    '12-16'],
  RU: ['01-01', '01-02', '01-03', '01-04', '01-05', '01-06', '01-07', '01-08', '02-23', '03-08', '05-01', '05-09',
    '06-12', '11-04'],
  UZ: ['01-01', '03-08', '03-21', '05-09', '09-01', '10-01', '12-08'],
  KG: ['01-01', '01-02', '01-03', '01-04', '01-05', '01-06', '01-07', '01-08', '01-09', '03-08', '03-21', '05-01',
    '05-02', '05-03', '05-04', '05-05', '05-06', '05-07', '05-08', '05-09', '08-31'],
  BY: ['01-01', '01-02', '01-07', '03-08', '05-01', '05-09', '07-03', '11-07', '12-25'],
};

export function isHoliday(phone, localDate) {
  const iso = localDate.toISOString();
  const list = HOLIDAYS_BY_YEAR[iso.slice(0, 4)]?.[country(phone)] ?? HOLIDAYS[country(phone)] ?? [];
  return list.includes(iso.slice(5, 10));
}

function zone(phone) {
  const d = digits(phone);
  let best = null;
  for (const z of ZONES) if (d.startsWith(z[0]) && (!best || z[0].length > best[0].length)) best = z;
  return best;
}

/** UTC offset in hours for the phone's country, or null if unknown. */
export const utcOffset = (phone) => zone(phone)?.[1] ?? null;
export const country = (phone) => zone(phone)?.[2] ?? null;

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
  if (isHoliday(phone, local)) {
    return { ok: false, reason: 'holiday', localTime: local.toISOString().slice(0, 16) };
  }
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

export const HOLD_HOURS = 48;

const parseLines = (lines) => lines.flatMap((l) => { try { return [JSON.parse(l)]; } catch { return []; } });

/**
 * Messages and the stop list. Recent history lives in memory for fast checks; every change is written
 * through to the KV store (files or Postgres, see storage.js). Create with `await Store.open(kv)`.
 */
export class Store {
  static async open(kv, { keepDays = 30, onError = () => {} } = {}) {
    const incoming = parseLines(await kv.readLog('incoming'));
    const outgoing = parseLines(await kv.readLog('outgoing'));
    const stop = JSON.parse((await kv.get('stoplist')) || '[]');
    const allowed = JSON.parse((await kv.get('allowlist')) || '[]');
    const linkedAt = Number(await kv.get('linked_at')) || 0;
    const pausedUntil = Number(await kv.get('paused_until')) || 0;
    return new Store(kv, { incoming, outgoing, stop, allowed, linkedAt, pausedUntil, keepDays, onError });
  }

  constructor(kv, {
    incoming = [], outgoing = [], stop = [], allowed = [], linkedAt = 0, pausedUntil = 0, keepDays = 30,
    onError = () => {},
  } = {}) {
    this.kv = kv;
    this.onError = onError;
    const since = Date.now() / 1000 - keepDays * 86400;
    // Leads only. A number gets here when the gateway writes to it, or when the agent registers a lead
    // that was messaged by hand (allow). Everyone else — the owner's personal chats — is invisible:
    // their messages are dropped on arrival, never stored, never returned by the API.
    this.allowed = new Set(allowed);
    this.linkedAt = linkedAt;   // when this number was first linked: new numbers warm up slowly
    this.pausedUntil = pausedUntil;   // ms; new chats are off until then (after a failed send)
    this.contacted = new Set([...outgoing.map((m) => phoneFromChatId(m.chatId)), ...this.allowed]);
    this.incoming = incoming.filter((m) => m.timestamp >= since);
    this.outgoing = outgoing.filter((m) => m.timestamp >= since);
    this.seen = new Set(this.incoming.map((m) => m.idMessage));
    this.stop = new Set(stop);
    // Messages from numbers that are not leads (yet). Kept only in memory, never written or returned,
    // for up to HOLD_HOURS: a lead messaged by hand from the page is registered at the next poll, and its
    // early reply must not be lost. Anything still unclaimed after that is forgotten.
    this.held = new Map();
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

  /** Is this a lead's chat (and not a personal one)? */
  isLead(phone) { return this.contacted.has(digits(phone)); }

  /** Registers leads the owner messaged by hand, so their replies become visible to the agent. */
  allow(phones, now = Date.now()) {
    const added = [...new Set(phones.map(digits))].filter((p) => p.length >= 10 && p.length <= 15 && !this.allowed.has(p));
    for (const p of added) { this.allowed.add(p); this.contacted.add(p); }
    if (added.length) {
      const list = JSON.stringify([...this.allowed]);
      this.#write(() => this.kv.set('allowlist', list));
    }
    this.#release(now);
    return added.length;
  }

  #hold(g, now) {
    const phone = phoneFromChatId(g.chatId);
    const list = this.held.get(phone) || [];
    if (list.length < 50 && !list.some((m) => m.idMessage === g.idMessage)) list.push({ g, at: now });
    this.held.set(phone, list);
    if (this.held.size > 5000) this.held.delete(this.held.keys().next().value);   // bounded memory
  }

  /** Moves held messages of numbers that became leads into the store; forgets expired ones. */
  #release(now = Date.now()) {
    for (const [phone, list] of this.held) {
      const fresh = list.filter((x) => now - x.at < HOLD_HOURS * 3600e3);
      if (this.isLead(phone)) {
        this.held.delete(phone);
        for (const x of fresh) this.addIncoming(x.g, now);
      } else if (fresh.length) this.held.set(phone, fresh);
      else this.held.delete(phone);
    }
  }

  addIncoming(g, now = Date.now()) {
    if (!this.isLead(phoneFromChatId(g.chatId))) {   // not a lead: nothing stored, nothing shown
      this.#hold(g, now);
      return false;
    }
    if (this.seen.has(g.idMessage)) return false;   // WhatsApp resends on reconnect
    this.seen.add(g.idMessage);
    this.incoming.push(g);
    this.#write(() => this.kv.append('incoming', JSON.stringify(g)));
    if (isOptOut(textOf(g))) this.addStop(phoneFromChatId(g.chatId));
    this.queue.push({ receiptId: this.nextReceipt++, body: { typeWebhook: 'incomingMessageReceived', ...g } });
    return true;
  }

  addOutgoing(g) {
    this.outgoing.push(g);
    this.#write(() => this.kv.append('outgoing', JSON.stringify(g)));
    this.contacted.add(phoneFromChatId(g.chatId));
    this.#release();
  }

  addStop(phone) {
    this.stop.add(digits(phone));
    const list = JSON.stringify([...this.stop]);
    this.#write(() => this.kv.set('stoplist', list));
  }

  /** Remembers the first successful link of the number (only once). */
  /**
   * WhatsApp restricts accounts it suspects of spam: new chats fail, old ones still work
   * (faq.whatsapp.com/717472490411581). A failed send to a new chat pauses new chats, and it survives restarts.
   */
  pauseNewChats(untilMs) {
    this.pausedUntil = untilMs;
    this.#write(() => this.kv.set('paused_until', String(untilMs)));
  }

  markLinked(now = Date.now()) {
    if (this.linkedAt) return;
    this.linkedAt = now;
    this.#write(() => this.kv.set('linked_at', String(now)));
  }

  /** New chats in the last `days` days and how many of them answered at all. */
  replyStats(days = 30, now = Date.now()) {
    const since = now / 1000 - days * 86400;
    const firsts = new Map();
    for (const m of this.outgoing) {
      if (m.newChat && m.timestamp >= since && !firsts.has(m.chatId)) firsts.set(m.chatId, m.timestamp);
    }
    let answered = 0;
    for (const [chatId, t] of firsts) if (this.incoming.some((m) => m.chatId === chatId && m.timestamp >= t)) answered++;
    return { newChats: firsts.size, answered, rate: firsts.size ? answered / firsts.size : null };
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
/** How long to show "typing…" before a message: ~40 ms a character, clamped to 2–8 s. */
export function typingMs(text, perChar = 40) {
  return Math.min(8000, Math.max(2000, String(text ?? '').length * perChar));
}

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

  // The same long text to several people in a day is a mailing: it reads as a bot and WhatsApp bans for it.
  // Short phrases («Спасибо большое!») are fine to repeat.
  const norm = (t) => String(t).toLowerCase().replace(/\s+/g, ' ').trim();
  if (norm(text).length >= 60) {
    const others = new Set(store.outgoing.filter((m) => nowS - m.timestamp < 86400 && norm(m.textMessage || '') === norm(text)
      && phoneFromChatId(m.chatId) !== d).map((m) => m.chatId));
    if (others.size >= cfg.sameTextLimit) return { ok: false, status: 409, reason: 'same_text_many', sentTo: others.size };
  }

  const isNew = !store.contacted.has(d);
  if (isNew) {
    // WhatsApp is blocked in Russia since February 2026 (RKN removed its domains from the national DNS):
    // most people there see a new chat only with a VPN. Write to them by email; replies still go through.
    if (store.pausedUntil > now.getTime()) {
      return { ok: false, status: 429, reason: 'new_chats_paused', until: new Date(store.pausedUntil).toISOString() };
    }
    const cc = country(d);
    if ((cfg.noNewChatCountries || []).includes(cc)) return { ok: false, status: 451, reason: 'country_blocked', country: cc };
    const dayStart = new Date(now); dayStart.setUTCHours(0, 0, 0, 0);
    const newToday = store.outgoing.filter((m) => m.newChat && m.timestamp >= dayStart.getTime() / 1000).length;
    const limit = dailyNewChatLimit(store, cfg, now);
    if (newToday >= limit) return { ok: false, status: 429, reason: 'daily_new_chat_limit', limit };
    // WhatsApp counts messages left without an answer; a long run of them gets the number banned.
    // Low reply rate also means the texts or the segment are off: stop and let a person look.
    const st = store.replyStats(30, now.getTime());
    if (cfg.minReplyRate > 0 && st.newChats >= 20 && st.rate < cfg.minReplyRate) {
      return { ok: false, status: 429, reason: 'low_reply_rate', ...st };
    }
    const lastNew = store.outgoing.filter((m) => m.newChat).at(-1);
    if (lastNew && nowS - lastNew.timestamp < cfg.minIntervalSec) {
      return { ok: false, status: 429, reason: 'too_soon', retryAfterSec: Math.ceil(cfg.minIntervalSec - (nowS - lastNew.timestamp)) };
    }
  }
  return { ok: true, newChat: isNew };
}

/**
 * Optional slow start for a freshly linked number (WARMUP=true): 3 new chats a day the first week, 8 the
 * second. This is practice of WhatsApp API vendors, not an official WhatsApp figure, so it is off by default.
 * What is confirmed (TechCrunch, 17.10.2025): WhatsApp tests a monthly cap on messages to non-contacts who
 * do not reply — hence the low-reply-rate pause below, which is on by default.
 */
export function dailyNewChatLimit(store, cfg, now = new Date()) {
  if (!cfg.warmup || !store.linkedAt) return cfg.dailyNewChats;
  const days = (now.getTime() - store.linkedAt) / 86400e3;
  const ramp = days < 7 ? 3 : days < 14 ? 8 : Infinity;
  return Math.min(cfg.dailyNewChats, ramp);
}

export function loadConfig(env = process.env) {
  const num = (k, d) => (env[k] === undefined || env[k] === '' ? d : Number(env[k]));
  const cfg = {
    id: env.WAGATE_ID || '1101000001',
    token: (env.WAGATE_TOKEN || '').trim(),   // a pasted value often ends with a newline
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
    sameTextLimit: num('SAME_TEXT_LIMIT', 2),
    warmup: env.WARMUP === 'true',
    minReplyRate: num('MIN_REPLY_RATE', 0.1),
    checkLimit: num('CHECK_LIMIT', 45),        // checkWhatsapp calls a day: no bulk number lookups
    pauseHours: num('PAUSE_HOURS', 24),
    noNewChatCountries: (env.NO_NEW_CHAT_COUNTRIES ?? 'RU').split(',').map((c) => c.trim().toUpperCase()).filter(Boolean),
  };
  if (cfg.token.length < 16) throw new Error('WAGATE_TOKEN не задан или короче 16 символов (см. .env.example)');
  return cfg;
}
