import makeWASocket, { useMultiFileAuthState, DisconnectReason, fetchLatestWaWebVersion } from '@whiskeysockets/baileys'
import qrcode from 'qrcode-terminal'
import { handleMessage } from './agent.js'

// Why useMultiFileAuthState: it persists the login session to disk so you
// don't have to re-scan the QR code every time you restart the process —
// important once this is a long-running deployed service, not just a script.
//
// Why fetchLatestWaWebVersion: Baileys ships a hardcoded WhatsApp client
// revision (lib/Defaults). When WhatsApp bumps its protocol revision, the
// server rejects stale clients with `CB:failure reason="405"` (client_too_old).
// Fetching the live revision from web.whatsapp.com/sw.js and passing it to
// makeWASocket keeps the connection on a version the server accepts.

const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

let sock: any = null
let reconnectTimer: NodeJS.Timeout | null = null

async function startBot() {
  const { state, saveCreds } = await useMultiFileAuthState('auth_state')
  const { version, isLatest } = await fetchLatestWaWebVersion()
  if (!isLatest) {
    console.log('WARNING: could not fetch live WhatsApp version, using Baileys default')
  } else {
    console.log('Using WhatsApp version:', version.join('.'))
  }
  
  sock = makeWASocket({
    auth: state,
    version,
    syncFullHistory: false,
    markOnlineOnConnect: false
  })

  sock.ev.on('creds.update', saveCreds)

  sock.ev.on('connection.update', async (update: any) => {
    const { qr, connection, lastDisconnect } = update

    if (qr) {
      console.log('\nScan this QR code with your spare WhatsApp number:')
      console.log('WhatsApp -> Settings -> Linked Devices -> Link a Device')
      qrcode.generate(qr, { small: true })
    }

    if (connection === 'close') {
      const statusCode = (lastDisconnect?.error as any)?.output?.statusCode
      const shouldReconnect = statusCode !== DisconnectReason.loggedOut
      
      console.log('Connection closed, statusCode =', statusCode, '-> reconnecting =', shouldReconnect)
      
      if (statusCode === 440 || (lastDisconnect?.error as any)?.message?.includes('conflict')) {
        console.error('⚠️ [CONFLICT DETECTED]: Another WhatsApp bot instance is currently active with this number!')
        console.error('Please terminate the other running bot instance to prevent multiple connections from fighting and getting banned.')
      }

      if (shouldReconnect) {
        // Slow down reconnect on conflict/stream errors to avoid spamming the server
        const reconnectTimeout = statusCode === 440 ? 8000 : 3000
        console.log(`Reconnecting in ${reconnectTimeout}ms...`)
        clearTimeout(reconnectTimer!)
        reconnectTimer = setTimeout(startBot, reconnectTimeout)
      } else {
        console.log('Logged out. Delete the auth_state folder and restart to re-scan the QR.')
      }
    }

    if (connection === 'open') {
      console.log('Connected to WhatsApp.')
    }
  })

  sock.ev.on('messages.upsert', async ({ messages, type }: any) => {
    console.log(`[messages.upsert] Event triggered. Type: ${type}, Message count: ${messages.length}`)
    
    for (const msg of messages) {
      console.log(`[messages.upsert] Processing message: fromMe = ${msg.key.fromMe}, remoteJid = ${msg.key.remoteJid}`)
      console.log('[messages.upsert] Raw message payload:', JSON.stringify(msg.message, null, 2))

      if (msg.key.fromMe) {
        console.log('[messages.upsert] Skipped: message is from me.')
        continue
      }

      // Message text lives in different places depending on the client
      const text = msg.message?.conversation || msg.message?.extendedTextMessage?.text
      if (!text) {
        console.log('[messages.upsert] Skipped: no text payload extracted (conversation or extendedTextMessage).')
        continue
      }

      if (!msg.key.remoteJid) {
        console.log('[messages.upsert] Skipped: msg.key.remoteJid is undefined.')
        continue
      }

      // Only handle private chats (1-on-1), not groups (groups check with @g.us)
      if (!msg.key.remoteJid.endsWith('@s.whatsapp.net') && !msg.key.remoteJid.endsWith('@lid')) {
        console.log(`[messages.upsert] Skipped: remoteJid is not a private chat: ${msg.key.remoteJid}`)
        continue
      }

      console.log('Received text from', msg.key.remoteJid, ':', text)
      
      try {
        // Humanize: Show typing status, wait 1.5 seconds, then respond
        await sock.sendPresenceUpdate('composing', msg.key.remoteJid)
        await delay(1500)
        
        const reply = await handleMessage(msg.key.remoteJid, text, { channel: 'whatsapp' })
        console.log('Reply:', reply)
        
        await sock.sendPresenceUpdate('paused', msg.key.remoteJid)
        await sock.sendMessage(msg.key.remoteJid, { text: reply })
      } catch (err) {
        console.error('Error handling message:', err)
        await sock.sendPresenceUpdate('paused', msg.key.remoteJid)
        await sock.sendMessage(msg.key.remoteJid, {
          text: 'Sorry, something went wrong on my side. Please try again in a moment.',
        })
      }
    }
  })
}

// Graceful shutdown: Cleanly close websocket on termination to prevent "stream conflict / 440" stale connections on restart
const shutdown = () => {
  console.log('Shutdown signal received. Cleaning up connection...')
  if (reconnectTimer) clearTimeout(reconnectTimer)
  if (sock) {
    sock.ev.removeAllListeners('connection.update')
    sock.ws.close()
  }
  process.exit(0)
}

process.on('SIGINT', shutdown)
process.on('SIGTERM', shutdown)

startBot()
