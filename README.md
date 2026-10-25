---
title: 25 Martini Order Assistant
emoji: 🍸
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
---

# 🍸 25 Martini Telegram Order Bot (`@mtniassistantbot`)

Autonomous Telegram Order Bot for 25 Martini cross-border personal shopping (Singapore × Yangon).
Connected directly to Notion databases:
- `01_ORDERS_AND_FINANCIAL_LEDGER` (Orders, landed pricing, customer intake)
- `03_CARGO_BATCHES_LOG` (Air cargo flights, HAWB tracking, Peninsula Plaza / Royal Express)

---

## Architecture & Features

- **Built with**: `python-telegram-bot` v20 (asyncio) + Notion REST API
- **Deployment**: Hugging Face Spaces (Free CPU tier, 16GB RAM) or Docker / systemd
- **Port 7860 Health Check**: Lightweight built-in HTTP server keeps the Space alive 24/7
- **Admin Commands**:
  - `/neworder` — 7-step guided intake with MMK/SGD conversion
  - `/pending` — Real-time active orders pipeline
  - `/order [id]` — Look up order card with interactive buttons
  - `/summary` — Gross sales volume, order counts, and stage distribution
  - `/batches` — Air cargo shipment tracking
  - `/newbatch` — Create new cargo batch and mark orders shipped
  - `/batchstatus` — Update transit status (`Departed SG` ➔ `In Transit` ➔ `Arrived Myanmar` ➔ `Completed`)

---

## Environment Variables (Hugging Face Secrets)

Add these in your Hugging Face Space Settings under **Variables and Secrets**:

| Secret Key | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token from `@BotFather` (`8920492353:AAEshB2ImpFi4xUwg7CsZSPu3FMQAE3dEWw`) |
| `NOTION_TOKEN` | Internal Notion API integration token |
| `NOTION_ORDERS_DB_ID` | `404f3e9b-b31c-81dc-973b-c0674e577c6e` |
| `NOTION_BATCHES_DB_ID` | `404f3e9b-b31c-816e-aa59-f17f15ae1015` |
| `ADMIN_IDS` | Comma-separated admin Telegram IDs (`1753678802,8995849513,5065850640,6019728260`) |
| `ORDER_TRACK_CHANNEL_ID` | Telegram tracking group / channel ID |
| `ORDER_TRACK_TOPIC_ID` | Tracking topic ID (`34`) |
| `PORT` | `7860` |
