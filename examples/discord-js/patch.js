// Redirect a discord.js v14 Client to the dapi-emu local emulator.
//
// Strategy:
//   * REST: pass { rest: { api: 'http://127.0.0.1:8080/api' } } to the Client.
//     discord.js builds endpoints as `${api}/v${version}/<route>`, so the
//     emulator receives the exact paths it implements under /api/v10/*.
//   * Gateway: discord.js calls REST `/gateway/bot` before connecting, and
//     the emulator already returns ws://127.0.0.1:8080/gateway there, so no
//     explicit WS override is needed once REST is pointed at us.
//   * Compression: the emulator speaks plain JSON (send_text), no zlib-stream.
//     discord.js's default compression is already `null`, so we just make sure
//     nothing enables it.
//
// Usage:
//   const { createEmulatorClient } = require('./patch');
//   const client = createEmulatorClient({ intents: [...] });

'use strict';

const { Client, GatewayIntentBits } = require('discord.js');

const EMULATOR_HTTP_API = process.env.DAPI_API_BASE || 'http://127.0.0.1:8080/api';

function createEmulatorClient(overrides = {}) {
  const options = {
    intents: [GatewayIntentBits.Guilds],
    ...overrides,
    rest: {
      api: EMULATOR_HTTP_API,
      ...(overrides.rest || {}),
    },
    ws: {
      // Leave compression null (default). Explicit for clarity.
      compression: null,
      ...(overrides.ws || {}),
    },
  };
  return new Client(options);
}

module.exports = { createEmulatorClient, EMULATOR_HTTP_API };
