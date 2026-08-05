import { handleMessage } from './src/agent.js'
import { readFileSync } from 'node:fs'

const env = Object.fromEntries(readFileSync('../.env', 'utf8').split('\n').filter(Boolean).map(l => {
  const i = l.indexOf('='); let v = l.slice(i + 1).replace(/\r$/, '').trim(); if (v.startsWith('"') && v.endsWith('"')) v = v.slice(1, -1); return [l.slice(0, i), v]
}))
process.env.GROQ_API_KEY = env.GROQ_API_KEY
process.env.BOOKING_API_URL = env.BOOKING_API_URL

async function ask(phone, text) {
  console.log(`\n=== USER(${phone.slice(0, 6)}):`, text)
  const reply = await handleMessage(phone, text)
  console.log('=== BOT :', reply)
  return reply
}

// Conversation 1: books 14:00 on 2026-08-10
const p1 = '923000000001@s.whatsapp.net'
console.log('\n########## CONVERSATION 1 ##########')
await ask(p1, 'I want to book a car wash for Saturday')
await ask(p1, '2pm')
await ask(p1, 'Ali Raza, Honda Civic, +923000000001')
console.log('\n########## CONVERSATION 2 (same slot, different customer) ##########')
const p2 = '923000000002@s.whatsapp.net'
await ask(p2, 'Hello, I need a wash too, also Saturday')
await ask(p2, 'around 2pm')
await ask(p2, 'Bilal, Toyota Corolla, +923000000002')
console.log('\nDONE')
process.exit(0)
