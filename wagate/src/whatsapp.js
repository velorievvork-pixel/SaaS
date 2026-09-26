// WhatsApp connection through Baileys (the WhatsApp Web protocol, linked device).
// The phone stays the main device: the gateway is one of its "linked devices".
import makeWASocket, {
  Browsers, DisconnectReason, fetchLatestBaileysVersion, isJidBroadcast, isJidGroup, isJidNewsletter, proto,
} from 'baileys';
import QRCode from 'qrcode';
import { useKvAuthState } from './auth.js';
import { toGreenMessage, toJid } from './core.js';

export class WhatsApp {
  /**
   * onIncoming(greenMessage) is called for each new personal message from another person.
   */
  constructor({ kv, logger, onIncoming, onLinked = () => {}, pairPhone = '' }) {
    this.onLinked = onLinked;
    this.kv = kv;
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
    const { state, saveCreds, clear } = await useKvAuthState(this.kv);
    this.clearSession = clear;
    let version;
    try { ({ version } = await fetchLatestBaileysVersion()); } catch { /* bundled version is fine */ }
    const sock = makeWASocket({
      auth: state,
      version,
      // Baileys at info/debug would log chat ids, including personal ones: warnings and errors only.
      logger: this.logger.child({ module: 'baileys' }, { level: 'warn' }),
      browser: Browsers.ubuntu('Chrome'),
      markOnlineOnConnect: false,          // do not show "online" to everyone all day
      // Privacy: no full history. Only the initial bootstrap is accepted, because Baileys takes the
      // phone <-> LID mappings from it (with every type off it warns of session errors). The chats
      // and texts from it arrive as 'messaging-history.set', which this gateway never listens to,
      // so nothing from personal chats is kept; only the mappings go to the session keys.
      syncFullHistory: false,
      shouldSyncHistoryMessage: ({ syncType }) => syncType === proto.HistorySync.HistorySyncType.INITIAL_BOOTSTRAP,
      shouldIgnoreJid: (jid) => Boolean(isJidGroup(jid) || isJidBroadcast(jid) || isJidNewsletter(jid)),
    });
    this.sock = sock;
    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', async (u) => {
      if (u.qr) {
        this._state = 'notAuthorized';
        this._qr = (await QRCode.toDataURL(u.qr)).split(',')[1];
        this.logger.info('QR обновлён: откройте /waInstance{id}/qr/{token} или используйте код по номеру');
        await this.#autoPair();
      }
      if (u.connection === 'open') {
        this._state = 'authorized';
        this._qr = null;
        this.onLinked();
        this.retries = 0;
        this.logger.info('WhatsApp подключён');
      }
      if (u.connection === 'close') {
        const code = u.lastDisconnect?.error?.output?.statusCode;
        if (code === DisconnectReason.loggedOut) {
          // Unlinked from the phone: the saved session is dead. Forget it and start a fresh pairing.
          this._state = 'notAuthorized';
          this.logger.warn('Сессия отвязана с телефона: начинаю новую привязку (QR или код по номеру).');
          await this.clearSession();
          setTimeout(() => this.start().catch((e) => this.logger.error(e, 'restart failed')), 2000);
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

  // PAIR_PHONE set: ask for a pairing code by itself and print it in the log, so linking works on
  // hosting without a console (Render). A code lives a few minutes; a new one every 3 minutes until linked.
  async #autoPair() {
    if (!this.pairPhone || Date.now() - (this.lastPairAt || 0) < 180_000) return;
    this.lastPairAt = Date.now();
    try {
      const code = await this.sock.requestPairingCode(this.pairPhone);
      this.logger.warn(`КОД ПРИВЯЗКИ для +${this.pairPhone}: ${code}  →  WhatsApp → Связанные устройства → `
        + 'Привязка устройства → «Связать по номеру телефона». Действует несколько минут.');
    } catch (e) {
      this.logger.error(e, 'не удалось получить код привязки');
    }
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
