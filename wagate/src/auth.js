// WhatsApp session (Baileys auth state) kept in a KV store instead of a folder of files,
// so it survives restarts on hosting without a persistent disk.
// Same data and serialization as Baileys' useMultiFileAuthState.
import { BufferJSON, initAuthCreds, proto } from 'baileys';

const CREDS = 'auth:creds';

export async function useKvAuthState(kv) {
  const read = async (key) => {
    const raw = await kv.get(key);
    return raw ? JSON.parse(raw, BufferJSON.reviver) : null;
  };
  const write = (key, value) => kv.set(key, JSON.stringify(value, BufferJSON.replacer));

  const creds = (await read(CREDS)) || initAuthCreds();
  return {
    state: {
      creds,
      keys: {
        get: async (type, ids) => {
          const out = {};
          await Promise.all(ids.map(async (id) => {
            let value = await read(`auth:${type}-${id}`);
            if (type === 'app-state-sync-key' && value) value = proto.Message.AppStateSyncKeyData.fromObject(value);
            out[id] = value;
          }));
          return out;
        },
        set: async (data) => {
          const tasks = [];
          for (const type in data) {
            for (const id in data[type]) {
              const value = data[type][id];
              tasks.push(value ? write(`auth:${type}-${id}`, value) : kv.del(`auth:${type}-${id}`));
            }
          }
          await Promise.all(tasks);
        },
      },
    },
    saveCreds: () => write(CREDS, creds),
    /** After the phone unlinks the device the session is dead: forget creds so a new pairing starts clean. */
    clear: () => kv.del(CREDS),
  };
}
