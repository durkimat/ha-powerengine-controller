# Changelog

Released only when the card changes, on the version of the [PowerEngine app](https://github.com/durkimat/ha-powerengine-controller)
it ships with (one sequence, so card versions can skip numbers). Older entries below were kept in step with the app.

## 0.9.200

### Behaviour changes

- **Events: the event's own pay and the export rate on top are two figures.** The Costs page sentence for today says "Axle exported 3.3 kWh today and paid £3.26, and the export rate adds £0.49". The events table has "Axle pays" and "Export rate on top" columns, and the period line says the same. An older app that doesn't send the split keeps the single "paid you" figure. Needs app 0.9.200.

## 0.9.199

### Behaviour changes

- **Config page: the engine not in use is greyed out and folded away, not hidden.** "Engine v1 settings" (or v2) is still there to open and change; the one in use is open as normal. Engine v1's planning settings still shape its comparison plan and the simulators while engine v2 runs.

## 0.9.198

### Behaviour changes

- **Config page: the Cheap import threshold, Automatic cheap threshold and Grid-charge target now show whichever engine is in use.** The car's smart-charge requests read them on both engines, but they had been placed under Engine v1 settings and so were hidden on engine v2. "Planning and cheap rate" under Engine v1 settings is now just "Planning".

## 0.9.197

### Behaviour changes

- **Config page: only the settings of the engine in use are shown.** "Engine v1 settings" shows when engine v1 is running and "Engine v2 settings" when engine v2 is (the other one is hidden, unless one of its settings has a problem to fix).
- **"Your house" is now three groups:** Your equipment (Battery, Grid and house, Solar, Car, Cold battery, Inverter control), Tariff and events (Tariff and planning, Selling, Grid events, Free-power sessions) and Tools and messages (Costs page extras, Notifications and announcements). Each folds away.
- **Dampening tuning is now part of Inverter control**, under its own heading.
- **Tariff simulator and Engine comparison are one section, "Costs page extras".**
- **Entity publishing moved from "Other" to the Operation section.** The "Other" section is gone unless a new input appears that the card doesn't know yet.

## 0.9.196

### Behaviour changes

- **Config page: "Your house", "Engine v1 settings" and "Engine v2 settings" now fold away.** Click a heading to open or close it; the page remembers which you closed. Searching, or a section with a problem, opens the group it is in.
- **Notifications and announcements now sit inside "Your house"** (when the engine groups are shown).
- **The battery's BMS charge limit and BMS discharge limit moved from "Other" into Battery**, under Optional inputs.

## 0.9.195

### Behaviour changes

- **Config page: Notifications and announcements.** One section replaces Notifications and Alexa announcements: a table of what goes where (tick the channels each kind of message goes to), then each channel (Home Assistant notifications, a phone, Alexa speakers, WhatsApp) with its opening words, the hours it may be used, whether a message outside them is held or skipped, and a button to send it a test message. You can add and remove channels. Below are the grid event reminder times and the last messages sent, and any held. Messages sent from elsewhere with the `pe_notify` event appear in the table once they have been seen. Your current settings show as the same channels; nothing changes until you save. Needs app 0.9.195 or newer (an older app keeps the old sections).

## 0.9.194

### Behaviour changes

- **Costs page, day by day chart: the PowerEngine bar is now bright pink.** It was dark grey and almost invisible against a dark background.

## 0.9.193

### Behaviour changes

- **Config page: announcements to WhatsApp.** Under Alexa announcements, "Also send them to WhatsApp" with the group id(s) or number(s) to send to (separate several with commas). The test announcement button sends to WhatsApp as well. Needs app 0.9.193 or newer (hidden on older apps).

## 0.9.192

### Behaviour changes

- **Config page: switches to run during free power.** Under Free-power sessions, a new "Switches to run during free power" list: pick a switch or input boolean, optionally name it, add or remove rows, then Save. Needs app 0.9.191 or newer (the list is hidden on older apps).

## 0.9.190

### Behaviour changes

- **Config page:** reads the input and settings catalogues from however many sensors the app split them across (`catalogueAttrs`), and waits rather than showing half a list. Needs app 0.9.190 only when a catalogue is split; works with older apps.

## 0.9.189

### Behaviour changes

- **Costs page on a phone:** the Alignment card can show "Expand all / Collapse all" on the same row (`tools: true`), and the Costs period buttons are an even grid instead of ragged wrapping.

## 0.9.188

### Behaviour changes

- **Engines compared:** administrators get "Compare this day again" (in an opened day) and "Try again" (on a skipped day), with the app's answer shown above the table. Needs app 0.9.188.

## 0.9.187

### Behaviour changes

- **New Costs page cards:** `powerengine-costs-period-card` (the period picker and its summary) and `powerengine-costs-days-card` (the chart and table by day). The waterfall, engines compared and events cards take `shared: true` to follow the picker. Needs app 0.9.187.

### Behind the scenes

- `costsRange`, `costsSummary`, `costsBars`, `peParts`, `waterfallSteps`, `compareScoped`, `eventsInRange`; the period is kept in `localStorage` (`powerengine-costs-period`) and announced as a window event.

## 0.9.186

### Behaviour changes

- **Engines compared:** select a compared day for the breakdown of why engine v2 fell short of the best possible. Needs app 0.9.186.
- **New events card** (`custom:powerengine-events-card`): the monthly grid-event table, where a month opens to its days. Needs app 0.9.186.

### Behind the scenes

- `whyView`, `eventsView`.

## 0.9.185

### Behaviour changes

- **New foldable section card** (`custom:powerengine-section-card`) and an Expand all / Collapse all card (`custom:powerengine-section-tools`). Open or closed is remembered per browser, and the cards inside are built the first time a section opens. Used by the Costs page; needs app 0.9.185 for the new page.

### Behind the scenes

- `sectionOpen`, `sectionsLoad`.

## 0.9.184

### Behaviour changes

- **Engines compared:** days that are only approximate (part-measured half-hours scaled up, or a 25-hour day) show a **≈** with the reason on hover.
- **Savings waterfall:** when a period paid you, the last bar reads "You were paid" with the amount shown without a minus.

### Behind the scenes

- `approxNote`, `shownValue`.

## 0.9.183

### Behaviour changes

- **Savings waterfall card: Today and Custom buttons.** Custom shows two date pickers (From, To) over the last 31 days and totals them with the same steps as the app (`waterfallCustom`). An older app shows "needs the newer PowerEngine app" for Custom and nothing yet for Today. Needs app 0.9.183.

### Behind the scenes

- `waterfallCustom` is tested against the figures `pe_core.costs.waterfall` gives for the same days.

## 0.9.182

### Behaviour changes

- **"Announce a summary when a grid event ends"** switch in the Announcements section (on by default). Needs app 0.9.182.

### Behind the scenes

- `alexaDraft` carries `finish`; `MIN_APP_VERSION` is 0.9.182.

## 0.9.179

### Behaviour changes

- **Heating set-back for grid events.** The Grid events section gains the "Turn the heating down before grid events" switch, the "Heating set-back switch" entity and the "Heating pump overrun" setting (15 minutes by default). Needs app 0.9.179.

### Behind the scenes

- `MIN_APP_VERSION` is 0.9.179; the frozen feature texts include the new feature.

## 0.9.176

### Behaviour changes

- **Speaker groups apart from single speakers.** The Announcements speaker list splits into "Single speakers" and "Speaker groups" (Amazon Speaker Group devices, plus Everywhere), with a note that ticking a group and one of its speakers makes that speaker say it twice. If Home Assistant does not give the page its device list, only Everywhere is treated as a group.

### Behind the scenes

- `alexaSpeakerKinds` (tested in `tests/helpers.test.cjs`).

## 0.9.175

### Behaviour changes

- **Announcements section.** The Alexa settings move out of Notifications into their own "Announcements" heading and section, apart from the Engine v1 and Engine v2 settings. The switch, speakers, allowed hours, opening words and hours-before are all there, without the "Alexa:" prefix on each.
- **"Play a test announcement" button** next to the speakers. It plays "Test message" (after your opening words) on the ticked speakers without saving first, and shows what happened or "No reply" after 20 seconds. Admin only.

### Behind the scenes

- `alexaTestPayload` (tested in `tests/helpers.test.cjs`); `MIN_APP_VERSION` is 0.9.175 (the app that answers `pe_alexa_test`).

## 0.9.174

### Behaviour changes

- **Alexa announcements settings.** The Notifications section of the Config page gains the "Alexa announcements" switch, a tick-list of your Alexa Media Player speakers ("Everywhere" is all of them), the earliest time on a weekday and at the weekend, the latest time, the opening words ("Daddybot here." by default), and the hours before a grid event each reminder is said. Saving no longer drops them.

### Behind the scenes

- `alexaDraft`, `parseHoursBefore` and `alexaSpeakerName` (tested in `tests/helpers.test.cjs`); `MIN_APP_VERSION` is 0.9.174 (the app that reads `notifications.alexa`).

## 0.9.170

### Behaviour changes

- The plan and history charts draw a free-power half-hour at its recorded price (0p) and tag it "free power" in the hover, as they already did for the planned part. An older app does not send the flag and the charts look as before.

## 0.9.169

### Behaviour changes

- The Free-power sessions block lists the new **All free sessions** input (see the app notes). An older app does not know it, and the block looks as before.

## 0.9.167

### Behaviour changes

- **The line under a measurable setting shows what is in use**, from the app's new `use` attribute on `sensor.pe_diag_learned` ("In use: 17.02 kWh, measured (you entered 18 kWh)"), replacing "Fixed: 18 kWh". Car charger power, Export limit, Minimum reserve and the Battery's hard floor get the same line, and the engine v2 hard-floor readout shows the floor in use. With an older app the card shows what it showed before.
- Text fixes as in the app notes: "SolaX Modbus" removed from the control and test texts, "Learn: where discharging stops" says which floor it raises.

### Behind the scenes

- `inUseFor`, `nowText`, `readout(…, inUse)`, `floorsReadout(…, inUse)`; tests in `tests/inuse.test.cjs`; `tests/card_texts_before_names.json` updated for the two reworded texts.

## 0.9.166

### Behaviour changes

- **The engine v2 history chart separates its charts more clearly.** Each of the four (battery and modes, prices, grid, sun and house) sits on a lightly shaded background with a darker line along its bottom.
- **The battery scale starts at the battery's own lowest level** (your hard floor, 12% here), not 0%, since it can never go lower. That gives the level line more room. Without a known floor the scale runs 0 to 100% as before.

## 0.9.165

### Behaviour changes

- **The engine v2 history chart no longer draws the dashed blue line for the day's opening plan** (the battery path expected at the first plan after midnight, which stopped at midnight). The plan usually moves for good reasons, so the comparison told you little and the line was hard to read. The solid line (what the battery did) and the dotted line (the forecast from now) stay, and the hover line no longer prints "Expected at the start of the day".

## 0.9.164

### Behaviour changes

- **The engine v2 history chart scrolls sideways, like the expected timeline.** It opens an hour before now, with the hours still to come spread out comfortably, and you scroll left for the rest of the day. The scales stay in view on the left while it moves, and your place is kept when it redraws.
- **The history chart's forecast reaches as far as the expected timeline's: 30 hours on from now.** Both now stop at the same point.
- **A smart slot's price stays dashed after it has run**, on the history chart and on the expected timeline's hours already run, so you can see where the smart slots and the guaranteed slot were. This needs app 0.9.164; days recorded earlier show solid lines.
- The history chart's panel titles stay in view as you scroll.

### Behind the scenes

- `FORECAST_HOURS`, `viewH` on the history view, `slot` on recent rows and the history's import steps. `_whenLaidOut` moved to the shared base card (a box id argument). Tests: `tests/engine_v2.test.cjs` (two new).

## 0.9.161

### Behaviour changes

- **The engine v2 history chart shows its numbers on the chart.** Move over the chart, or touch it, and each value floats where the inspection line meets its line: the battery level (with its likely range ahead), the import price, the value of a stored kWh, the grid and car power, and the sun and house power (with the sun's likely range ahead). The time shows above the chart. The large box underneath is gone, leaving one line with the time, the mode and why.

## 0.9.160

### Behaviour changes

- **The engine v2 history chart shows the sun and the house.** A new panel under the grid bars shows the solar and house power as they happened and, right of the Now line, the expected sun (with its likely range) and the predicted house load. Hovering or tapping gives the values at that time. A tick box in the key hides it.
- **Today now runs on to the end of the engine's 36-hour forecast**, as the plan chart does, with a marker at midnight and the next day named on the axis.
- The past sun and house need app 0.9.160; with an older app only the forecast side shows.

## 0.9.159

### Behaviour changes

- **The engine v2 history chart now looks ahead.** On today it shows what happened up to now, a red Now line, and, dimmed to the right, what the engine expects: modes, battery with its likely range, prices and value. The next arrow steps on to tomorrow's forecast, as far as the engine's 36-hour timeline reaches.
- **Pick a time.** Hover or tap the chart for a cursor and a readout underneath: the mode and why, battery level, import and export price, value, grid and car power.
- **Grid and car bars follow their real timing** (5-minute steps) when the app is 0.9.159 or newer; older apps keep the half-hour bars.

## 0.9.157

### Behaviour changes

- **The engine v2 settings page uses the new range.** The readout under "The battery's range" shows the hours above and below your everyday range. It also shows where a grid event stops. The page refuses a range whose bottom is not below its top. The "comfort band gave up" figure is gone, because the comfort band no longer exists.

## 0.9.153

### Behaviour changes

- **The value map no longer calls every very high value a grid event.** Near the battery's floor the plan prices running out very high, which showed as purple "grid event (off the scale)" even with no event. Those cells are now orange, "off the scale: the battery near its floor", and the hover text says it is not a grid event. Purple stays for cells that a real grid event covers.

## 0.9.152

### Behaviour changes

- **The value line on the expected timeline is smooth.** It uses the value the app publishes along the expected path (needs app 0.9.152). With an older app the card now blends between the hourly rows of the curve instead of jumping on the hour, which removes the hourly jumps but not all of the wobble.
- The price axis no longer stretches for one very large value near the battery's floor.

## 0.9.151

### Behaviour changes

- **A key above the plan chart and the history chart, with a tick box for each line.** Value of a stored kWh, buy line, sell line and re-plans on both; grid energy as well on the history. Untick one to hide it, tick it to bring it back. Your choice is remembered in that browser. The key sits in the header beside the title, so it takes no extra height on a wide screen.
- **The buy and sell lines have colours of their own** (teal and magenta), so they no longer look like the import and export prices.

## 0.9.150

### Behaviour changes

- **The expected timeline shows the value of a stored kWh.** A green line on the price axis: solid over the hours already run, dashed from now on along the expected battery path. Hovering says what it is at that time.
- **Buy and sell lines on the plan chart and the history.** Thin dotted lines (orange for buy, red for sell) show where it charges (the value is above the buy line) and where it sells (below the sell line), so you can see why it acts when it does.
- **The history has a grid energy panel.** Each half-hour shows the energy imported (up) and exported (down), and the car as an outline over the import.
- **Both charts mark a re-plan.** A small tick on the time axis shows each moment the plan was worked out again.
- Needs app 0.9.150 for the recorded lines, the energy and the ticks (an older app shows the charts as before, without them).

## 0.9.140

### Behaviour changes

- **The Engine v2 history and the plan chart draw the modes as they ran.** Each mode band starts and ends where the change happened, not on the half-hour, and the battery level line has a point at each change. Needs app 0.9.140; with an older app both keep drawing half-hours.

## 0.9.138

### Behaviour changes

- **The settings profile is shown with engine v2.** The engine badge reads "Engine v2 · <profile>", the Engine v2 history's chip reads "Engine v2 in control · <profile>", and the same-day comparison's "in control" column shows the profile. Needs app 0.9.138 for the profile (an older app shows them as before).

## 0.9.137

### Behaviour changes

- **Settings profiles for engine v2.** A "Settings profile" box under the Planning engine choice lets you load "defaults", "2.1 beta" or your own saved profile, save your changes as a new profile, update or delete one of yours, and revert to the profile in use. It shows how many settings you have changed from the profile. Engine 2.1 beta is no longer a separate engine button; with the new app it is a profile. Needs app 0.9.137 for profiles (an older app shows the page as before).

## 0.9.136

### Behaviour changes

- **"Engine 2.1 beta" on the config page** (needs app 0.9.136): a third button under "Planning engine", shown only when the app says it has the choice. Switching asks for the same confirmation as v1 and v2, with a note that it is a beta. While it is in use, the "Engine v2 settings" group says "in use by engine 2.1 beta": the two share one set of settings, including ten new ones.

## 0.9.135

### Behaviour changes

- **"Re-plan now" under the expected timeline** (needs app 0.9.135): asks the app to re-plan, then says "Re-planning..." and "Re-planned at HH:MM" when the new plan arrives, or says so when nothing came back within 20 seconds.
- **The value map's buy line uses the price the plan worked with for a smart slot that is only a chance** (the chance of the offered price plus the rest at the standard rate), now that the timeline draws such a slot at the offered price. Before, the buy line and the plan could disagree about whether a charge was worth it.

## 0.9.131

### Behaviour changes

None. This release is for the card only: nothing about planning, control or the app's sensors changes.
- **The plan chart now opens an hour before "now" and stays where you put it on a phone.** Inside Home Assistant the chart was positioned before it had a width, so it opened at the start of the 18 hours of history, and every update (several sensors change together) sent it back there while you were sliding. It now waits until it has a width before positioning, and ignores the old chart being swapped out.

## 0.9.130

- **The plan chart now opens an hour before "now" every time.** In 0.9.128 it could open at the start of the 18 hours instead, when the card was built before it was on screen (for example a dashboard view you had not opened yet). It now opens in the right place and keeps your place when you scroll.

## 0.9.128

- The engine card's **plan chart now opens one hour before "now"**, so the now line sits near the left edge. The rest of the last 18 hours is still a scroll to the left.
- **On a phone the chart no longer jumps back while you scroll it.** A redraw (a sensor update, or the once-a-minute clock) that arrives while your finger is on the chart now waits until you let go, and your place is kept.
- The chart's hover text now survives every redraw, a card on a hidden dashboard view catches up when you return to it, and the leftmost hour label is no longer clipped.

## 0.9.126

- The engine card's **plan chart now shows the last 18 hours** as they ran (modes, battery level, import price), shaded left of "now", and scrolls back over them. It needs app 0.9.126 for the history; with an older app the chart looks as before.
- **"Now" on the plan chart follows the clock.** It used to stay where the plan was last worked out, because the chart only redrew when the plan changed. It now redraws about once a minute while on screen, without losing your scroll position.

## 0.9.120

- New **Stop the car charger during grid events** switch on the Config page, under Grid events (on by default). It needs app 0.9.120, so the card now asks for that version or newer.

## 0.9.118

### Changed

- **The expected-timeline hover text is now a box under the chart**, like the value map's, which reads better on a phone. Move
  over the chart, or tap it, and a guide line marks the time while the box shows the mode and what ends it, the battery
  level (with its likely range), the import price, the sun forecast and the house use expected.

### Fixed

- **The sun and house strip no longer vanishes when no sun is expected.** When the plan's window has no sun in it (for
  example in the evening, before sunrise), the whole strip was left out. It now stays with the house line, and the legend says
  no sun is expected in this window.

## 0.9.117

### New

- **Hover text on the Engine v2 expected timeline.** Move the pointer over the chart (or tap it on a phone) to see a guide
  line and a small box with what the plan says at that time: the mode and whether it happened or is expected, what ends it
  and why, the battery level (with the likely range for the future), the import price (and whether it is a smart slot that
  may not come, a grid event or free power), the sun forecast and the house use expected. It shows the values and status
  that do not fit inside the bars.

### Fixed

- **Engine v2 lists now run newest first, then older as you scroll down.** "Mode changes" on the history card was oldest
  first. "Latest" on the Engine v2 today card showed the oldest of the recent entries instead of the newest (it reversed a
  list the app already sends newest first); it now shows up to 30, newest at the top, in a scrolling box.

## 0.9.115

### New

- **The Engine v2 expected-timeline chart shows the sun forecast.** A strip along the bottom of the chart shows the sun
  the plan expects (shaded from the low to the high forecast, with the middle line) and the house use expected, so you
  can see where spare sun is meant to go. The legend gives the strip's scale. Needs app 0.9.115; with an older app the
  chart looks as before.

## 0.9.113

### Behaviour changes

- None.

### Fixes

- **The engine badge sizes itself to its text.** On a phone its line wraps, and the card below covered it.

## 0.9.112

### Behaviour changes

- None.

### Fixes

- **The engine tab icons show their 1 and 2.** The digit was cut out with a drawing rule Home Assistant's icons don't use, so
  both tabs showed the same plain engine. The digits are now drawn so they cut out the way Home Assistant draws icons.

## 0.9.111

### Behaviour changes

- **Saving waits longer for PowerEngine's answer** (45 seconds, was 15), and if none comes it says the change may still go
  through and to refresh in a minute, instead of reporting an error. A late answer still replaces the message.

### Fixes

- **Engine tab icons now draw.** Home Assistant draws the dashboard's tabs before the card file has loaded, so the engine icons
  were treated as unknown and left blank for good. Once the card has registered them it now has Home Assistant look them up again.

## 0.9.110

### Behaviour changes

- **Engine icons and badges:** the two engine pages get an engine icon with 1 or 2 on it, and a badge saying whether that engine is
  Active, Paused or Passive.

### New

- **Engine v2 history card** with a day picker (level as run against expected, modes, prices, value of a stored kWh, mode changes).
- **Engines compared, same day** card for the Costs page: savings of engine v1, engine v2 and the best possible for the last 7 days,
  the better engine marked, the difference, which engine was in control, totals and the calibration line.
- The setting "Engine comparison (Costs page)" on the Config page.

## 0.9.109

### Behaviour changes

- When PowerEngine updates its own Home Assistant package, the Configuration and Update cards show "PowerEngine has updated its Home Assistant package." An administrator presses **Load PowerEngine's Home Assistant changes** to load it without restarting Home Assistant; other users are asked to get an administrator to do it.
- After you change "Other battery controller" and save, the same banner appears straight away.
- If PowerEngine can't manage its Home Assistant package (no packages folder, a package set up by hand, or an error), the Configuration page and setup checklist show a short explanation with a link to the install guide.
- Needs PowerEngine 0.9.109 or later for the banner; with an older app nothing changes.

## 0.9.108

### Behaviour changes

- **Predbat is now optional on the dashboard.** The Battery controller handover card (the Predbat and PowerEngine switch) only appears when Predbat is set as the other battery controller (with an older app: only when Predbat's own entities exist in Home Assistant). Everyone else sees nothing in its place.
- **Config page, Inverter control:** a new choice, **Other battery controller** (none, Predbat or another controller). Until you save a choice it shows the one PowerEngine is using, worked out from your guards. The three guard entities show only when Predbat or another controller is chosen, and they no longer count as "needed to go live" otherwise. If nothing is chosen and no guard is mapped, a prompt (and the setup checklist) asks you to choose, because PowerEngine won't go Active until you do.
- The handover card no longer checks or lists a built-in set of personal charge and discharge automations.
- The Tests page's first step mentions Predbat only when Predbat is in use.

## 0.9.107

### Behaviour changes

- **The Engine v2 page shows engine v2's preview while engine v1 is in control.** The three engine v2 cards (Monitoring, plan and health) now show the data with a line at the top, "Preview: engine v1 is in control. Nothing is sent.", and the Monitoring card says "Would be charging ..." instead of the live mode. The old "Engine v2 is not running (engine v1 is)" shows only when there is nothing to preview (an app older than 0.9.107, or the preview switched off). The new setting appears on the config page by itself.

## 0.9.106

### Behaviour changes

- **Config page: an engine choice at the top** (engine v1 or engine v2, with a confirmation either way), and the settings in
  three groups: Your house, Engine v1 settings and Engine v2 settings. The engine not in use is dimmed but stays editable.
  With an app older than 0.9.106 nothing changes.

### New

- Three cards for engine v2: what it is doing now and what ends it (with the value of a stored kWh against today's prices),
  the expected timeline and value map, and its health.
- The new shared setting "Battery's hard floor" in the battery section.

## 0.9.105

- The Config page's *Tariff and planning* section has the new **Overnight window** choice (Learned or Fixed times) and the two fixed times, with a line showing the window in use and the one learned from the rates (needs app 0.9.105; an older app has no such settings or line, so nothing shows).

## 0.9.104

- New setting **Shortest real charge** and tick box **Learn: shortest real charge** in the car section of the Config page (needs app 0.9.104; an older app does not list them, so nothing shows).
- The car section is now titled "Car and smart charging" (it carried the supplier's name).
- Removing the car charger in Your system also switches off the new learning tick box.

## 0.9.100

- New **Report a problem** section in the diagnostics card on the Health tab. Type a title and a short description and press **Prepare report**: the card collects the diagnostics and replaces account, meter and serial numbers, site and device ids, emails and postcodes with placeholders. You can read exactly what will be sent. **Download and open GitHub issue** saves the file and opens a pre-filled GitHub issue (you need a GitHub account); drag the file onto the issue's Diagnostics box and submit.
- The ordinary diagnostics export is unchanged.

## 0.9.97

- New **Override** card at the top of the Monitoring page: shows what the inverter is doing and lets an admin switch it to Self-use, Hold, Charge or Export for a plan window, a number of half-hours, until a time, or permanently, with a Cancel button while one is on. It hides itself on an app without the override.
- The dashboard's "Mode" tile is renamed **Power Engine**. Needs card 0.9.97 (the dashboard now names the new card).

## 0.9.95

### Your system

- **New "Your system" card replaces the Setup wizard** (`custom:powerengine-system-card`; the old `powerengine-wizard-card` name still works). The list of equipment is read only. **Change your system** opens a panel to add, edit, replace and remove equipment: pick what it is, find it in Home Assistant, choose its entities (with live values, a sign check and an Invert tick), review.
- Changes are a draft until **Apply to System**. **Save draft** keeps it in this browser; closing with unsaved edits asks Save draft, Discard changes or Keep editing. Apply sends only the equipment changes.
- Removing an optional part, an extra solar plant or another device shows what goes with it first. The inverter and the tariff can only be replaced.
- The configuration card no longer edits the inverter, tariff, solar plants or other devices; use Your system. It follows the saved equipment after an Apply, so its own Save never writes old equipment back.
- Needs PowerEngine app 0.9.95 for the dashboard that names the new card; the card itself works with the same apps as before.

## 0.9.93

### Other devices

- **Your system now has an "Other devices" list** (with PowerEngine app 0.9.93 or newer; older apps don't show it). Add a device, pick which of its battery level, battery power and solar power sensors to use, and see what PowerEngine reads from it. Devices are read only: nothing is controlled but the inverter chosen above.
- Devices you already have in Home Assistant that PowerEngine doesn't use, other than solar-only ones, are now described as "could be added as a read-only device below" instead of "not supported yet" when the app supports it.

## 0.9.89

### Setup wizard and Config page

- The Setup wizard is folded away on a system that is already set up, and starts open on a new one.
- It reads what PowerEngine already uses for each part and keeps that chosen (EDF stays chosen if the Octopus integration is also installed). Other devices Home Assistant has for a part are listed as "also found, not used".
- **Solar plants:** shows the plants you already have, offers other solar-looking devices (a second solar inverter, plug-in panels) to add as read-only plants with their power and energy-today guessed, and lets you add or remove one without touching the other parts. A hybrid inverter with a battery that has no support yet is listed as "not supported yet", with the entity-list export.
- **Your system** lists the solar plants it counts and those devices too, and its labels match the wizard (Car charger, Electricity tariff, Solar forecast, Grid events).
- The setup checklist card hides itself when everything is in place.
- Wording: the grid events switch, section and notification are named "Grid events" instead of after the provider.

## 0.9.88

### Setup wizard

- New **Setup wizard** card (`custom:powerengine-wizard-card`). It lists what a home needs (required and optional parts, found or not found in Home Assistant, with install links), lets you pick each device, fills PowerEngine's inputs from that device's own entities with live values, checks battery and grid signs against what you see (with one-click Invert) and that house load adds up, and saves a Passive setup. Parts you don't have can be skipped; on an already configured system you can change one part at a time.
- **Candidate entities export** for hardware PowerEngine doesn't support yet: download or copy a scrubbed list of a device's entities (units, options, limits, firmware) to send with a support request.
- The card hides the wizard when the app is too old to tell it what to look for.

## 0.9.86

- Plan history day picker card (`powerengine-history-date-card`): a date box with previous/next day arrows, used by the dashboard's Plan history page. It hides itself on an older app.

## 0.9.84

- New Plan history date picker card, used by the dashboard's Plan history page. It hides itself on an older app.

## 0.9.82

- The Tests page no longer shows "Configuration error". (It broke in 0.9.71.)

## 0.9.77

- Configuration page: the new "Inverter max output" setting appears under Inverter control.
- Update card: a **Check for updates** button (admins) asks HACS to look for new releases, and the Update button stays disabled until an update is known.
- Setup checklist: the "Check again" button is gone (the checks run on load and after an install).

## 0.9.75

- The savings chart's **Custom** button is gone, along with the date-range line under the chart. The other buttons (Yesterday, Last 7 days, This month, Last 30 days) work as before.
- The card works with app 0.9.69 or newer again.

## 0.9.74

- The savings chart has a new Custom button, shown when the app offers a chosen date range. It brings back the range you picked with From and To, so you can switch away and return to it.
- The chart's caption shows the dates of the chosen range and how many days it covers, with a short note if some of the days asked for had no costs recorded.
- The card now asks for app 0.9.72 or newer (the version that added the date range).

## 0.9.71

- Checks versions against a minimum instead of requiring an exact match: this card needs app 0.9.69 or later, and it
  warns on the update and health cards if the app is older, or if the app asks for a newer card. From now on the card
  is released only when it changes.

## 0.9.70

- The demo card reloads the page once after a demo start, day change or exit, so the demo's dashboard shows without a manual refresh.

## 0.9.69

- The demo welcome card uses the demo's own day titles (the same as the banner). Health says "Not set up yet" before setup. true/false settings that arrive as text are read as booleans in the config form.

## 0.9.68

- No card changes; version kept in step.

## 0.9.67

- No card changes; version kept in step.

## 0.9.66

- New **Your system** block at the top of the configuration card: choose the inverter (and its firmware), car charger, car, tariff, forecast and grid events, with each option's test status. It warns before an inverter change switches PowerEngine to Passive, and shows a banner until the supervised tests are run again. It is hidden with an older app.

## 0.9.65

- No card changes; version kept in step.

## 0.9.64

- No card changes; version kept in step.

## 0.9.63

- New `powerengine-demo-card`: a welcome card with a day picker on an unconfigured PowerEngine, and a demo banner with day switching and exit. It hides itself on a set-up system. The setup card gains a "Try the demo" link.

## 0.9.62

- New `powerengine-setup-card`: checks HACS (and its AppDaemon option), the AppDaemon add-on, the PowerEngine app, the chart cards and MQTT, with HACS install buttons for admins. Available in the card picker as "PowerEngine setup".

## 0.9.61

- Supplier and device names on the Config page come from PowerEngine (identical text for EDF/Zappi/Solcast/Solis).

## 0.9.60

- Config page: smart-charge request options (requests per day, time between requests, look-ahead, whole-house slots, skip when the car is full) in the car and smart-charge section.

## 0.9.59

No card changes; version kept in step with the app.

## 0.9.58

No card changes; version kept in step with the app.

## 0.9.57

No card changes; version kept in step with the app.

## 0.9.56

No card changes; version kept in step with the app.

## 0.9.55

No card changes; version kept in step with the app.

## 0.9.54

- Waterfall card: tap a column (or its label) for its name and exact value; "Day-to-day" label.

## 0.9.53

- Waterfall card drawn as columns cascading left to right (short labels and rounded values on narrow screens).

## 0.9.52

- New `powerengine-waterfall-card` (Costs page): where the savings came from, per period, drawn as rows that stay readable on a phone.

## 0.9.51

No card changes; version kept in step with the app.

## 0.9.50

No card changes; version kept in step with the app.

## 0.9.49

- New `powerengine-health-card` (findings with Dismiss) and `powerengine-log-card` (Health tab).
- Update card: shows a release GitHub has before HACS does, and its "what's new" notes.

## 0.9.48

- No card changes; version kept in step with the app.

## 0.9.47

- No card changes; version kept in step with the app.

## 0.9.46

- New `powerengine-update-card` on Configuration: refreshes both PowerEngine repositories in HACS, runs
  `script.powerengine_update`, shows progress and a Reload page button.

## 0.9.45

- No card changes; version kept in step with the app.

## 0.9.44

- No card changes; version kept in step with the app.

## 0.9.43

- No card changes; version kept in step with the app.

## 0.9.42

- No card changes; version kept in step with the app.

## 0.9.41

- No card changes; version kept in step with the app.

## 0.9.40

- No card changes; version kept in step with the app.

## 0.9.39

- Battery topic: *Learn: inverter conversion losses*; the slow-down option now covers discharging too.

## 0.9.38

- Axle events: *Axle also earns the export rate* option.

## 0.9.37

- Grid and house: *Check meter import today* and *Check meter export today* inputs.

## 0.9.36

- Grid and house: *Use the check meter* option. Selling: *Overnight switch cost* setting.

## 0.9.35

- No card changes; version kept in step with the app.

## 0.9.34

- No card changes; version kept in step with the app.

## 0.9.33

- Configuration: the Check meter input now sits under Grid and house (it showed under Other).

## 0.9.32

- Diagnostics export: adds 24 h of history for the mapped raw grid, check-meter, battery, house-load and car
  readings, and for the grid meter cross-check.

## 0.9.31

- No card changes; version kept in step with the app.

## 0.9.30

- No card changes; version kept in step with the app.

## 0.9.29

- No card changes; version kept in step with the app.

## 0.9.28

- Selling section: **Deeper selling overnight** tick box (on by default).

## 0.9.27

- No card changes; version kept in step with the app.

## 0.9.26

- No card changes; version kept in step with the app.

## 0.9.25

- No card changes; version kept in step with the app.

## 0.9.24

- No card changes; version kept in step with the app.

## 0.9.23

- No card changes; version kept in step with the app.

## 0.9.22

- No card changes; version kept in step with the app.

## 0.9.21

- Inverter control section: **RAM max power**.

## 0.9.20

- No card changes; version kept in step with the app.

## 0.9.19

- No card changes; version kept in step with the app.

## 0.9.18

- Inverter control section: **RAM switch cost**.

## 0.9.17

- No card changes; version kept in step with the app.

## 0.9.16

- Inverter control section: **Control method** (Timed windows / RAM remote control) and **RAM refresh**.

## 0.9.15

- No card changes; version kept in step with the app.

## 0.9.14

- The Dampening tuning section shows what each setting saved over the last 7 days (or would have, if off).

## 0.9.13

- No card changes; version kept in step with the app.

## 0.9.12

- New **Dampening tuning** section on the config page: Restart hold-off (on) and Burst damping (off), with their
  timings.

## 0.9.11

- No card changes; version kept in step with the app.

## 0.9.10

- No card changes; version kept in step with the app.

## 0.9.9

- No card changes; version kept in step with the app.

## 0.9.8

- No card changes; version kept in step with the app.

## 0.9.7

- New **diagnostics export** card (Health tab): one JSON file with the app's settings, write log, plan and recent
  log lines, plus live entity states and 24 h of history. Download, Share or Copy, for uploading when there's no
  shell. Account numbers, serials and similar attributes are removed.

## 0.9.6

- No card changes; version kept in step with the app.

## 0.9.5

- Supervised test card rebuilt for the new **Tests** tab: per-test instructions (what it does, what to watch for on
  the inverter screen, what PowerEngine checks), live battery/grid readings, the new RAM remote-control tests
  (RC force charge/discharge, hold, failsafe) with their verdict, and a check that SolaX Modbus exposes the
  remote-control entities.

## 0.9.4

- No card changes; version kept in step with the app.

## 0.9.3

- No card changes; version kept in step with the app.

## 0.9.2

- No card changes; version kept in step with the app.

## 0.9.1

- No card changes; version kept in step with the app.

## 0.9.0

- No card changes; version kept in step with the app.

## 0.8.15

- No card changes; version kept in step with the app.

## 0.8.14

- No card changes; version kept in step with the app.

## 0.8.13

- No card changes; version kept in step with the app.

## 0.8.12

- No card changes; version kept in step with the app.

## 0.8.11

- No card changes; version kept in step with the app.

## 0.8.10

- No card changes; version kept in step with the app.

## 0.8.9

- Notifications: *Send to* offers Home Assistant's notification area (the default), your phones, or Off.

## 0.8.8

- No card changes; version kept in step with the app.

## 0.8.7

- No card changes; version kept in step with the app.

## 0.8.6

- No card changes; version kept in step with the app.

## 0.8.5

- Battery controller panel: with PowerEngine selected, a missing Predbat read-only switch (Predbat not connected) is shown as safe, with a note, instead of *not fully live*.

## 0.8.4

- No card changes; version kept in step with the app.

## 0.8.3

- No card changes; version kept in step with the app (automatic AppDaemon restart after updates).

## 0.8.2

- **Config page reorganised by topic.** One section per topic (Battery, Grid and house, Solar, Tariff and planning,
  Car and EDF smart charge, Selling, Axle, Free power, Cold battery, Inverter control, Tariff simulator,
  Notifications), each holding its switches, inputs, settings and learning. Anything new that isn't placed yet
  appears under *Other*.
- **Search** (names, descriptions and entity IDs) and filters: *All*, *Needs attention*, *Required*, *Optional*.
- **Colour:** green strip for a working required input, red for one missing or failing, amber for one needed
  only for something not in use (going live, or a switched-off feature), grey for optional. Section headers count
  required and optional inputs; a line at the top says how many required inputs need attention and jumps to the
  first.
- Inverter window, current, storage-mode and apply entities show as *Needed to go live* (required once live).
- Switched-off topics (Axle, Free power, Cold battery, Simulator) are greyed out with a note.
- Fix: new features (learning, cold caution) showed unticked on configs saved before 0.8.0.

## 0.8.1

- Features: four *Learn: …* switches replace *Use learned limits*.
- Settings: choice settings (a drop-down) shown in their section; *Battery location* under *Cold battery*.
- Fills in a role's `required` as "yes" when the app leaves it out.

## 0.8.0

- Features: *Use learned limits*, *Cold battery caution*, *Learn cold behaviour*. Settings: new *Cold battery* section.
- *Use measured* boxes on *Max charge power* and *Max discharge power*, showing the learned rate.

## 0.7.7

- No card changes; version kept in step with the app.

## 0.7.6

- No card changes; version kept in step with the app (phone-friendly charts in the dashboard).

## 0.7.5

- Handover panel: an entity it can't read makes the status *not fully live* (with a note) instead of *live*.

## 0.7.4

- Handover card: status line (live / paused for testing / not fully live), *Pause for testing* and *Resume (go live)*
  while PowerEngine is selected; PowerEngine mode should be *passive* under Predbat and *active* under PowerEngine;
  a pause while PowerEngine is selected is shown as testing, not as a fault.

## 0.7.3

- New `custom:powerengine-handover-card`: switch battery control between Predbat and PowerEngine through
  `input_select.battery_controller` (the app's handover package), with a confirm step, progress, a should-be/is table
  and *Re-apply*. Options: `title`, `selector`, `read_only`, `pause`, `mode`, `scripts`, `legacy`.

## 0.7.2 (beta)

### Behaviour changes
- None. Version kept in step with the app.

## 0.7.1 (beta)

### Behaviour changes
- None (the new *Window change cost* setting appears under Inverter control automatically). Version kept in step.

## 0.7.0 (beta)

### Behaviour changes
- Config card: new *Optimised planning* feature (on by default).

## 0.6.5 (beta)

### Behaviour changes
- None. Version kept in step with the app.

## 0.6.4 (beta)

### Behaviour changes
- None (the new arbitrage settings appear in the Arbitrage section automatically). Version kept in step.

## 0.6.3 (beta)

### Behaviour changes
- None. Version kept in step with the app.

## 0.6.2 (beta)

### Behaviour changes
- Simulator set-up card: equipment settings (bigger battery, more solar, a second car).

## 0.6.1 (beta)

### Behaviour changes
- **New `custom:powerengine-sim-card`** (Simulator tab): imports a year of hourly energy from Home Assistant's
  statistics for the Simulator while an admin has the page open, and holds the heat-pump settings.

## 0.6.0 (beta)

### Behaviour changes
- Config card: new *Tariff simulator* feature and *Tariff opportunities* notification.

## 0.5.18 (beta)

### Behaviour changes
- None. Version kept in step with the app.

## 0.5.17 (beta)

### Behaviour changes
- **Use measured** tick box also on the new *Battery round-trip efficiency* input.

## 0.5.16 (beta)

### Behaviour changes
- **Use measured** tick box next to *Usable battery capacity*, with the measured figure when there is one.

## 0.5.15 (beta)

### Behaviour changes
- None. Version kept in step with the app.

## 0.5.14 (beta)

### Behaviour changes
- **Active can be chosen** under Operation on the config card, with a warning of what it does.

## 0.5.13 (beta)

### Behaviour changes
- **New `custom:powerengine-test-card`** (Config tab): starts a supervised inverter test in the app (admins only;
  the app refuses unless the handover guards are safe). Shows the status and each step.
- Config card: note on the new *Handover guards* section.

## 0.5.12 (beta)

### Behaviour changes
- None. Version kept in step with the app.

## 0.5.11 (beta)

### Behaviour changes
- None. Version kept in step with the app.

## 0.5.10 (beta)

### Behaviour changes
- None. Version kept in step with the app.

## 0.5.9 (beta)

### Behaviour changes
- None. Version kept in step with the app.

## 0.5.8 (beta)

### Behaviour changes
- Smart-charge optimisation description updated.

## 0.5.7 (beta)

### Behaviour changes
- Energy arbitrage description updated: it is now planned (and simulated in Passive mode).

## 0.5.6 (beta)

### Behaviour changes
- None. Version kept in step with the app.

## 0.5.5 (beta)

### Behaviour changes
- New feature checkbox **Automatic cheap threshold** (on by default).

## 0.5.4 (beta)

### Behaviour changes
- Unmapped inputs with a suggested entity now show **Suggested: <entity>** (click to use it), and a section with
  several shows **Use all N suggested entities**. Previously suggestions were only pre-filled on a brand-new
  config, so inputs added in later versions (like the Solis timed-slot controls) started empty.

## 0.5.3 (beta)

### Behaviour changes
- Reads the settings list from the app's new `sensor.pe_map_settings` (app 0.5.3), falling back to the old location.

## 0.5.2 (beta)

### Behaviour changes
- None. Version kept in step with the app.

## 0.5.1 (beta)

### Behaviour changes
- New **Notifications** section on the config page: choose the phone's notify service (from those HA offers) and which notifications to send.

## 0.5.0 (beta)

### Behaviour changes
- None. Version kept in step with the app.

## 0.4.13 (beta)

### Behaviour changes
- None. Version kept in step with the app.

## 0.4.12 (beta)

### Behaviour changes
- New `custom:powerengine-toggle-card`: a discreet row with an optional title on the left and a small switch
  (icon, name, slider) on the right; no card box. Used by the app's Costs tab for number alignment.

## 0.4.11 (beta)

### Behaviour changes
- None. Version kept in step with the app.

## 0.4.10 (beta)

### Behaviour changes
- None. Version kept in step with the app.

## 0.4.9 (beta)

### Behaviour changes
- None. Version kept in step with the app.

## 0.4.8 (beta)

### Behaviour changes
- None. Version kept in step with the app.

## 0.4.7 (beta)

### Behaviour changes
- With Battery charging power and Battery discharging power both mapped, Battery power shows **Not used** (no
  sign note, no Invert, no live readout or problems) and the pair show **Required**.

## 0.4.6 (beta)

### Behaviour changes
- None. Version kept in step with the app.

## 0.4.5 (beta)

### Behaviour changes
- None. Version kept in step with the app (the new battery inputs appear automatically).

## 0.4.4 (beta)

### Behaviour changes
- None. Version kept in step with the app.

## 0.4.3 (beta)

### Behaviour changes
- None. Version kept in step with the app.

## 0.4.2 (beta)

### Behaviour changes
- The config page is split into collapsible sections: Operation and features; settings by topic (Battery and
  charging, Supply limits, Axle events, Arbitrage); one section per input group; Solar plants.
- Each section's header shows how many items need checking and how many are unsaved; sections with problems open
  by themselves. *Expand all* / *Collapse all* at the top; which sections are open is remembered in this browser.
- With an older app (no sections sent), all settings appear in one section.

## 0.4.1 (beta)

### Behaviour changes
- None. Version kept in step with the app.

## 0.4.0 (beta)

### Behaviour changes
- None. Version kept in step with the app.

## 0.3.8 (beta)

### Behaviour changes
- New feature checkbox **Top up when cheap** (on by default).
- The **Energy arbitrage** option says it isn't built yet and warns to check export tariff terms.

## 0.3.7 (beta)

### Behaviour changes
- None. The settings section is now headed "Safety, limits and thresholds" (it now holds the main fuse and car charger settings).

## 0.3.6 (beta)

### Behaviour changes
- None. Version kept in step with the app.

## 0.3.5 (beta)

### Behaviour changes
- None. Version kept in step with the app.

## 0.3.4 (beta)

### Behaviour changes
- None. Version kept in step with the app.

## 0.3.3 (beta)

### Behaviour changes
- None. Version kept in step with the app.

## 0.3.2 (beta)

### Behaviour changes
- None. Version kept in step with the app.

## 0.3.1 (beta)

### Behaviour changes
- None. Version kept in step with the app.

## 0.3.0 (beta)

### Behaviour changes
- None. Version kept in step with the app (0.3.0, "Plan").

## 0.2.0 (beta)

### Behaviour changes
- None to your devices.

### Added
- **Safety and thresholds** section (minimum reserve, cheap-import threshold, grid-charge target, restart margin,
  Axle look-ahead and margin), with ranges checked before saving.
- **House load includes the car charger** option (Grid and house).
- View-only mode for non-admin users.
- The card now lives on the PowerEngine dashboard's **Config** tab; a separate dashboard is no longer needed.

## 0.1.0 (beta)

### Behaviour changes
- None. Version kept in step with the app (0.1.0, "See").

## 0.0.4 (beta)

### Behaviour changes
- None to your devices. The card can now **save** PowerEngine's configuration (admin only).

### Added
- Every input grouped by area, each with a one-line description and a Required/Optional badge.
- HA entity pickers, with suggestions pre-filled from your system on first use; fixed values where allowed.
- **Live values** next to each input (rate lists, forecasts and dispatches are summarised).
- Signed inputs show the expected sign, an **Invert** tickbox, and how PowerEngine will read the live value.
- Instant checks (exists, domain, unit, availability) and PowerEngine's own check after saving.
- Solar plants: main plant plus **+ Add solar plant**.
- Features and operation mode (Active disabled in this build).
- Bump/boost entities can never be chosen as control outputs.

## 0.0.3 (beta)

### Behaviour changes
- None. The card now shows whether the app is running, its version and operation mode, and warns if app and card versions differ.

## 0.0.2 (beta)

### Behaviour changes
- None. Version bump to stay in step with the app (0.0.2).

## 0.0.1 (beta)

### Behaviour changes
- None. Scaffold card: shows its version and whether the PowerEngine app is detected. No editing yet.
