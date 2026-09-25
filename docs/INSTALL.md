# PowerEngine: installation guide

A complete, from-scratch setup, in order: seven steps, about 45 minutes. Each step ends with a **Check** so
you know it worked before moving on.

> Keep this guide current: any release that adds or changes a setup step
> updates this file in the same pull request.

**Version this guide matches:** 0.5.13 (beta, Passive-only)

---

## Before you start

You need:

| Requirement | Why | Notes |
| --- | --- | --- |
| Home Assistant OS or Supervised | Add-ons (called *Apps* in newer HA) | Container/Core installs need AppDaemon and Mosquitto run separately |
| [HACS](https://hacs.xyz/) | Installs and updates PowerEngine | |
| Admin access to HA | Creating users, add-ons, dashboards | |
| A terminal on the HA machine | Editing AppDaemon's config | The **Advanced SSH & Web Terminal** add-on (Open Web UI) or **Studio Code Server** |

Tested with: Solis hybrid inverter via the SolaX Modbus integration, Fox ESS
batteries, Solcast, myenergi Zappi, EDF (edf_energy) tariff, Axle VPP.

> **Terminal tip:** every command below runs **in HA's terminal** (the add-on),
> not on your own computer. If you see `Unknown command: ha`, you're in the
> wrong terminal.

---

## Step 1: MQTT broker

PowerEngine creates its entities through MQTT.

1. **Settings → Add-ons → Add-on store → Mosquitto broker → Install**, then
   **Start**, and turn on *Start on boot*.
2. **Settings → Devices & services**: accept the discovered **MQTT**
   integration (or add it: *Add integration → MQTT*).

**Check:** *Settings → Devices & services → MQTT* shows as configured.

Skip this step if you already use Mosquitto (e.g. for Zigbee2MQTT).

---

## Step 2: a login for PowerEngine

A dedicated, non-admin HA user that AppDaemon uses to log in to Mosquitto
(Mosquitto accepts HA logins).

1. Your profile → turn on **Advanced mode** (needed to see *Users*).
2. **Settings → People → Users → Add user**
   - Name: `powerengine`
   - A strong password
   - **Can only log in from the local network**: on
   - **Administrator**: off

**Check:** the user appears in the list, without the admin badge.

---

## Step 3: AppDaemon add-on

1. **Settings → Add-ons → Add-on store → AppDaemon → Install**, turn on
   *Start on boot* and *Watchdog*, then **Start** once so it creates its
   config folder.

2. **Store the MQTT login** (replace the placeholder with the password from
   Step 2 before pressing Enter):

   ```bash
   cat >> /addon_configs/a0d7b954_appdaemon/secrets.yaml <<'SECRETS'
   powerengine_mqtt_user: powerengine
   powerengine_mqtt_password: "PASTE-PASSWORD-HERE"
   SECRETS
   ```

3. **Point AppDaemon at the folder HACS installs into.** HACS puts AppDaemon
   apps in `/homeassistant/appdaemon/apps/`, but the add-on reads its own
   folder by default, so it would never see PowerEngine.

   If you already have AppDaemon apps, copy them across first (this copies,
   so the originals stay as a fallback):

   ```bash
   mkdir -p /homeassistant/appdaemon/apps
   cp -rn /addon_configs/a0d7b954_appdaemon/apps/. /homeassistant/appdaemon/apps/
   ```

4. **Edit AppDaemon's config:** `nano /addon_configs/a0d7b954_appdaemon/appdaemon.yaml`

   Add the `app_dir` line directly under `appdaemon:`, and the `MQTT:` block
   under `plugins:` next to `HASS:`. Indentation matters (spaces, not tabs):

   ```yaml
   appdaemon:
     app_dir: /homeassistant/appdaemon/apps
     # ...your existing latitude/longitude/time_zone lines stay here...
     plugins:
       HASS:
         type: hass
         token: !env_var SUPERVISOR_TOKEN
       MQTT:
         type: mqtt
         namespace: mqtt
         client_host: core-mosquitto
         client_port: 1883
         client_user: !secret powerengine_mqtt_user
         client_password: !secret powerengine_mqtt_password
         client_id: appdaemon-powerengine
         client_topics: NONE   # publish only; don't subscribe to every topic
   ```

   Save with **Ctrl+O**, **Enter**, then exit with **Ctrl+X**.

5. **Restart AppDaemon:** `ha apps restart a0d7b954_appdaemon`

**Check:**

```bash
sleep 20; ha apps logs a0d7b954_appdaemon | grep -iE "mqtt|error" | tail -10
```

You should see `Connected to MQTT broker at URL core-mosquitto:1883`, and
your existing apps (if any) starting as before.

**To undo:** remove the `app_dir` line and the `MQTT:` block, then restart.

---

## Step 4: install PowerEngine through HACS

1. **Enable AppDaemon apps in HACS:** *Settings → Devices & services → HACS →
   Configure* → tick the AppDaemon apps option → Submit.
2. **HACS → ⋮ → Custom repositories**, add both:
   - `https://github.com/durkimat/ha-powerengine-controller`, category **AppDaemon**
   - `https://github.com/durkimat/ha-powerengine-card`, category **Dashboard**
3. **Download both, at the same version.** While PowerEngine is in beta, turn
   on pre-releases for each repo in HACS, then pick the latest version.
4. Restart AppDaemon: `ha apps restart a0d7b954_appdaemon`
5. Reload the browser page (so the card's resource loads).

**Check the log:**

```bash
sleep 20; ha apps logs a0d7b954_appdaemon | grep -i powerengine | tail -10
```

Expected (before any configuration):

```
PowerEngine 0.5.13 starting (Passive-only build: nothing is controlled)
No config.yaml found (...); running unconfigured.
Inputs: unconfigured; mode unconfigured (...)
Published NN entities under the PowerEngine device
```

**Check the device:** *Settings → Devices & services → MQTT → PowerEngine*:

| Entity | Expected |
| --- | --- |
| Version | matches what you installed |
| Heartbeat | a time that moves on every minute |
| Config OK | Off (until configured) |
| Operation mode / Configured mode | unconfigured |
| Input mapping | unconfigured |
| Input catalogue / Settings catalogue | the number of inputs and settings PowerEngine knows about |

---

## Step 5: the PowerEngine dashboard

PowerEngine ships its own dashboard and keeps it up to date: on every start it
writes `/homeassistant/powerengine/dashboard.yaml`. You register it with HA once.

1. **Install two dashboard cards from HACS** (search each, then Download):
   - **Power Flow Card Plus**: the energy flow picture (Monitoring tab)
   - **ApexCharts Card**: the plan chart (Plan tab) and the daily cost chart (Costs tab)

   Reload the browser afterwards.
2. **Register the dashboard** in `configuration.yaml`. If your
   `configuration.yaml` has **no** `lovelace:` section yet, paste this into the
   HA terminal (it appends to the end of the file):

   ```bash
   cat >> /homeassistant/configuration.yaml <<'EOF'

   # PowerEngine dashboard (managed by the PowerEngine app)
   lovelace:
     dashboards:
       powerengine-dash:
         mode: yaml
         title: Power Engine
         icon: mdi:lightning-bolt
         show_in_sidebar: true
         filename: powerengine/dashboard.yaml
   EOF
   ```

   If you **already** have a `lovelace:` section, edit the file instead
   (`nano /homeassistant/configuration.yaml`) and add just the
   `powerengine-dash:` block under its `dashboards:`.

   If you keep a local git copy of your config, pull it afterwards so your next
   push doesn't overwrite this change.

3. **Check and restart Home Assistant** (only needed the first time):

   ```bash
   ha core check && ha core restart
   ```

   When HA is back, restart AppDaemon: `ha apps restart a0d7b954_appdaemon`

**Check:** *Power Engine* appears in the sidebar with five tabs: **Monitoring** (it says *UNCONFIGURED*
until Step 6), **Plan**, **Costs**, **Health** and **Config** (the configuration card, with suggested entities
pre-filled).

Anyone can open the Config tab, but only admins can save; other users see it
read-only.

- The dashboard is managed: edits to `dashboard.yaml` are overwritten on the
  next update. To customise, copy the cards into a dashboard of your own.

---

## Step 6: configure PowerEngine

Open the PowerEngine dashboard's **Config** tab (you must be an admin to save). The page is split into
collapsible sections: *Expand all* opens everything, and a section that needs attention opens by itself and
shows how many items to check. Work through it top to bottom:

1. **Operation and features**
   - *Operation*: leave on **Passive** (monitor and simulate only; nothing is controlled).
   - *Features*: tick what you use. *Automatic cheap threshold* and *Top up when cheap* are on by default.
     *Smart-charge optimisation* only records what it would ask EDF for until Active mode. Before turning on
     *Energy arbitrage*, check your export tariff allows exporting energy bought from the grid (in Passive mode it
     only plans and simulates). Inputs only needed by a feature you've switched off become optional.
2. **Notifications (optional):** pick your phone's notify service (from the HA companion app, usually
   `notify.mobile_app_<phone name>`) and tick what you want to hear about. Nothing is sent until a service is
   chosen.
3. **Settings** (four sections; the defaults are sensible):
   - *Battery and charging*: minimum reserve, cheap-import threshold (with the automatic threshold on, this is
     the most it can be), grid-charge target, charge restart margin.
   - *Supply limits*: **Main supply fuse** (the rating on your supply cutout; default 60 A, the cautious
     choice; update it if the fuse is upgraded), **Car charger power** (7.4 kW for a 32 A Zappi), **Export
     limit** (your DNO-approved limit).
   - *Axle events*: look-ahead and safety margin.
   - *Arbitrage*: **Battery wear cost** (battery price ÷ (capacity × rated cycles); the help text shows an
     example) and the minimum profit per kWh.
4. **Inputs**, one section per group. Unmapped inputs show **Suggested: <entity>** (click to use it), and a
   section with several has **Use all N suggested entities**. For every input:
   - Check the **Now:** value looks right.
   - Signed inputs (battery power, grid power): read the **reads as** text. If it says *charging* when the
     battery is discharging (or *importing* when you're exporting), tick **Invert**.
   - **Battery power must have a sign.** Watch it while the battery charges: if it never goes negative, the
     sensor is unsigned (Solis via SolaX Modbus is). Then map **Battery charging power** and **Battery
     discharging power** (Solis: `sensor.solis_battery_input_energy` / `sensor.solis_battery_output_energy`,
     which despite their names are power in W). The single **Battery power** input then shows *Not used*.
   - *Grid and house*: tick **House load includes the car charger** if the inverter's house load includes
     the car. If unsure, compare *House power* on the Monitoring tab with and without the car charging.
   - *Control outputs* (Solis timed-slot hours/minutes, currents, update button, storage mode, export limit):
     only written in Active mode, but map them now so PowerEngine can count the writes your current setup makes
     (Health tab, EEPROM wear).
   - *Handover guards* (read only): entities that show nothing else is controlling the inverter. With Predbat
     and the legacy automations: **Other controller read-only** = `switch.predbat_set_read_only` (must be on),
     **Other control off (1)/(2)** = `automation.charge_house_battery_on` and
     `automation.house_battery_start_charging` (must be off). Active mode and supervised tests are refused
     until every mapped guard is safe.
   - Fix anything shown in red.
5. **Solar plants:** the main plant (on the hybrid inverter) is pre-filled. Use **+ Add solar plant** for
   extra arrays.
6. **Save.** PowerEngine checks everything, writes `/homeassistant/powerengine/config.yaml` and keeps the
   previous version as `config.yaml.bak-<date>`.

**Check:** the banner says *Saved*, inputs show *PowerEngine check: OK*, and *Operation mode* becomes
**passive**. If it stays *unconfigured*, the reason (on the entity, and at the top of the card after a refresh)
lists the inputs still needing attention.

---

## Step 7: first-day checks

| Tab | What to expect |
| --- | --- |
| **Monitoring** | A status line saying what PowerEngine *would* do and why ("PASSIVE. Would self-use: …"), the energy flow, and an activity list that fills as decisions change. |
| **Plan** | A headline, a 36-hour chart (planned battery %, prices, solar and house-load forecasts, grid charging) and the actions table. The house-load forecast is learned from the last 14 days of history within a few minutes. |
| **Costs** | About 14 days filled from HA history within a few minutes of starting (the log shows `Cost backfill: …` lines), then counted live every half-hour. The *Unexplained* column should be small (tens of pence a day); if it's pounds, check the sensors (see Troubleshooting). |
| **Health** | Findings (if any), battery efficiency and losses, plan vs what happened (from the second day), smart-charge slots, requests to EDF, inverter writes. |

After **14 full days**, the battery's round-trip efficiency and usable capacity are measured and replace the
configured figures (Health tab says *measured*).

### What PowerEngine keeps

All in `/homeassistant/powerengine/` (outside the app folder, so updates never touch it):

| File | What |
| --- | --- |
| `config.yaml` (+ `.bak-<date>`) | Your configuration and backups |
| `dashboard.yaml` | The managed dashboard (rewritten on update) |
| `costs/` | Half-hour energy and cost records (about 20 KB a day, 400 days), plan snapshots, smart-charge slot and request history |
| `load_history.json` | PowerEngine's own house-load record |
| `inverter_writes.json`, `notifications.json`, `ui.json` | Write counts, sent notifications, dashboard defaults |

---

## Active mode

Not available yet: every current build is Passive-only. The design (Solis timed slots, safety rules, handover
from Predbat) is in the spec; this section will describe switching over when it's released. Ready now:

- **Handover guards** (config card, *Handover guards*): Active is refused while any mapped guard is unsafe (e.g.
  Predbat not read-only, or a legacy automation on), and if one trips while PowerEngine is in control it stops
  writing at once and notifies you (it writes nothing more, since something else has taken over).
- **Pause control** (Monitoring tab, top right): in Active mode, pausing returns the inverter to Self-Use once
  (both windows closed) and then makes no changes until you resume. Choosing Passive does the same.
- **Supervised inverter test** (Config tab, below the config card, admins only). **This writes to the
  inverter**, in any mode, when you start it. Hand control over first (Predbat read-only, legacy automations
  off), pick Hold, Grid charge, Force discharge or Self-Use for 1 to 10 minutes, tick the box and start. It
  writes the settings, reads them back after 10 seconds, records battery power and SoC each minute, then returns
  the inverter to Self-Use and reads that back. *Stop and revert* ends it early. The window it sets ends two
  minutes after the test, so a restart mid-test can't leave it running. Result: `sensor.pe_diag_test_write`
  (passed / failed / stopped / refused, with every step). Hand back to Predbat or legacy afterwards.

---

## Updating

The dashboard updates itself with the app; refresh the browser after updating.

1. HACS shows updates under *Settings → Updates* (betas only if pre-releases are on).
2. Update **both** repos to the **same** version. The card warns if they differ.
3. Restart AppDaemon, then reload the browser.
4. Read the release notes' **Behaviour changes** first: they list anything that changes what PowerEngine does.

Your settings (`/homeassistant/powerengine/config.yaml`) live outside the app
folder, so updates never touch them.

## Rolling back

HACS → the repo → ⋮ → **Redownload** → pick the previous version. Do the same
for the card, then restart AppDaemon.

## Uninstalling

1. Remove PowerEngine's entities: add `remove_entities: true` to
   `/homeassistant/powerengine/config.yaml` (create the file with just that
   line if it doesn't exist), restart AppDaemon, and wait for the log to say
   the entities were removed.
2. HACS: remove both repos.
3. Optional: remove the `powerengine-dash:` entry from `configuration.yaml`, delete `/homeassistant/powerengine/`, the
   `powerengine` HA user, and the `MQTT:` block in `appdaemon.yaml`. Keep
   `app_dir` if other AppDaemon apps now live in `/homeassistant/appdaemon/apps/`.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| No `PowerEngine` lines in the AppDaemon log | AppDaemon isn't reading the HACS folder | Step 3.3–3.4 (`app_dir`) |
| `MQTT plugin not configured` in the log | No `MQTT:` block, or wrong indentation | Step 3.4 |
| MQTT connection refused / not authorised | Wrong login in `secrets.yaml`, or the user is limited wrongly | Step 2 and 3.2; check the Mosquitto add-on log |
| PowerEngine entities show *Unavailable* | App stopped or AppDaemon restarting | Check the AppDaemon log; the Heartbeat entity expires after 5 minutes without an update |
| Card says *app not detected* | App not running, or MQTT not set up | Steps 3–4 |
| Card warns about a version mismatch | App and card on different versions | Update both to the same version |
| `Unknown command: ha` | Commands run on your own computer | Use HA's terminal add-on |
| `Config problem: ...` in the log | `config.yaml` has an error | The message names the problem; fix or restore the backup |
| Dashboard missing from the sidebar | `lovelace:` entry not added, or HA not restarted | Step 5 |
| Energy flow or plan chart says *Custom element doesn't exist* | Power Flow Card Plus or ApexCharts Card not installed | Step 5.1, then reload the browser |
| Plan says *house load learned from 0 days* | No history for the house-load input yet (new install, or recorder excludes it) | Wait a day; check the recorder keeps the house-load entity |
| PowerEngine missing from HACS (repos disappeared) but still running | HACS lost its record of the custom repositories | Re-add both under *HACS → ⋮ → Custom repositories* and download the latest version; settings and dashboard are unaffected |
| Dashboard values are *unknown* | Inputs not configured, or mode *unconfigured* | Step 6 |
| Save says *could not send* | You're not an admin | Log in as an admin user |
| Save says *no reply from PowerEngine* | App not running, or can't write its folder | Check the AppDaemon log for the reason |
| Mode stays *unconfigured* after saving | Required inputs missing or failing checks | See the reason on *Operation mode*; fix the inputs flagged on the card |
| Costs tab empty after a few minutes | The backfill couldn't read history (recorder excludes an input, or a sensor was renamed) | Check the AppDaemon log for `Cost backfill` lines and warnings |
| Health: *Battery power never shows charging* | Unsigned battery power sensor | Step 6.4: map the charging/discharging pair |
| Costs: *Unexplained* is pounds a day | A sensor is wrong or missing (house load, grid, battery, solar) | Compare the *Energy by day* table with the inverter's daily counters |
| Health: many inverter writes a day | Your current controller (e.g. Predbat) writes often | See the EEPROM section on the Health tab |
