import { handleMessage } from './src/agent.js'
import { readFileSync } from 'node:fs'

const env = Object.fromEntries(readFileSync('../.env', 'utf8').split('\n').filter(Boolean).map(l => {
  const i = l.indexOf('='); let v = l.slice(i + 1).replace(/\r$/, '').trim(); if (v.startsWith('"') && v.endsWith('"')) v = v.slice(1, -1); return [l.slice(0, i), v]
}))
process.env.GROQ_API_KEY = env.GROQ_API_KEY
process.env.BOOKING_API_URL = env.BOOKING_API_URL

// Second, SEPARATE conversation -- different customer, same slot 42 (14:00 on 2026-08-10)
const phone2 = '923009999999@s.whatsapp.net'

async function ask(phone, text) {
  console.log('\n=== USER:', text)
  const reply = await handleMessage(phone, text)
  console.log('=== BOT :', reply)
  await new Promise(r => setTimeout(r, 6000))
  return reply
}

await ask(phone2, 'Hello, I want to book a wash')
await ask(phone2, 'this Saturday')
await ask(phone2, 'around 2pm')
await ask(phone2, 'my name is Bilal Khan, Toyota Corolla')
await ask(phone2, 'my phone is +923009999999')
console.log('\nDONE')
process.exit(0)
