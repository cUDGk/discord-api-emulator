// Minimal discord.js v14 bot that connects to the dapi-emu emulator.
//
//   node bot.js <TOKEN>
//
// Exits 0 when `ready` fires, non-zero on timeout / error.

'use strict';

const { Events, GatewayIntentBits } = require('discord.js');
const { createEmulatorClient, EMULATOR_HTTP_API } = require('./patch');

const token = process.argv[2] || process.env.DAPI_BOT_TOKEN;
if (!token) {
  console.error('usage: node bot.js <TOKEN>   (or set DAPI_BOT_TOKEN)');
  process.exit(2);
}

const client = createEmulatorClient({
  intents: [GatewayIntentBits.Guilds, GatewayIntentBits.GuildMessages],
});

client.on(Events.Debug, (m) => console.log('[debug]', m));
client.on(Events.Warn, (m) => console.warn('[warn]', m));
client.on(Events.Error, (e) => console.error('[error]', e));

client.once(Events.ClientReady, (c) => {
  console.log('Logged in as', c.user.tag);
  // Give the gateway a moment to settle then exit cleanly.
  setTimeout(() => {
    client.destroy().finally(() => process.exit(0));
  }, 500);
});

console.log('REST api base =', EMULATOR_HTTP_API);
client.login(token).catch((err) => {
  console.error('login failed:', err);
  process.exit(1);
});
