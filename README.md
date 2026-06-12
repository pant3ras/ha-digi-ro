![Digi Romania](https://raw.githubusercontent.com/pant3ras/ha-digi-ro/main/logo.png)

# Digi Romania for Home Assistant

A custom integration that pulls your **Digi** (RCS & RDS) invoice data from
**My Digi** (`digi.ro/my-account`) into Home Assistant: your latest invoice, its
amount and due date, and anything left to pay — for every address on your account.

> Unofficial. Not affiliated with or endorsed by Digi / RCS & RDS. It reads the
> same My Account pages your browser sees, signed in with your own Digi account.

## What you get

**Per address** (one device each):
- **Last invoice amount** (RON) — with invoice number, description, status, the
  **PDF download link**, and the per-service breakdown as attributes.
- **Last invoice date** and **Last invoice due date**.
- **Unpaid total** (RON) — what's still to pay, with the list of unpaid invoices
  (amount, due date, PDF link) as attributes.
- **Unpaid invoices** — how many invoices are still open.

## Authentication — sign in inside Home Assistant

You log in **directly in Home Assistant**, no browser or cookie copying. When you
add the integration:

1. **Credentials** — enter the e-mail (or client ID) and password you use at digi.ro.
2. **Verification** — Digi sends a 2-step code (SMS or e-mail); pick the method.
3. **Enter the code** — type it in. If your account has more than one address,
   choose which one to set up. Done.

After that first sign-in the integration keeps its **own session** and refreshes it
silently — no SMS every time. Digi's session is long-lived (~90 days); when it does
finally expire, Home Assistant prompts you to sign in again (same quick flow).

Your password is stored in Home Assistant's config entry (like any integration
credential) and is only used to sign in.

## Installation

### Via HACS (custom repository)
1. HACS → ⋮ → **Custom repositories**.
2. Add this repo URL, category **Integration**.
3. Install **Digi Romania**, then restart Home Assistant.

### Manual
Copy `custom_components/digi_ro/` into your HA `config/custom_components/` folder and
restart Home Assistant.

Then: **Settings → Devices & Services → Add Integration → Digi Romania**.

## Notes & limitations
- Polls every 12 hours. Invoices change at most monthly, so there's no need to poll
  more often.
- Digi has **no public API** — the integration parses the My Account invoice pages.
  If Digi restructures those pages, parsing may need an update.
- Amounts are in RON, as Digi returns them.
- Read-only. The integration never performs account actions, payments, or changes.

## Credits

Brought to you by **PanTeraS**.

If you find this useful, you can [buy me a coffee ☕](https://www.buymeacoffee.com/panteras).
