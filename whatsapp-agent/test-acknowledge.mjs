import { handleMessage } from './src/agent.js'
import { readFileSync } from 'node:fs'

const env = Object.fromEntries(readFileSync('../.env', 'utf8').split('\n').filter(Boolean).map(l => {
  const i = l.indexOf('='); let v = l.slice(i + 1).replace(/\r$/, '').trim(); if (v.startsWith('"') && v.endsWith('"')) v = v.slice(1, -1); return [l.slice(0, i), v]
}))
process.env.GROQ_API_KEY = env.GROQ_API_KEY
process.env.BOOKING_API_URL = env.BOOKING_API_URL

const phone = '923009999999@s.whatsapp.net'

async function ask(text) {
  console.log('\n=== USER:', text)
  const reply = await handleMessage(phone, text)
  console.log('=== BOT :', reply)
  await new Promise(r => setTimeout(r, 6000))
  return reply
}

await ask('i want to book for saturday')
await ask('2pm')
await ask('Bilal, Toyota Corolla, phone +923009999999')
await ask('is my booking still confirmed?')
console.log('\nDONE')
process.exit(0)
