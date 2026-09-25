# PowerEngine: installation guide

A complete, from-scratch setup, in order. Allow about 45 minutes. Each step ends
with a **Check** so you know it worked before moving on.

> Keep this guide current: any release that adds or changes a setup step
> updates this file in the same pull request.

**Version this guide matches:** 0.3.5 (beta, Passive-only)

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
PowerEngine 0.3.5 starting (Passive-only build: nothing is controlled)
No config.yaml found (...); running unconfigured.
Inputs: unconfigured; mode unconfigured (...)
Published 28 entities under the PowerEngine device
```

**Check the device:** *Settings → Devices & services → MQTT → PowerEngine*:

| Entity | Expected |
| --- | --- |
| Version | matches what you installed |
| Heartbeat | a time that moves on every minute |
| Config OK | Off (until configured) |
| Operation mode / Configured mode | unconfigured |
| Input mapping | unconfigured |
| Input catalogue | the number of inputs PowerEngine knows about |

---

## Step 5: the PowerEngine dashboard

PowerEngine ships its own dashboard and keeps it up to date: on every start it
writes `/homeassistant/powerengine/dashboard.yaml`. You register it with HA once.

1. **Install two dashboard cards from HACS** (search each, then Download):
   - **Power Flow Card Plus**: the energy flow picture (Monitoring tab)
   - **ApexCharts Card**: the plan chart (Plan tab)

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
         title: PowerEngine
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

**Check:** *PowerEngine* appears in the sidebar with three tabs: **Monitoring**
(it will say *UNCONFIGURED* until Step 6), **Plan**, and **Config**, which opens the
configuration card with suggested entities pre-filled.

Anyone can open the Config tab, but only admins can save; other users see it
read-only.

- The dashboard is managed: edits to `dashboard.yaml` are overwritten on the
  next update. To customise, copy the cards into a dashboard of your own.

> Upgrading from 0.1 or earlier? You can delete the separate *PowerEngine config*
> dashboard you created before: the card now lives on the Config tab.

---

## Step 6: configure PowerEngine

On the PowerEngine dashboard's **Config** tab (you must be an admin to save):

1. **Operation:** leave on **Passive** (monitor and simulate only).
2. **Features:** tick the ones you use (Axle, free-power sessions, and so on).
   Inputs only needed by a feature you've switched off become optional.
3. **Safety and thresholds:** the defaults are sensible; change the cheap-import
   threshold and grid-charge target to suit your tariff.
4. **Grid and house:** tick **House load includes the car charger** if your
   inverter's house load includes the car (car on the same meter/CT). If unsure,
   compare *House power* on the Monitoring tab with and without the car charging.
5. **Inputs:** work down each section. For every input:
   - Pick the entity (suggestions are pre-filled on first use) or enter a fixed value where offered.
   - Check the **Now:** value looks right for that input.
   - For signed inputs (battery power, grid power), read the **reads as** text.
     If it says *charging* when the battery is discharging (or *importing* when
     you're exporting), tick **Invert**.
   - Fix anything shown in red.
6. **Solar plants:** the main plant (on the hybrid inverter) is pre-filled. Use
   **+ Add solar plant** for any extra arrays.
7. **Save.** PowerEngine checks everything, writes
   `/homeassistant/powerengine/config.yaml` and keeps the previous version as a
   backup (`config.yaml.bak-<date>`).

**Check:** the banner says *Saved*, each input shows *PowerEngine check: OK*,
and *Operation mode* becomes **passive**. On the Monitoring tab the status now
starts with what PowerEngine *would* do, e.g. *"PASSIVE. Would self-use:
nothing better to do at 30.28p…"*, and the Activity list fills as decisions change.

The **Plan** tab shows the next 24–48 hours: a one-line headline, a chart
(planned battery %, prices, solar and house-load forecasts, grid charging) and
a table of actions with the reason for each. Within a few seconds of starting,
PowerEngine learns your typical house load from the last 14 days of history.

Signs look wrong (battery shows charging while discharging)? Fix it with
**Invert** on the Config tab, not in the dashboard. If it stays *unconfigured*, its
reason (on the entity, and at the top of the card after a refresh) lists the
inputs still needing attention.

To restore a previous configuration, copy a backup over `config.yaml` and
restart AppDaemon.

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
3. Optional: delete the config dashboard, `/homeassistant/powerengine/`, the
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
