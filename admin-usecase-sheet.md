# Telegram Order Bot — Quick Use Case Sheet (Admin Access)

For: 3 admin users with Telegram IDs in ADMIN_IDS whitelist.
Every user listed passes the `is_admin()` check and has full access.

---

## Before You Start

- Send `/start` or `/menu` → you should see the keyboard (if you see `⛔ Access Denied`, your Telegram user ID is not in ADMIN_IDS — ask Nick to add it).
- Bot responds to **commands** and the **menu keyboard**.

---

## Use Case 1 — Take a New Order (`/neworder`)

1. `/neworder` → 👤 Type customer's **Name** → Send
2. 📞 **Contact / Phone** → Send
3. 📦 **Items** → Send
4. 💰 **Price** → Send (e.g. `150`)
5. 💳 **Deposit** → tap `✅ Paid (Yes)` or `⏳ Unpaid (No)`
6. 📍 **Area / Location** → Send
7. 📋 **ORDER SUMMARY REVIEW** → tap `✅ Confirm Order`
8. ✅ Bot logs to Google Sheet `Orders` and broadcasts to tracking channel.

Cancel anytime: `/cancel` (stops the conversation) or tap `❌ Cancel` at confirmation.

---

## Use Case 2 — Look Up an Order (`/order [#]`)

- `/order 023` (or `/order #023`) → shows full order details + action buttons.
- Buttons: `✏️ Update Status`, `💳 Toggle Deposit`, `❌ Cancel Order`.

---

## Use Case 3 — List Pending Orders (`/pending` or Menu `📋 Pending Orders`)

- Shows orders with status = `Confirmed`.
- Used before creating batches.

---

## Use Case 4 — Aggregate Stats (`/summary` or Menu `📊 Summary`)

- Total orders, pending deposit, status counts, value in progress.
- Confirm it shows `0` after a fresh data wipe.

---

## Use Case 5 — Create a Batch (`/newbatch`)

1. `/newbatch` → 📅 **Ship Date** (e.g. `Aug 15`)
2. 🏷️ **HAWB / Tracking number** → Send
3. Bot lists pending orders → reply with order numbers, comma-separated (e.g. `023,025`)
4. Batch created; included orders marked `Shipped`; broadcast sent.

---

## Use Case 6 — List / Check Batches (`/batches` or `/batchstatus` or Menu `📦 Batch Status`)

- `/batches` → full list of all batches + status counts.
- `/batchstatus 001` → single batch detail with update buttons.
- `/batchstatus 001 Completed` → updates directly (allowed: `Departed SG / In Transit / Arrived Myanmar / Out for Delivery / Completed`).
- Menu button `📦 Batch Status` → shows the batch list when tapped.

---

## Use Case 7 — Update / Cancel / Toggle (Admin-Only Commands)

| Command | Example | What it does |
|---|---|---|
| `/updatestatus` | `/updatestatus 023 Shipped` | Updates status; broadcasts update |
| `/editdeposit` | `/editdeposit 023 yes` | Marks deposit paid; broadcasts |
| `/cancelorder` | `/cancelorder 023 Customer changed mind` | Marks `Cancelled` + records reason |

---

## Use Case 8 — Get Your Telegram User ID (`/id`)

- Send `/id` → bot replies with your user ID number.
- If the bot says `⛔ Access Denied` on `/start`, send your `/id` result to Nick to add to ADMIN_IDS.

---

## Troubleshooting

- `⛔ Access Denied` → not in ADMIN_IDS list.
- No reply → check bot is active: Nick runs `systemctl status telegram-order-bot`.
- Sheets not updating → check `.env` file; bot reads `service_account.json` directly.
- Any unhandled error → bot logs to `journalctl -u telegram-order-bot -f`; Nick checks logs.

---

Built: Aug 10 2026 | Bot: `telegram-order-bot.service` on this GCP VM (2 vCPU, 7.8GB RAM)
