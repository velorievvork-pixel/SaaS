// Offline tests: a fake WhatsApp stands in for Baileys. Run: npm test
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { after, before, beforeEach, describe, test } from 'node:test';
import {
  Store, checkSend, country, typingMs, digits, inWorkingHours, isOptOut, loadConfig, phoneFromChatId, toGreenMessage, utcOffset,
} from '../src/core.js';
import { createServer } from '../src/server.js';
import { FileKV } from '../src/storage.js';

const TOKEN = 'test-token-0123456789';
// Tuesday 2026-09-29 06:00 UTC = 11:00 Astana (+5), 09:00 Moscow (+3)
const TUE_11_ASTANA = new Date('2026-09-29T06:00:00Z');
const KZ = '77011234567', KZ2 = '77019876543', RU = '79161234567';

const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), 'wagate-'));
const newStore = (dir = tmp()) => Store.open(new FileKV(dir));
const cfgWith = (over = {}) => ({ ...loadConfig({ WAGATE_TOKEN: TOKEN, WAGATE_ID: '1101', MIN_INTERVAL_SEC: '0' }), ...over });

describe('phones and time zones', () => {
  test('8… becomes 7…, chat id → phone', () => {
    assert.equal(digits('8 (701) 123-45-67'), KZ);
    assert.equal(phoneFromChatId(`${KZ}@c.us`), KZ);
  });
  test('+7 7xx is Kazakhstan, +7 9xx is Russia', () => {
    assert.equal(utcOffset(KZ), 5);
    assert.equal(utcOffset('77172123456'), 5);   // Astana landline
    assert.equal(utcOffset(RU), 3);
    assert.equal(utcOffset('996555123456'), 6);
    assert.equal(utcOffset('12025550100'), null);
  });
  test('no messages on public holidays of the recipient\'s country (2026, with transfers)', () => {
    assert.deepEqual([country(KZ), country(RU), country('996555123456')], ['KZ', 'RU', 'KG']);
    const mon26oct = new Date('2026-10-26T06:00:00Z');       // Republic Day moved from Sunday 25.10
    assert.equal(inWorkingHours(KZ, mon26oct).reason, 'holiday');
    assert.equal(inWorkingHours(RU, mon26oct).ok, true);      // not a holiday in Russia
    assert.equal(inWorkingHours(RU, new Date('2026-11-04T08:00:00Z')).reason, 'holiday');
    assert.equal(inWorkingHours(KZ, new Date('2026-08-31T06:00:00Z')).ok, true);   // 30.08 is not a day off in 2026
    assert.equal(inWorkingHours('996555123456', new Date('2026-11-09T05:00:00Z')).ok, true);  // KG: no transfers
    assert.equal(inWorkingHours('998901234567', new Date('2026-01-14T06:00:00Z')).ok, true);  // UZ: 14.01 works
  });
  test('working hours are the recipient\'s, not ours', () => {
    assert.equal(inWorkingHours(KZ, TUE_11_ASTANA).ok, true);
    assert.equal(inWorkingHours(KZ, new Date('2026-09-29T14:00:00Z')).reason, 'off_hours');  // 19:00 Astana
    assert.equal(inWorkingHours(KZ, new Date('2026-09-27T06:00:00Z')).reason, 'weekend');    // Sunday
    assert.equal(inWorkingHours('12025550100', TUE_11_ASTANA).reason, 'unknown_country');
  });
});

describe('opt-out', () => {
  test('requests to stop writing', () => {
    for (const t of ['Больше не пишите сюда', 'не беспокойте', 'СТОП', 'Удалите мой номер', 'это спам']) {
      assert.ok(isOptOut(t), t);
    }
  });
  test('a refusal is not an opt-out, one polite close is still allowed', () => {
    assert.equal(isOptOut('Здравствуйте не интересует'), false);
    assert.equal(isOptOut('Пишите на почту директору'), false);
  });
});

describe('Baileys → Green-API message', () => {
  test('text from a phone JID', () => {
    const g = toGreenMessage({ key: { remoteJid: `${KZ}@s.whatsapp.net`, id: 'A1' }, messageTimestamp: 1790000000,
      pushName: 'Радмила', message: { conversation: 'Здравствуйте' } });
    assert.deepEqual([g.chatId, g.textMessage, g.typeMessage, g.senderName], [`${KZ}@c.us`, 'Здравствуйте', 'textMessage', 'Радмила']);
  });
  test('LID address with the phone in remoteJidAlt', () => {
    const g = toGreenMessage({ key: { remoteJid: '123456789@lid', remoteJidAlt: `${KZ}@s.whatsapp.net`, id: 'A2' },
      messageTimestamp: 1, message: { extendedTextMessage: { text: '+7 700 760 0141 Виктория' } } });
    assert.equal(g.chatId, `${KZ}@c.us`);
    assert.equal(g.extendedTextMessage.text, '+7 700 760 0141 Виктория');
  });
  test('own messages, groups and protocol messages are skipped', () => {
    assert.equal(toGreenMessage({ key: { remoteJid: `${KZ}@s.whatsapp.net`, fromMe: true }, message: { conversation: 'x' } }), null);
    assert.equal(toGreenMessage({ key: { remoteJid: '1203630@g.us' }, message: { conversation: 'x' } }), null);
    assert.equal(toGreenMessage({ key: { remoteJid: `${KZ}@s.whatsapp.net` }, message: { protocolMessage: {} } }), null);
  });
  test('voice message keeps its type for a human to listen to', () => {
    const g = toGreenMessage({ key: { remoteJid: `${KZ}@s.whatsapp.net`, id: 'A3' }, messageTimestamp: 1, message: { audioMessage: {} } });
    assert.equal(g.typeMessage, 'audioMessage');
  });
});

describe('sending rules', () => {
  let store;
  beforeEach(async () => { store = await newStore(); });
  const out = (phone, t, extra = {}) => store.addOutgoing({ type: 'outgoing', idMessage: 'x', timestamp: t,
    typeMessage: 'textMessage', chatId: `${phone}@c.us`, textMessage: 'hi', newChat: true, ...extra });

  test('daily cap counts only new chats', () => {
    const cfg = cfgWith({ dailyNewChats: 1 });
    const t = TUE_11_ASTANA.getTime() / 1000 - 600;
    out(KZ, t);
    assert.equal(checkSend({ phone: KZ2, text: 'a', store, cfg, now: TUE_11_ASTANA }).reason, 'daily_new_chat_limit');
    // Reply in an existing chat is not cold outreach
    assert.equal(checkSend({ phone: KZ, text: 'другой текст', store, cfg, now: TUE_11_ASTANA }).ok, true);
  });
  test('pause between new chats', () => {
    out(KZ, TUE_11_ASTANA.getTime() / 1000 - 30);
    const v = checkSend({ phone: KZ2, text: 'a', store, cfg: cfgWith({ minIntervalSec: 120 }), now: TUE_11_ASTANA });
    assert.equal(v.reason, 'too_soon');
    assert.equal(v.retryAfterSec, 90);
  });
  test('typing indicator lasts like a person typing, 2 to 8 seconds', () => {
    assert.deepEqual([typingMs('Да'), typingMs('x'.repeat(100)), typingMs('x'.repeat(1000))], [2000, 4000, 8000]);
  });
  test('no new chats to Russian numbers (WhatsApp blocked there), replies still go', () => {
    const at = new Date('2026-09-29T08:00:00Z');                      // Tue 11:00 Moscow
    const v = checkSend({ phone: RU, text: 'Здравствуйте', store, cfg: cfgWith(), now: at });
    assert.deepEqual([v.reason, v.country], ['country_blocked', 'RU']);
    assert.equal(checkSend({ phone: KZ, text: 'Здравствуйте', store, cfg: cfgWith(), now: at }).ok, true);
    assert.equal(checkSend({ phone: RU, text: 'x', store, cfg: cfgWith({ noNewChatCountries: [] }), now: at }).ok, true);
    assert.deepEqual(loadConfig({ WAGATE_TOKEN: TOKEN, NO_NEW_CHAT_COUNTRIES: '' }).noNewChatCountries, []);
    store.contacted.add(RU);                                          // they wrote first or answered before
    assert.equal(checkSend({ phone: RU, text: 'Спасибо', store, cfg: cfgWith(), now: at }).ok, true);
  });
  test('same text to the same number within a day is refused', () => {
    out(KZ, TUE_11_ASTANA.getTime() / 1000 - 3600);
    assert.equal(checkSend({ phone: KZ, text: 'hi', store, cfg: cfgWith(), now: TUE_11_ASTANA }).reason, 'duplicate_24h');
  });
  test('the same long text to many people in a day is refused, short phrases are fine', () => {
    const long = 'Здравствуйте! Меня зовут Ярослав, я из компании Camirix. Вижу, что вы ищете менеджера.';
    const t = TUE_11_ASTANA.getTime() / 1000 - 600;
    out(KZ, t, { textMessage: long });
    out(RU, t, { textMessage: long });
    const cfg = cfgWith({ dailyNewChats: 99 });
    assert.equal(checkSend({ phone: KZ2, text: long, store, cfg, now: TUE_11_ASTANA }).reason, 'same_text_many');
    out(KZ, t, { textMessage: 'Спасибо большое!' });
    out(RU, t, { textMessage: 'Спасибо большое!' });
    assert.equal(checkSend({ phone: KZ2, text: 'Спасибо большое!', store, cfg, now: TUE_11_ASTANA }).ok, true);
  });
  test('optional warm-up (WARMUP=true): 3 new chats a day, then 8, then the normal limit; off by default', () => {
    assert.equal(loadConfig({ WAGATE_TOKEN: TOKEN }).warmup, false);
    const cfg = cfgWith({ dailyNewChats: 15, warmup: true });
    store.linkedAt = TUE_11_ASTANA.getTime() - 2 * 86400e3;
    const t = TUE_11_ASTANA.getTime() / 1000 - 600;
    for (const p of ['77010000001', '77010000002', '77010000003']) out(p, t);
    assert.equal(checkSend({ phone: KZ2, text: 'a', store, cfg, now: TUE_11_ASTANA }).limit, 3);
    store.linkedAt = TUE_11_ASTANA.getTime() - 10 * 86400e3;
    assert.equal(checkSend({ phone: KZ2, text: 'a', store, cfg, now: TUE_11_ASTANA }).ok, true);
    assert.equal(checkSend({ phone: KZ2, text: 'a', store, cfg: cfgWith({ warmup: false }), now: TUE_11_ASTANA }).ok, true);
  });
  test('new chats pause when almost nobody answers (20+ chats, under 10%)', () => {
    const t = TUE_11_ASTANA.getTime() / 1000 - 10 * 86400;
    for (let i = 0; i < 20; i++) out(`7701100${String(i).padStart(4, '0')}`, t);
    const cfg = cfgWith({ dailyNewChats: 99 });
    const v = checkSend({ phone: KZ2, text: 'a', store, cfg, now: TUE_11_ASTANA });
    assert.deepEqual([v.reason, v.newChats, v.answered], ['low_reply_rate', 20, 0]);
    // two answers out of twenty = 10%: allowed again
    for (const i of [0, 1]) {
      store.addIncoming({ idMessage: `a${i}`, timestamp: t + 60, typeMessage: 'textMessage',
        chatId: `7701100${String(i).padStart(4, '0')}@c.us`, textMessage: 'да' });
    }
    assert.equal(checkSend({ phone: KZ2, text: 'a', store, cfg, now: TUE_11_ASTANA }).ok, true);
    // replies to people who already wrote are never paused
    assert.equal(checkSend({ phone: '77011000005', text: 'b', store, cfg, now: TUE_11_ASTANA }).ok, true);
  });
  test('stop list and off hours', () => {
    store.addStop(KZ);
    assert.equal(checkSend({ phone: KZ, text: 'a', store, cfg: cfgWith(), now: TUE_11_ASTANA }).status, 403);
    assert.equal(checkSend({ phone: KZ2, text: 'a', store, cfg: cfgWith(), now: new Date('2026-09-29T16:00:00Z') }).status, 425);
    assert.equal(checkSend({ phone: KZ2, text: 'a', store, cfg: cfgWith({ enforceHours: false }), now: new Date('2026-09-29T16:00:00Z') }).ok, true);
  });
  test('incoming opt-out lands on the stop list and survives a restart', async () => {
    const dir = tmp();
    const s1 = await newStore(dir);
    s1.allow([KZ]);
    s1.addIncoming({ idMessage: 'm1', timestamp: 1, typeMessage: 'textMessage', chatId: `${KZ}@c.us`, textMessage: 'не пишите больше' });
    await s1.flush();
    assert.ok((await newStore(dir)).stop.has(KZ));
  });
  test('the same incoming message is stored once', () => {
    const g = { idMessage: 'm1', timestamp: 1, typeMessage: 'textMessage', chatId: `${KZ}@c.us`, textMessage: 'x' };
    store.allow([KZ]);
    assert.equal(store.addIncoming(g), true);
    assert.equal(store.addIncoming(g), false);
  });
  test('listens on all interfaces only on Render or when asked', () => {
    assert.equal(loadConfig({ WAGATE_TOKEN: TOKEN }).host, '127.0.0.1');
    assert.equal(loadConfig({ WAGATE_TOKEN: TOKEN, RENDER: 'true' }).host, '0.0.0.0');
    assert.equal(loadConfig({ WAGATE_TOKEN: TOKEN, PAIR_PHONE: '+7 701 123-45-67' }).pairPhone, KZ);
  });
  test('weak token is refused at start', () => {
    assert.throws(() => loadConfig({ WAGATE_TOKEN: 'short' }), /WAGATE_TOKEN/);
  });
});

describe('privacy: only leads are read', () => {
  const msg = (phone, id, text = 'личное') => ({ type: 'incoming', idMessage: id, timestamp: 1, typeMessage: 'textMessage',
    chatId: `${phone}@c.us`, textMessage: text });

  test('a personal chat is dropped: not kept, not queued, not written anywhere', async () => {
    const dir = tmp();
    const s = await newStore(dir);
    assert.equal(s.addIncoming(msg(KZ2, 'p1')), false);
    assert.deepEqual([s.incoming, s.queue], [[], []]);
    await s.flush();
    assert.equal(fs.existsSync(path.join(dir, 'incoming.jsonl')), false);
    // even «не пишите» from a personal chat does not touch the stop list
    s.addIncoming(msg(KZ2, 'p2', 'не пишите'));
    assert.equal(s.stop.size, 0);
  });
  test('a number becomes a lead when the gateway writes to it or the agent registers it', async () => {
    const dir = tmp();
    const s = await newStore(dir);
    s.addOutgoing({ type: 'outgoing', idMessage: 'o1', timestamp: 1, typeMessage: 'textMessage', chatId: `${KZ}@c.us`, textMessage: 'hi' });
    assert.equal(s.addIncoming(msg(KZ, 'l1', 'Здравствуйте')), true);
    assert.equal(s.allow(['+7 (701) 987-65-43', 'мусор', KZ2]), 1);   // duplicates and junk are skipped
    assert.equal(s.addIncoming(msg(KZ2, 'l2')), true);
    await s.flush();
    const again = await newStore(dir);
    assert.ok(again.isLead(KZ) && again.isLead(KZ2), 'allow list survives a restart');
    assert.equal(again.isLead(RU), false);
  });
  test('an early reply from a lead messaged by hand is kept in memory until the lead is registered', async () => {
    const dir = tmp();
    const s = await newStore(dir);
    const t0 = Date.now();
    assert.equal(s.addIncoming(msg(KZ, 'r1', 'Здравствуйте, интересно'), t0), false);
    assert.deepEqual(s.incoming, []);
    await s.flush();
    assert.equal(fs.existsSync(path.join(dir, 'incoming.jsonl')), false, 'nothing written while unknown');
    s.allow([KZ], t0 + 3 * 3600e3);                       // next poll, 3 hours later
    assert.deepEqual(s.incoming.map((m) => m.idMessage), ['r1']);
    assert.equal(s.queue.length, 1);
  });
  test('messages from numbers that never become leads are forgotten after 48 hours', async () => {
    const s = await newStore();
    const t0 = Date.now();
    s.addIncoming(msg(KZ2, 'p1'), t0);
    s.allow([RU], t0 + 49 * 3600e3);                      // any later poll prunes expired ones
    assert.equal(s.held.size, 0);
    s.allow([KZ2], t0 + 50 * 3600e3);                     // too late: it was never kept
    assert.deepEqual(s.incoming, []);
  });
  test('a lead registered by hand is not a new chat (no daily limit for the follow-up)', async () => {
    const s = await newStore();
    s.allow([KZ]);
    assert.equal(checkSend({ phone: KZ, text: 'второе касание', store: s, cfg: cfgWith({ dailyNewChats: 0 }), now: TUE_11_ASTANA }).ok, true);
  });
});

describe('HTTP API (Green-API compatible)', () => {
  let server, base, wa, store, clock;
  before(async () => {
    wa = {
      _state: 'authorized', sent: [], known: new Set([KZ, KZ2]),
      state() { return this._state; }, qr: () => 'QRBASE64', pairingCode: async () => 'ABCD-1234',
      exists: async (p) => wa.known.has(p),
      send: async (p, t) => { wa.sent.push([p, t]); return `ID${wa.sent.length}`; },
    };
    store = await newStore();
    clock = TUE_11_ASTANA;
    server = createServer({ cfg: cfgWith(), wa, store, now: () => clock });
    await new Promise((ok) => server.listen(0, '127.0.0.1', ok));
    base = `http://127.0.0.1:${server.address().port}/waInstance1101`;
  });
  after(() => server.close());
  const call = (method, body, token = TOKEN) => fetch(`${base}/${method}/${token}`, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

  test('wrong token', async () => {
    assert.equal((await call('getStateInstance', undefined, 'nope-nope-nope-nope')).status, 401);
  });
  test('state and QR', async () => {
    assert.deepEqual(await (await call('getStateInstance')).json(), { stateInstance: 'authorized' });
    assert.equal((await (await call('qr')).json()).type, 'alreadyLogged');
  });
  test('sendMessage → idMessage, then visible in lastOutgoingMessages', async () => {
    const r = await call('sendMessage', { chatId: `${KZ}@c.us`, message: 'Здравствуйте!' });
    assert.equal(r.status, 200);
    assert.deepEqual(await r.json(), { idMessage: 'ID1' });
    const outList = await (await fetch(`${base}/lastOutgoingMessages/${TOKEN}?minutes=60`)).json();
    assert.equal(outList[0].textMessage, 'Здравствуйте!');
  });
  test('number without WhatsApp is not messaged', async () => {
    const r = await call('sendMessage', { chatId: '77770000000@c.us', message: 'x' });
    assert.equal(r.status, 404);
    assert.equal(wa.sent.filter(([p]) => p === '77770000000').length, 0);
  });
  test('rules come back as HTTP errors with a reason', async () => {
    clock = new Date('2026-09-29T15:00:00Z');   // 20:00 Astana
    const r = await call('sendMessage', { chatId: `${KZ2}@c.us`, message: 'вечер' });
    assert.equal(r.status, 425);
    assert.equal((await r.json()).error, 'off_hours');
    clock = TUE_11_ASTANA;
  });
  test('parallel sends cannot jump the daily limit', async () => {
    const s = await newStore();
    const srv = createServer({ cfg: cfgWith({ dailyNewChats: 1 }), wa, store: s, now: () => TUE_11_ASTANA });
    await new Promise((ok) => srv.listen(0, '127.0.0.1', ok));
    const b = `http://127.0.0.1:${srv.address().port}/waInstance1101/sendMessage/${TOKEN}`;
    const post = (p) => fetch(b, { method: 'POST', body: JSON.stringify({ chatId: `${p}@c.us`, message: 'x' }) });
    const codes = (await Promise.all([post(KZ), post(KZ2)])).map((r) => r.status).sort();
    srv.close();
    assert.deepEqual(codes, [200, 429]);
  });
  test('checkWhatsapp', async () => {
    assert.deepEqual(await (await call('checkWhatsapp', { phoneNumber: Number(KZ2) })).json(), { existsWhatsapp: true });
    assert.deepEqual(await (await call('checkWhatsapp', { phoneNumber: '77770000000' })).json(), { existsWhatsapp: false });
  });
  test('incoming → lastIncomingMessages and the notification queue', async () => {
    store.addIncoming({ type: 'incoming', idMessage: 'IN1', timestamp: Math.floor(TUE_11_ASTANA / 1000), typeMessage: 'textMessage',
      chatId: `${KZ}@c.us`, textMessage: 'Здравствуйте не интересует' });
    const inc = await (await fetch(`${base}/lastIncomingMessages/${TOKEN}?minutes=60`)).json();
    assert.equal(inc[0].textMessage, 'Здравствуйте не интересует');
    const n = await (await call('receiveNotification')).json();
    assert.equal(n.body.typeWebhook, 'incomingMessageReceived');
    const del = await fetch(`${base}/deleteNotification/${TOKEN}/${n.receiptId}`, { method: 'DELETE' });
    assert.deepEqual(await del.json(), { result: true });
    assert.equal(await (await call('receiveNotification')).json(), null);
  });
  test('not linked yet: sending waits, QR is served', async () => {
    wa._state = 'notAuthorized';
    assert.equal((await call('sendMessage', { chatId: `${KZ}@c.us`, message: 'x' })).status, 503);
    assert.deepEqual(await (await call('qr')).json(), { type: 'qrCode', message: 'QRBASE64' });
    assert.equal((await (await call('getAuthorizationCode', { phoneNumber: KZ })).json()).code, 'ABCD-1234');
    wa._state = 'authorized';
  });
  test('wagateAllow registers leads; personal chats never reach lastIncomingMessages', async () => {
    store.addIncoming({ type: 'incoming', idMessage: 'PERS', timestamp: Math.floor(TUE_11_ASTANA / 1000), typeMessage: 'textMessage',
      chatId: '77055550000@c.us', textMessage: 'мама: купи хлеб' });
    const inc = await (await fetch(`${base}/lastIncomingMessages/${TOKEN}?minutes=60`)).json();
    assert.ok(!inc.some((m) => m.idMessage === 'PERS'));
    const r = await call('wagateAllow', { phones: ['77770001122'] });
    assert.equal((await r.json()).added, 1);
    assert.equal((await call('wagateAllow', { phones: 'x' })).status, 400);
  });
  test('bad json and oversized body', async () => {
    const r = await fetch(`${base}/sendMessage/${TOKEN}`, { method: 'POST', body: '{' });
    assert.equal(r.status, 400);
    const big = await call('sendMessage', { chatId: `${KZ}@c.us`, message: 'x'.repeat(70_000) });
    assert.equal(big.status, 413);
  });
});
