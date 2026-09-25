// Offline tests: a fake WhatsApp stands in for Baileys. Run: npm test
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { after, before, beforeEach, describe, test } from 'node:test';
import {
  Store, checkSend, digits, inWorkingHours, isOptOut, loadConfig, phoneFromChatId, toGreenMessage, utcOffset,
} from '../src/core.js';
import { createServer } from '../src/server.js';

const TOKEN = 'test-token-0123456789';
// Tuesday 2026-09-29 06:00 UTC = 11:00 Astana (+5), 09:00 Moscow (+3)
const TUE_11_ASTANA = new Date('2026-09-29T06:00:00Z');
const KZ = '77011234567', KZ2 = '77019876543', RU = '79161234567';

const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), 'wagate-'));
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
  beforeEach(() => { store = new Store(tmp()); });
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
  test('same text to the same number within a day is refused', () => {
    out(KZ, TUE_11_ASTANA.getTime() / 1000 - 3600);
    assert.equal(checkSend({ phone: KZ, text: 'hi', store, cfg: cfgWith(), now: TUE_11_ASTANA }).reason, 'duplicate_24h');
  });
  test('stop list and off hours', () => {
    store.addStop(KZ);
    assert.equal(checkSend({ phone: KZ, text: 'a', store, cfg: cfgWith(), now: TUE_11_ASTANA }).status, 403);
    assert.equal(checkSend({ phone: KZ2, text: 'a', store, cfg: cfgWith(), now: new Date('2026-09-29T16:00:00Z') }).status, 425);
    assert.equal(checkSend({ phone: KZ2, text: 'a', store, cfg: cfgWith({ enforceHours: false }), now: new Date('2026-09-29T16:00:00Z') }).ok, true);
  });
  test('incoming opt-out lands on the stop list and survives a restart', () => {
    const dir = tmp();
    const s1 = new Store(dir);
    s1.addIncoming({ idMessage: 'm1', timestamp: 1, typeMessage: 'textMessage', chatId: `${KZ}@c.us`, textMessage: 'не пишите больше' });
    assert.ok(new Store(dir).stop.has(KZ));
  });
  test('the same incoming message is stored once', () => {
    const g = { idMessage: 'm1', timestamp: 1, typeMessage: 'textMessage', chatId: `${KZ}@c.us`, textMessage: 'x' };
    assert.equal(store.addIncoming(g), true);
    assert.equal(store.addIncoming(g), false);
  });
  test('weak token is refused at start', () => {
    assert.throws(() => loadConfig({ WAGATE_TOKEN: 'short' }), /WAGATE_TOKEN/);
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
    store = new Store(tmp());
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
    const s = new Store(tmp());
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
  test('bad json and oversized body', async () => {
    const r = await fetch(`${base}/sendMessage/${TOKEN}`, { method: 'POST', body: '{' });
    assert.equal(r.status, 400);
    const big = await call('sendMessage', { chatId: `${KZ}@c.us`, message: 'x'.repeat(70_000) });
    assert.equal(big.status, 413);
  });
});
