// WhatsApp connection through Baileys (the WhatsApp Web protocol, linked device).
// The phone stays the main device: the gateway is one of its "linked devices".
import path from 'node:path';
import makeWASocket, { Browsers, DisconnectReason, fetchLatestBaileysVersion, useMultiFileAuthState } from 'baileys';
import QRCode from 'qrcode';
import { toGreenMessage, toJid } from './core.js';

export class WhatsApp {
  /**
   * onIncoming(greenMessage) is called for each new personal message from another person.
   */
  constructor({ dataDir, logger, onIncoming, pairPhone = '' }) {
    this.authDir = path.join(dataDir, 'auth');
    this.logger = logger;
    this.onIncoming = onIncoming;
    this.pairPhone = pairPhone;
    this._state = 'starting';
    this._qr = null;
    this.sock = null;
    this.retries = 0;
  }

  state() { return this._state; }
  qr() { return this._qr; }

  async start() {
    const { state, saveCreds } = await useMultiFileAuthState(this.authDir);
    let version;
    try { ({ version } = await fetchLatestBaileysVersion()); } catch { /* bundled version is fine */ }
    const sock = makeWASocket({
      auth: state,
      version,
      logger: this.logger.child({ module: 'baileys' }),
      browser: Browsers.ubuntu('Chrome'),
      markOnlineOnConnect: false,          // do not show "online" to everyone all day
      syncFullHistory: false,
    });
    this.sock = sock;
    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', async (u) => {
      if (u.qr) {
        this._state = 'notAuthorized';
        this._qr = (await QRCode.toDataURL(u.qr)).split(',')[1];
        this.logger.info('QR обновлён: откройте /waInstance{id}/qr/{token} или используйте код по номеру');
      }
      if (u.connection === 'open') {
        this._state = 'authorized';
        this._qr = null;
        this.retries = 0;
        this.logger.info('WhatsApp подключён');
      }
      if (u.connection === 'close') {
        const code = u.lastDisconnect?.error?.output?.statusCode;
        if (code === DisconnectReason.loggedOut) {
          // Unlinked from the phone: the saved session is dead, a new QR is needed.
          this._state = 'notAuthorized';
          this.logger.warn('Сессия отвязана с телефона. Удалите data/auth и привяжите заново.');
          return;
        }
        if (code === DisconnectReason.forbidden) {
          this._state = 'blocked';
          this.logger.error('WhatsApp заблокировал номер (403). Отправка остановлена.');
          return;
        }
        if (code === DisconnectReason.connectionReplaced) {
          // Another copy of the gateway opened the same session; two copies would kick each other forever.
          this._state = 'notAuthorized';
          this.logger.error('Сессию открыл другой экземпляр шлюза. Этот остановлен.');
          return;
        }
        this._state = 'starting';
        const delay = Math.min(60_000, 2_000 * 2 ** this.retries++);
        this.logger.warn({ code }, `Соединение закрыто, переподключение через ${delay / 1000} с`);
        setTimeout(() => this.start().catch((e) => this.logger.error(e, 'reconnect failed')), delay);
      }
    });

    sock.ev.on('messages.upsert', ({ messages, type }) => {
      if (type !== 'notify') return;   // "append" is history sync, not new messages
      for (const msg of messages) {
        const g = toGreenMessage(msg, 'incoming');
        if (g) this.onIncoming(g);
      }
    });
  }

  async pairingCode(phone) {
    if (!this.sock) throw new Error('not started');
    return this.sock.requestPairingCode(phone);
  }

  async exists(phone) {
    const [r] = (await this.sock.onWhatsApp(phone)) || [];
    return Boolean(r?.exists);
  }

  async send(phone, text) {
    const r = await this.sock.sendMessage(toJid(phone), { text });
    return r?.key?.id || '';
  }
}
