# Changelog

Every release lists **Behaviour changes** (anything that changes what PowerEngine does to your system) first.

## 0.9.177 (beta)

### Behaviour changes

- **Alexa reminders under an hour say minutes.** A reminder set for 0.5 or 0.25 hours before a grid event used to say "starts in 0 hours". It now says "starts in 30 minutes" or "starts in 15 minutes", and "1 hour 30 minutes" for 1.5. Reminders of one hour or more are worded as before.

### Behind the scenes

- `announce._span` pluralises minutes ("1 minute"); the under-an-hour wording is tested in `tests/test_announce.py`.

## 0.9.176 (beta)

### Behaviour changes

- **Alexa speaker groups are listed apart from single speakers.** On the Config page the Announcements speaker list now has "Single speakers" and "Speaker groups" headings, with "Everywhere" under the groups. Ticking a group as well as a speaker that belongs to it makes that speaker say the announcement twice, so the page now warns about that. The app is unchanged in this release; the change is in the card.

## 0.9.175 (beta)

### Behaviour changes

- **Test announcement button.** The Config page can now play a test on your Alexa speakers: "Daddybot here. Test message." (your own opening words, then "Test message."). It uses the speakers ticked on the page at that moment, so you can try a new speaker before saving, and it ignores the allowed hours.
- **Announcements have their own section.** The Alexa settings have moved out of Notifications into their own "Announcements" heading, away from the Engine v1 and Engine v2 settings. They work with either engine and are separate from Home Assistant's notification area. Nothing about how they behave has changed, and your saved settings carry over.

### Behind the scenes

- New `pe_alexa_test` event (admin only, like the other Config page buttons) and `pe_alexa_test_result` answer; the app only accepts Alexa Media Player `notify.alexa_media_*` services in it.

## 0.9.174 (beta)

### Behaviour changes

- **Alexa announcements for grid events (off until you switch it on).** PowerEngine can now speak reminders on your Alexa speakers: by default 8, 5, 2 and 1 hours before a grid event and as it starts. Each says the battery level and whether the house looks prepared ("We're looking well prepared"), or whether saving power would help ("We should be fine, but saving power would help" / "The battery may run short"). The 2-hour and 1-hour reminders ask the house to start turning things off when saving would help, and the announcement as the event starts always asks for computers, ovens and lights to be switched off.
- **It introduces itself.** Every announcement starts with "Daddybot here." (the opening words are a setting, and can be emptied).
- **Quiet hours.** Nothing is announced before 07:30 on a weekday, before 09:00 on a Saturday or Sunday, or from 21:00. A reminder that falls in those hours is skipped, not said late. A reminder more than 20 minutes late is also dropped.
- **Set it up on the Config page, under Notifications:** the "Alexa announcements" switch, which speakers (any of your Alexa Media Player speakers, or "Everywhere"), the opening words, the three times, and the hours before the event. These need card 0.9.174, which this release includes; an older card is warned about.
- **"Ready" means:** with engine v2, the battery's projected level through the event stays above the reserve in the cautious case; "saving power would help" means only the middle case does; "may run short" means neither. Engine v1 compares the level now with what the event needs.

### Behind the scenes

- New `pe_core/announce.py` (when, ready or not, wording; tests in `tests/test_announce.py`). `notifications.alexa` in `config.yaml` holds `services`, `hours_before`, `earliest_weekday`, `earliest_weekend`, `latest` and `intro`; the on/off switch is `notifications.events.alexa`. Alexa keys are exempt from the 10-a-day notification cap. `MIN_CARD_VERSION` is 0.9.174, because an older card's save would drop the Alexa settings.
- Not yet heard on a real speaker: the first live grid event after switching it on is the first real test.

## 0.9.173 (beta)

### Behaviour changes

- **A day that made money now says so.** Where the Costs tab, the daily notification, the waterfall's last bar and the Simulator sentence used to say "you paid −£1.20", they now say "you were paid £1.20" (or "you have been paid" for today). Days with a cost read exactly as before. The table column "You paid" and the chart series of the same name are unchanged; a negative figure there still means money in.

### Behind the scenes

- `notify.daily_message` and `costs.waterfall` choose the verb from the sign of the total (below −£0.005); the dashboard headline, Today card and Simulator macros do the same. Golden dashboard render regenerated. No sensor or attribute changed shape, so the card repo is not released.

## 0.9.172 (beta)

### Behaviour changes

- **The Costs tab now starts with today.** A new line above the yesterday headline says what the day has cost so far (or that it has paid you), what it would have cost with no solar or battery, how many of the day's 48 half-hours that covers, and any Axle or free-power energy and payment so far today, with the time the last event ran.
- **An event is shown whole when a half-hour is short of readings.** On 29 Oct the 18:00 half-hour had about three minutes of missing readings, so the 18:30 half-hour looked like a new event and "Last event" showed one half-hour (1.78 kWh) of an hour-long event (3.48 kWh). Half-hours are now joined by their slot times. The day, month and savings figures were never affected; only the "Last event" summary was.

### Behind the scenes

- `CostBook._note_event` ends each half-hour at start + 1800 s instead of start + the seconds the recorder covered. `tests/test_costbook_events.py` has a case with 1617 s and 1707 s half-hours. The new dashboard card reads `sensor.pe_cost_today` and `sensor.pe_event_last`; no sensor or attribute changed shape, so the card repo is not released.

## 0.9.171 (beta)

### Behaviour changes

- **The car charger is kept stopped for the whole grid event.** On 27 Oct the Zappi went back to Eco+ within seconds of each stop, and PowerEngine gave up after three tries, six minutes into the event, leaving the car free to charge for the rest of it. It now stops the charger again whenever it finds the mode changed, no more often than every 2 minutes, until the event ends, and then puts the mode back as before.
- **A mode you set by hand during an event is now set back to Stopped** every 2 minutes. To keep a mode of your own through events, turn off the `axle_stop_car` setting.

### Behind the scenes

- `carstop.py` drops `MAX_STOPS_AFTER_FIRST`; the 2-minute gap (`MIN_GAP_S`) stays. `tests/test_carstop.py` re-stops about every 3 minutes for an hour and restores at the end. The cause of the Zappi's mode being set back is not found; no HA automation, AppDaemon app or the myenergi integration writes it.

## 0.9.170 (beta)

### Behaviour changes

- **A free-power session now shows as free on the charts and in the live sentences.** The meter keeps showing the tariff rate (6.66p) during a free session, and that is what the engine recorded for each half-hour, so the power page's charts drew the night at 6.66p once it was in the past (the planned part was drawn right), and the live sentences said "Import is 6.66p". The engine now treats the import price as 0p while a free session is active, records the half-hour as free, and the charts draw it at 0p and tag it "free power". What the engine decides is unchanged: the plan already priced the session at 0p.
- **The inverter clock is synced again when a sync did not take.** After the clocks went back on 25 Oct PowerEngine pressed the sync button, but the inverter stayed an hour ahead and the next attempt was a day away. A sync that leaves the clock 60 s or more out is now repeated after 30 minutes, up to twice, with a warning in the log; after that it waits a day as before.
- **No Health finding about the inverter clock with RAM control.** The finding said the timed charge and discharge windows were shifted, but RAM control sets no windows, so nothing depends on the clock. The drift is still on the Inverter clock drift sensor. With timed windows the finding is unchanged, and it now says when a sync was sent and the clock did not change.

### Behind the scenes

- `engine.py` zeroes `imp` while `r.free_active` and `free_power` is on (display and history only); `v2history` records `free` on a half-hour and publishes it on `sensor.pe_v2_history` and `sensor.pe_v2_recent` rows; `clock.sync_due(…, retries)`, `clock.finding(drift, ram, synced, now)`. Tests in `tests/test_clock.py` and `tests/test_v2history.py`; one line of `tests/golden/solis_parity.json` re-recorded (a clock 85 s out three hours after a sync is now due a retry).

## 0.9.169 (beta)

### Behaviour changes

- **A free night is seen whole.** The next-free-session sensors report one session at a time, so a night offered as several back-to-back sessions (for example 23:00 to midnight, then midnight to 07:00) was seen as only its first hour: the plan priced and charged for that hour, and the rest only appeared as each one began. A new optional input, **All free sessions** (the EDF integration's `…_free_electricity_session_events` entity, which lists every session), lets PowerEngine join sessions that touch or overlap into one run, so the plan, the prices and the notification cover the whole night. Without it, or while its list is empty, the three existing sensors are used as before.
- To use it, open Inputs, Free-power sessions and accept the suggested entity (it is pre-filled from the integration's name), then Save. Nothing changes until you do.

### Behind the scenes

- `parsing.free_chain`, `KrakenTariff.read_free_sessions(state, now)`, role `free_power_events` (attribute `events`). A few role descriptions were shortened to keep the input catalogue under 15,000 bytes. Tests in `tests/test_adapter_kraken.py`.

## 0.9.168 (beta)

### Behaviour changes

- **Engine v2's settings are now its own.** Until now five of them followed engine v1's equivalents unless you had saved them on the engine v2 page: Sell from the battery, Grid events, Export rate paid on top of a grid event, Free-power sessions and Your reserve. Changing the engine v1 page (for example Minimum reserve) therefore moved engine v2 with no sign on its page. Engine v2 now reads nothing from engine v1's settings. Hardware and site facts stay shared: battery size and rates, the hard floor, export limit, fuse, car charger power, the control settings and the learned values.
- **Nothing changes for your install on upgrade.** The first time 0.9.168 starts it writes the values engine v2 was following (only those that differ from engine v2's own default) into the saved engine v2 settings, marks the config `v2_split: true` and logs one line ("Engine v2 settings are now its own; values it was following from v1: …"). A backup of the config is kept as for any save. From then on the two engines' pages are independent. A new engine v2 install starts from engine v2's defaults, not from engine v1's values.
- Engine v2's Battery wear stays its own setting (0 unless you set it); it is not taken from engine 1's Battery wear cost.

### Behind the scenes

- `settings.parse_v2(raw, profile, legacy)` no longer takes v1's features and safety (`SEED_FROM` is gone); `settings.split_block` computes the one-off migration, `PowerEngine._split_v2_settings` applies it (in memory if the save fails, then retried at the next start), and `_on_save` keeps the `v2_split` mark when a card that does not know it saves. The nightly comparison and the demo template apply the same rule. Decision 8.12 in `docs/logic/08-decision-log.md`. Tests: `tests/test_v2_split.py`.

## 0.9.167 (beta)

### Behaviour changes

- **Settings now say which figure is really in use.** Under usable capacity, round-trip efficiency, max charge and max discharge power the card used to say "Fixed: 18 kWh" even when "Use measured" was ticked and a measured figure was being used. It now reads, for example, "In use: 17.02 kWh, measured (you entered 18 kWh)", or "In use: 18 kWh, as entered. The measured 17.02 kWh is not used: only 40 of the 100 readings needed so far". The input check on the Health tab says the same. The round trip also shows the grid-to-grid figure the plan prices a stored kWh with (the battery's own figure after the inverter's learned conversion losses).
- **The same line appears for the other figures the app may replace:** Car charger power and Export limit (learned), the Minimum reserve, and the Battery's hard floor (raised where the battery was seen to stop). The hard-floor readout in the engine v2 range section shows the floor in use.
- **Wording corrected.** "Everyday range: bottom" now says being low costs more the lower it goes (it said the opposite). "Highest grid charge" says a free-power session still charges to 100%. "Smart slots: keep above" says it is a soft cost 30 times the usual rate and does nothing while the firmness is Off. "Everyday range firmness" now gives the real rates for each choice. "Sample interval" says it needs a restart. "Learn: where discharging stops" says it raises the engine 1 reserve or the engine 2 hard floor. The two reserve settings and the two battery wear settings now say which engine each belongs to.
- **No inverter integration or person-specific wording** in the control method, RAM and test texts ("SolaX Modbus", "on yours").
- No change to what the plan does or what the inverter is sent.

### Behind the scenes

- New `pe_core/inuse.py` builds the sentences from the numbers the plan reads; `PowerEngine._in_use_report()` publishes them in `sensor.pe_diag_learned` attribute `use`, and `_evaluate` uses them for the "Fixed value" check message. `_hard_floor()` is the floor `_v2_facts` already used, now shared. Tests: `tests/test_inuse.py`.
- Docs: `docs/plans/engine-v2-architecture.md` said the 2.1 beta shortest run is 20 points (it is 5).

## 0.9.166 (beta)

### Behaviour changes

None. This release is for the card (the engine v2 history chart); the app itself is unchanged.

## 0.9.165 (beta)

### Behaviour changes

None.

## 0.9.164 (beta)

### Behaviour changes

- **Engine v2 remembers which half-hours were in a smart slot.** Each recorded half-hour now says whether a smart slot covered it, so the history and plan charts can keep drawing that price as a dashed line after the slot has run. Half-hours recorded before this update carry no mark and stay solid.

### Behind the scenes

- `v2history`: `_Acc` counts the time spent in a slot (`readings.current_dispatch()`); the record, the history series and `sensor.pe_v2_recent` carry `"slot": true` only when most of the half-hour was in a slot. Test: `tests/test_v2history.py`.

## 0.9.163 (beta)

### Behaviour changes

- **Engine v2's "2.2 beta" profile now has a gentle range instead of a firm one.** The battery still prefers 70 to 90%, but going above or below costs the plan much less: about 4p an hour to sit at 95% instead of 17.5p, so it fills up more readily when that avoids buying dear power later. In replays of 7 to 16 Oct this cost about £1.10 less, with 26 fewer mode changes, and made no difference on dull days. Removing the range altogether cost more, so the range stays. If you use 2.2 beta, the change applies on its own; a firmness you set yourself on top of the profile still wins. "2.1 beta" is unchanged.

### Behind the scenes

- `settings.BUILTIN_PROFILES["2.2 beta"]` sets `range_firmness: gentle`; its note says so. Decision log 8.11 records the simulator results.
- Tests: `tests/test_engine_v2_profiles.py`.

## 0.9.162 (beta)

### Behaviour changes

- **The house forecast is right again.** Since 14 Oct it expected about twice the house use on weekdays (45.8 kWh forecast against 26 kWh used on 19 Oct; about 2 kW all night). On each start PowerEngine rebuilt 14 days of house load from Home Assistant's history, corrected by the check meter, and that rebuilt figure replaced PowerEngine's own record. Its own record (the corrected live house load, which matches the Health tab's actuals) now comes first; the history only fills half-hours it lacks.
- **A sensor that drops out no longer carries its last reading.** When a reading in Home Assistant's history is unavailable, that time now counts as missing, not as the value before it.
- **Both engines plan with the corrected forecast.** The inflated forecast made stored energy look more valuable and kept more back for the house, so expect the plan to change: fewer "the house drifted from the forecast" re-plans overnight and more willingness to sell.
- **Engine v2's Self-use sentence no longer states a comparison that is false.** It said, for example, "worth 10.3p, less than the 6.66p import". When the plan chooses Self-use although a stored kWh is worth more than it saves, the sentence now gives the real reason: the battery is above the top of its everyday range, or Hold isn't allowed at that time.

### Behind the scenes

- `forecast.parse_history(..., gaps=True)` returns unavailable rows as None; `half_hour_means` counts them as gaps. `_rebuild_profile`: the load record wins over `_hist_means`.
- `execute._pick_for` and `value._reason`: the Self-use wording follows the real comparison.
- Tests: `tests/test_forecast.py` (two), `tests/test_engine_v2_execute.py` (one).

## 0.9.161 (beta)

### Behaviour changes

None. This release is for the card (the engine v2 history chart); the app itself is unchanged.

## 0.9.160 (beta)

### Behaviour changes

- **The engine v2 history now also records the sun and the house at their real timing.** `sensor.pe_v2_flows` carries the solar and house power as they happened, in 5-minute steps, next to the grid and car. A day Home Assistant no longer holds detail for shows none.
- **No other change to what the engine does.** The plan, the decisions and the inverter commands are untouched.

### Behind the scenes

- `real_flows` adds `sun` and `house` (kW x 0.5, like the others); the replay reads the solar plants for it (`history_entities(..., plants=True)`).

## 0.9.159 (beta)

### Behaviour changes

- **The engine v2 history now shows grid import, export and the car at their real timing.** Before, they were drawn in half-hour blocks. The app now reads Home Assistant's own record of grid and car power and publishes it in 5-minute steps on a new sensor, `sensor.pe_v2_flows`. The bar areas are the real energy. A day Home Assistant no longer holds detail for keeps the half-hour bars.
- **No other change to what the engine does.** The plan, the decisions and the inverter commands are untouched.

### Behind the scenes

- `pe_core/v2flows.py` (`real_flows`) rebuilds the day from the recorder with the replay machinery; `_day_flows` caches it (a past day once, today every 4 minutes) and a failed read is logged once and leaves the half-hour bars.
- `sensor.pe_v2_flows` is a separate sensor so `sensor.pe_v2_history` stays under the attribute budget.

## 0.9.158 (beta)

### Behaviour changes

- **Forecast caution now does something: it keeps energy back for the house.** While energy is dear, a sale never takes what the house may need before energy is certainly cheap again. A smart slot that may not come doesn't count as cheap. After such a sale, the house runs on what was kept instead of on the grid.
  - **Optimistic:** keeps nothing, the same as 0.9.157.
  - **Balanced (the new default):** keeps what the forecast's middle case says the house needs.
  - **Cautious:** keeps enough for a busy, dull day.
  - Before this, "Forecast caution" only set starting weights that learning replaced within days, so it had almost no effect.
  - This stops the pattern seen on 19 Oct: charge in a short smart slot, sell at 15p, then run out at about 18:00 and buy the evening at 28.84p.
- **The shortest charge or sale in the 2.1 beta and 2.2 beta profiles is 5 points, not 20.** At 20, a sale that only paid down to about 80% was forced on to 70%, selling energy the evening needed.
- **Replays (real money, end-of-day battery valued at the cheap rate), your settings:**
  - **Nine recorded October days with plentiful smart slots:** +£25.11 against +£25.47 on 0.9.157 (36p less).
  - **Three simulated dull days** (a brief early slot, no slot, an afternoon slot withdrawn): −£0.53 against −£1.43 (90p better).
  - **All twelve:** +£24.57 against +£24.03 (54p better), with 391 mode changes on the recorded days against 321.
  - On the recorded days trusting the slots always paid off, so caution costs a little there. On a day when the slots don't come, it saves much more.
- **The plan and the battery no longer split at the edge of a shortest run.** On 19 Oct at 10:11 the battery reached its level 0.1 point short of the shortest-run goal. The plan carried on charging, then rested on self-use; the battery treated the charge as complete and started selling. In both, the shortest run now ends when the run reaches its level.
- **Updating keeps the engine's saved state.** On the update to 0.9.157 the saved state was discarded once, because a field the new code no longer has couldn't be read back. Unknown fields are now ignored.

### Behind the scenes

- `value._keep_kwh` and the `keep` argument of `_choose` (all in the shared decision, so the plan and the battery agree); `value.KEEP_CASE` maps the caution; `value._rest` is the mode for the rest of a segment.
- `settings.START_WEIGHTS` replaces the caution-dependent starting weights.
- `execute`: unknown `Lines` fields are dropped on restore; `powerengine._v2_input` reads `slot_odds_pct` with a fallback.
- Tests: `tests/test_engine_v2_keep.py`, `tests/test_engine_v2_restore.py`, and the intent-settings tests updated.
- **Tried in the simulator and not adopted:**
  - a "leave the night at least this full" floor (little gain once the keep is in);
  - an "arrive at the night with at least X" margin (it held energy the evening needed);
  - a per-kWh price for charging above the range top (filling to 100% didn't help while the plan still sold the energy);
  - shortest-run variants that only gate the start or stop where a run stops paying (both cost money);
  - trusting announced slots in the keep (it removed most of the protection).

## 0.9.157 (beta)

### Behaviour changes

- **One decision for the plan and the battery.** Engine v2 now makes every decision with one piece of code. The expected timeline is that decision stepped forward over the forecast; the battery runs the same decision on the live level, the live prices and the mode it is actually in. Before, the live side had its own rules on top of the plan (price and level bands, a minimum time, a turn-round levy, "a change must pay for itself"), and the plan did not know about them, so what the forecast showed and what the battery did drifted apart. Those rules are gone.
- **Charges and sales run to a level, not for a time.** A charge or sale carries on across half-hours until it reaches its level, or until the price changes. When it gets there, the next decision is made straight away instead of waiting for the next half-hour. Idle time (Self-use or Hold) is still looked at again each half-hour, because the sun and house forecasts come in half-hours.
- **The next run starts now.** While the price stays the same, a charge or sale the plan would make later starts now and the idle time comes afterwards, because the forecast moves (smart slots come and go). A charge pulled forward this way stops at the top of your range.
- **"Now" is priced at the live rates.** A smart slot that is running is treated as certain, and a new rate that arrives a moment before its half-hour is used straight away.
- **Replays of 8 to 16 Oct:** with your settings, £25.56 earned over the nine days against £24.69 for 0.9.156 (87p more), with 318 mode changes against 279 and 30 flip-flops against 29. The forecast follows what the battery does far more closely (mean gap between planned and actual level 0.3 points, was 0.9).
- **Settings that say what you want.** The engine v2 settings page is reorganised into five sections:
  - **What the battery may do:** selling, grid events, free power, when Hold is allowed, the highest charge level and the highest price to charge at.
  - **The battery's range:** your reserve; the everyday range (bottom and top) and how firmly it holds (off, gentle, firm, strict); the shortest charge or sale; a level to keep above while a smart slot is counted on; and the smart slot odds, which move here from the shared settings.
  - **Trading:** the cost of a mode change, and battery wear.
  - **Forecast:** caution (cautious, balanced, optimistic) and whether to learn.
  - **Advanced:** the rest, collapsed.
  The comfort band and its top-up cost are gone (the range does that job), and so are 24 lower-level settings (the curve rates, the starting weights and so on); they are now worked out from the settings above. What you had saved carries over where it means the same thing, and the 2.1 beta and 2.2 beta profiles give the same behaviour as before: in the replays your settings give exactly the same result before and after the change.
- **Each setting was checked to do what it says**, over four stored days (`tools/sim/effects.py`): a lower range bottom keeps it out of the low levels (hours below 50%: 24.5 at 40%, 20.4 at 80%), a higher top lets it sit higher (hours above 90%: 1.3 at 80%, 16.1 at 97%), a firmer range keeps it inside (hours outside 70 to 90%: 61 off, 47 strict), a longer shortest run and a dearer mode change both mean fewer changes (162 to 137, and 151 to 118), wear makes the battery give out less (346 to 325 kWh at 6p), the charge price cap stops peak-rate charging, wider Hold means more Hold, and selling on means selling. The smart slot odds made no difference between 30% and 60% on these days and let the battery go lower at 90% (hours below 70%: 39.5, 39.1, 42.3).
- **Removed settings:** `executor`, `price_band_p`, `level_band_pct`, `min_dwell_s` and `reversal_levy_p` (the live-only rules), plus the comfort band, the level-curve and slot settings, the starting weights, the two learning switches, `prefer_self_use`, `hold_only_car_reserve`, `wear_house_p` and `wear_sale_p` (replaced as above). A saved value is ignored without error, or carried over where it means the same thing.

### Behind the scenes

- `value._choose` is the decision. `value._walk` steps it forward (each record now has `pieces`: the modes inside a segment), and `value.decide` is the executor's call. `execute._follow` keeps the decision in hand and decides again when the plan, the limits or the live rates change, when its time is up, or when a run reaches its level.
- `rules.LEVEL_BAND_PCT` (1 point) replaces `level_band_pct` for the reserve latch. The corridors, `as_run` and `idle_action` are gone.
- `V2Settings` derives the old tuning names as properties. `settings._migrate` carries saved values over, and `config.MOVED_TO_V2` seeds `slot_odds_pct` from the old safety setting.
- Tested and not used, because each cost money in the replays: a minimum time in a mode (2, 5 or 10 minutes) and a turn-round price (5 or 10p), both inside the shared decision, and a higher mode-change cost (4, 6 or 10p).
- Docs: `docs/plans/engine-v2-architecture.md` (the decision, the settings) and `docs/logic/08-decision-log.md` (8.9).

## 0.9.156 (beta)

### Behaviour changes

- **Fourteen engine v2 settings are gone from the settings page.** In replays of the eight stored days (8 to 15 Oct), switching each of them off or moving it changed nothing measurable, so they no longer need a setting. A saved value is ignored (no error).
  - **Removed, with their code:** "Re-plan when a charge or sale ends" (`replan_on_level`; results were identical), "Levy to restart a run that just ended" (`reentry_levy_p`; two of the eight days changed, by no mode changes and about 20p in total), and the two "energy left at the end" settings (`terminal_value`, `terminal_value_p`; the refill rule is the only one now).
  - **Now fixed numbers in the code, same values as before:** corridor width, longest step, solar forecast change, forecast drift, outside-expected-range minutes, batch window, backstop age, deadline grace, reading debounce and level resolution.
  - Everything else, including the level curves, switch cost, the reversal levy, the minimum time in a mode, the hold ban and the 15p grid-charge cap, is unchanged.
  - On the eight days, six come out identical to before. The two that differ (12 and 13 Oct) are the removed restart levy: one mode change apart, about 20p dearer in total.
- **One Engine tab on the dashboard.** The dashboard now shows only the page of the engine in use (Engine v1 or Engine v2) instead of both. Switching engine in the settings rewrites the dashboard, so refresh the page after switching. If PowerEngine can't tell which engine is in use (the demo), both stay.

### Behind the scenes

- `settings.RETIRED` lists the fourteen; the demoted ones are module constants (`triggers.REVALUE_COALESCE_S`, `observe.DRIFT_KWH` and so on) the simulator can still change as `engine_v2.<module>.<NAME>`. `value.TERMINAL_FIXED_P` stays only for tests and the simulator.
- `dashboard.render(..., engine=)` drops the other engine's page between `# BEGIN/END view-engine-v1|v2` comments. Tests: one in `tests/test_dashboard.py`, one in `tests/test_event_charge.py`; the value, trigger, execute and profile tests were updated for the constants.

## 0.9.155 (beta)

### Behaviour changes

- **A smart-slot change and a price change now wait for each other before the plan is worked out again ("price churn hold", 60 s).** EDF updates the smart-slot list first and its rates entity 20 to 60 seconds later, so the plan was being made twice, and the first one saw a half-updated tariff (on 17 Oct it drew a cheap rate as certain for a minute). Over 8 to 18 Oct, 435 of 870 re-plans were caused by one of these two changes, and 103 came in close pairs. The hold is measured from the first of the two, so one re-plan sees both; on those days it would have removed 94 re-plans (11% of all). Anything else that asks for a re-plan (the car, a grid event, a settings change, a drift) is not held, and takes any held change with it. The mode the battery is in still follows the live rate every few seconds; only the forecast waits.
- **New setting: Price churn hold** (Engine v2, "When" section), default 60 s, 0 to 300 s. At 0 the old behaviour returns.

### Behind the scenes

- `Triggers.due`: `HELD_KINDS` (`slots_changed`, `prices_published`) wait `price_churn_hold_s` from the first of them (`hold_since`, saved with the trigger state); everything else keeps `revalue_coalesce_s`. Tests: four in `tests/test_engine_v2_triggers.py`. Docs: the engine v2 plan and architecture pages.

## 0.9.154 (beta)

### Behaviour changes

- **The value line on the expected timeline is steadier.** The plan holds the battery right on the edge of a step in the value of a stored kWh (just enough for the evening peak, or the end of a charge it can only just finish), so the value read at the path flipped between the two sides of the step every 15 minutes and drew a zigzag. The app now averages the value along the path over about an hour. A real change still shows; the 15-minute flip-flop does not. Nothing the controller does changes; only the display. Works with the card you already have.

### Behind the scenes

- `publish._smoothed` (5-point centred moving average, `PATH_VALUE_SMOOTH`) over `_path_value`. Test in `tests/test_engine_v2_publish.py`.

## 0.9.153 (beta)

### Behaviour changes

None. The app is unchanged; this release carries the card update (the value map's "off the scale" label).

## 0.9.152 (beta)

### Behaviour changes

- **The expected timeline's value line no longer wobbles.** The app now publishes the value of a stored kWh at each point of the expected battery path, the same figure the controller reads at that level, instead of leaving the card to read it off a coarse copy of the value curve (one row an hour, one column per 10% of battery). That coarse reading made the line zigzag with a one-hour period as the battery slid through the grid. Nothing the controller does changes; only the display.

### Behind the scenes

- `publish._path_value` adds `value` to the timeline sensor's `path` (thinned with the rest of the path when the attribute is trimmed). Tests: two in `tests/test_engine_v2_publish.py`.

## 0.9.151 (beta)

### Behaviour changes

None. The app is unchanged; this release carries the card update (a key above the plan and history charts).

## 0.9.150 (beta)

### Behaviour changes

- **The plan and history charts can show more.** The app now records, for each half-hour, the buy and sell lines (the stored-kWh value above which it charges and below which it sells), the energy imported from and exported to the grid, and the energy the car took. It also records when the plan was worked out again. The recent-hours data the plan chart scrolls back over now carries the value of a stored kWh as well. Nothing the controller does changes. These figures start from the moment you update, so earlier hours and days show none of them.

### Behind the scenes

- `v2history`: the half-hour record gains `buy_line_p`, `sell_line_p`, `grid_in_kwh`, `grid_out_kwh` and `car_kwh`; the day file gains `replans`. Both sensors publish them under short names (`buy_p`, `sell_p`, `grid_in`, `grid_out`, `car`, `replans`). An older record without them publishes without them.
- Tests: two in `tests/test_v2history.py`.

## 0.9.149 (beta)

### Behaviour changes

- **The expected timeline no longer shows a rest where the battery would not rest.** When a charge or sale ended part-way through a half-hour, the plan filled the rest with a hold (or self-use) until the next price change, and the timeline showed it. With holds not allowed (the 2.2 beta profile), the controller doesn't wait there: its own lines decide, and it sells where a stored kWh is worth less than a sale brings, or charges where it is worth more than the import price. The timeline now shows that sale or charge, and the levels after it follow. A rest is still shown where the lines would not act. Nothing the controller does changes; only the display.
- On 8–15 Oct, of the rests the plan showed starting within 15 minutes, the old timeline showed 39 and the battery rested in 2; the new one shows 8 and the battery rested in 2. Further ahead the picture still changes as the plan is worked out again, as it always has.
- **The "until" time on a waiting stretch is now local time.** It used the UTC clock, so in summer a rest ending at 10:00 read "until 09:00".

### Behind the scenes

- `execute.idle_action` evaluates the executor's own buy/sell test (`lines`, the price band and the level band) for a stretch with no run; `publish._as_run` uses it and returns the moves that shift the levels after a run it adds, `_path_after_moves` applies them to the level path.
- Tests: four in `tests/test_engine_v2_publish.py`.

## 0.9.148 (beta)

### Behaviour changes

- **The battery now charges ahead of an announced grid event, just in time, even at the dear rate.** The 15p limit on grid charging (`charge_max_import_p`) used to hold even when an event was announced, so on a dull day with the battery run down the plan bought nothing and the event sold what little was left. Now, when an event is announced and the plan would reach its start short of what the event can sell, the limit is lifted for a calculated stretch only: from the latest safe start (the charger's rate, the level the plan reaches, what the event takes from the battery plus a 5 point margin) less half an hour, to the event's start. Charging in that stretch stops at the level the event needs. An event pays about 115p a kWh against about 34p to put a kWh in and take it out. No setting; once the stretch has begun it stays open. If the battery is already high enough, nothing changes.
- On seven winter-like synthetic days (dull sun, no daytime smart slots, house load ×1.4, event at 17:00 announced at 13:00), adjusted cost for the six usable days: one-hour event 2.22 before and -6.76 now; two-hour event -1.94 before and -24.71 now. Dear-rate charging was about 3 kWh a day for the one-hour event (a fixed six-hour window used 8-12 kWh). On the eight October days with an event added every evening, the result was within a few pence (-66.64 against -66.17 at 20:00, -64.35 against -64.42 at 17:00).
- **"Be prepared for a late grid event" and its two settings are removed** (`late_events`, `late_events_per_week`, `late_event_hours`). They treated an event as independent half-hours and so only ever valued the bottom 2.4 kWh above the floor. In the October tests (an event added to every day) preparing for an unannounced event was dearer than not, whichever way it was modelled, and buying at the dear rate for a 15-30% chance of an event loses on average. A value you saved for them is ignored, not an error. The beta profiles already had it off.

### Behind the scenes

- `eventcharge.py` works out the window at every revalue from the plan made with the rule in force, keeps it open once it has started (`latch`), and marks the forecast's segments (`Segment.event_charge_to`); `rules._core` lets a charge through and caps its ceiling there. A second solve is made only when an announced event is not reached.
- Removed `late_events*` from `settings.py` (kept in `RETIRED`), `value.py` (`_late_events`, `LATE_SHARES`, the event branch of `_backward`), the sim switches and the tests that covered them; the settings sensor has room again.
- Tests: `tests/test_event_charge.py`. One value test (holds in an arbitrage window) now excludes the rest of a part-way stop.
- Tested with `tools/sim` on synthetic winter days (`~/pe-sim/tools/winter.py`) and real days with an event injected (`inject.py`); the method and numbers are in `docs/plans/engine-v2-2-beta.md`.

## 0.9.147 (beta)

### Behaviour changes

- **The stored value no longer jumps because the plan itself is fixed, not smoothed.** The jumps in the value of a stored kWh came from the planner: a half-hour is planned as one mode, so some battery levels could not be reached by any mix and left kinks in the cost-to-go that each earlier half-hour copied back into a comb. The planner now fills those gaps (it takes the lowest convex curve from the reserve floor up, as if the half-hour could be shared between modes, which the live controller can do). The value of a stored kWh never rises with the battery level, and the plan no longer depends on the level step.
- **The 0.9.146 read-out smoothing is gone,** and so is the moving average that turning `late_events` on used to bring with it. With the planner fixed they changed nothing (identical results with and without).
- On the 7-14 Oct days in the simulator (2.2 beta profile): adjusted cost -£22.05, against -£21.92 on 0.9.145 and -£22.07 on 0.9.146; 232 mode changes (247 and 234); 42.9 hours below 60% (43.4 and 52.0, so the battery stays higher than in 0.9.146). A kWh bought above the 15p limit: 0.9 level points in the eight days, none before. On 14 Oct no stored-value curve rises with the level (86 of 86 did before).
- The level-curve settings were tuned on the jagged curve and are being retested on this planner; none are changed here.

### Behind the scenes

- `value.CONVEX_COST_TO_GO` and `_convex_from` in `_backward`; removed `MONOTONE_LINES`, `MONOTONE_MEDIAN`, `read_row`, `floor_pct_at`, `_smoothed`.
- Tests: `tests/test_convex_cost_to_go.py` replaces `tests/test_stored_value_smooth.py`; three value tests loosened (grid-event value, end of a look-ahead in a cheap window, holds in an arbitrage window).
- Cause and the way it was found: `docs/plans/engine-v2-level-penalty.md`.

## 0.9.146 (beta)

### Behaviour changes

- **The value of a stored kWh no longer jumps up and down.** While the battery drained steadily the number moved up and down every few seconds, and the stored-value chart looked like random blocks of colour. The value is now read as a curve that never rises with the battery level (more in the battery is never worth more per kWh), with single spikes removed first. It applies to the live value, to when a charge or a sale stops, and to the value chart; the plan itself is worked out as before. On the 7-14 Oct days in the simulator the adjusted cost was £0.15 better (-£22.07 against -£21.92), with 13 fewer mode changes (234 against 247) and no more flip-flops. On 14 Oct the live value changed direction 8 times in the day against 271. One cost: the battery sits a little lower, 52 hours below 60% in the eight days against 43.
- **The 2.2 beta profile's "Slots: bottom multiplier" is 30, not 300.** The 300 wall inside smart slots was dearer by £0.37 over those eight days with 12 more mode changes. If you already saved a value of your own it is used, as before.
- **The penalty table is read between its entries.** The level curves were looked up at the nearest of 100 entries per kWh, which made the value step at every entry. A small gain on its own (£0.25 over the eight days).

### Behind the scenes

- `value.read_row(row, floor_pct)` does the median and the pooling, `value.floor_pct_at` gives the floor of a segment; `value_at`, `value.lines` and `publish._curve` read through it. Constants `MONOTONE_LINES` (on) and `MONOTONE_MEDIAN` (5) in `value.py`; no setting, because the settings sensor has no room for one.
- Cause found by running one day through 0.9.113 to 0.9.144 and the profiles: the value curve is smooth with the defaults profile and rough with the beta profiles' `late_events` off (91p of total rise per curve alone, 315p with the level curves on, 15p with `late_events` back on). `late_events` is not changed here. See `docs/plans/engine-v2-level-penalty.md`.
- Tests: `tests/test_stored_value_smooth.py`, an interpolation test in `tests/test_level_penalty.py`.

## 0.9.145 (beta)

### Behaviour changes

- **Engine 2.2 beta no longer charges the battery from the grid at the peak rate.** A new setting, "Highest price to charge at" (`charge_max_import_p`), is 15p in the 2.2 beta profile and off (0) everywhere else. While the import price is above it, grid charging is not allowed, in the plan and in the engine alike. A smart slot that may still happen counts at its own, cheaper price, so a slot you might get is still planned for. Free power, grid events and manual overrides are not held back; while the car charges at a dear rate the battery just holds. Why: the plan for 15 Oct showed several charges at 28.84p, some straight after selling at 15p, because the value the plan puts on a stored kWh near the level bands is about the price of a peak-rate kWh, so buying looked like a tie. On the 7-13 Oct days in the simulator this cut charging at dear rates from 39 level points to 3 and the week's adjusted cost by £1.44, for about 20 more mode changes (225 against 205) and six more flip-flops (23 against 17). One cost: the battery is no longer topped up at the peak rate ahead of a late grid event.
- **Shorter help texts on the engine settings.** The settings sensor was within a few bytes of its size limit, so about twenty help texts are a little shorter. Nothing else about them changed.

### Behind the scenes

- `rules._core` drops Charge from the allowed modes when `price_p` (from `rules._cheapest_p`: the segment's import price, or its smart-slot price when a slot may happen) is above the setting, for the normal rule only. `limits_now` and `limits_for` both pass it, so the plan and the engine agree.
- Not in this release: the part-way charge or sale still rests in Hold where Hold is not allowed, which makes the value curve jittery (backlog: "Noise in the value curve"). Making the rest follow the allowed modes cleaned the curve but was £0.73 dearer over the week in the simulator, so it waits for a further look.

## 0.9.144 (beta)

### Behaviour changes

- **The battery graph follows the plan as the engine runs it.** Since 0.9.141 the mode strip shows each charge or sale pulled forward, starting as soon as the one before it is done, but the battery line was still drawn on the plan's own clock, with every run ending at the end of its segment. Later in the day the two were up to an hour apart: the strip said the battery was waiting on self-use, while the line still showed it selling (14 Oct, 21:52). The line, and the likely-range lines around it, are now re-timed the same way as the strip. This changes only what is drawn: the plan, its levels and what the engine does are as in 0.9.143.

### Behind the scenes

- `publish._path_as_run` maps each path point through the anchors `_as_run` records (shown time to the plan's time). The engine's own comparison of the level against the plan still reads the unchanged `vr.path`.

## 0.9.143 (beta)

### Behaviour changes

- **A smart slot is still a chance on a day the dispatch covers entirely.** EDF shows a half-hour at the cheap rate as soon as a dispatch is planned, so on 14 Oct, with a dispatch from 15:00 to 03:00, every half-hour of the day read 6.66p. The engine finds the standard rate a slot replaces by taking the day's highest rate, and that day had none: nothing counted as a smart slot that might not come, so the plan priced the whole window at the cheap rate as certain, and the forecast stopped dashing it. The standard rate now falls back to the highest rate known on the other days (tomorrow's 28.84p), so the half-hours still to come are a chance again (priced at the expected price, shown dashed), as on the days that still had a dearer half-hour. This also changes the plan on such days: the cheap energy is worth a little less, so the battery's arbitrage band can move.

### Behind the scenes

- `forecast._standard_rate` replaces `_day_max` for the standard rate; the day's own highest rate is used whenever it is above the slot's price, so days with a dearer half-hour are unchanged.

## 0.9.142 (beta)

### Behaviour changes

- **A charge or a sale now runs a minimum length, taken from your level bands.** In the 2.1 and 2.2 beta profiles the top band starts at 90% and the bottom at 70%, so a charge or a sale now runs the 20 points between them before the engine may stop it or turn it round. Near the edge of a band it runs only as far as the band: a charge that starts at 85% needs to reach only 90%, and the sale after it then runs the full 20 points. This is meant to end the bursts of short charges and sales near the top of the battery (13 Oct, 16:30 and 21:40), where each action ended on the far side of the steep drop in value between about 90% and 93% and the next re-plan turned it round. The plan carries the same rule: a charge or sale that is running keeps its minimum in the plan's first steps, and the plan's later charges and sales are walked with it, so the plan and the engine agree. Safety limits, forced modes (free power, events, overrides) and urgent events still end a leg at once. There is no new setting: it applies wherever both level bands are on (the top and bottom rates above zero), and not at all where they are off.
- On the seven days 7 to 13 Oct, replayed with the same prices: mode changes fell from 293 to 205, flip-flops from 36 to 17, and legs shorter than 3 points from 19 to 4, at a cost of about 1p over the week. The battery did not reach 100%. This replay does not reproduce the 21:40 burst, so that one is the thing to watch live.

### Behind the scenes

- `value.leg_band` and `value.leg_need` give the rule; `_walk` and `solve(leg_from=...)` carry it in the plan, and `Executor.leg_from` (saved in the state) holds the live leg. The backward value pass is unchanged: the rule is applied when the plan is walked forward, not priced into the value curve.

## 0.9.141 (beta)

### Behaviour changes

- **The forecast shows the plan as the engine runs it.** The plan ends a charge or sale part-way through a half-hour and fills the rest with a "hold", which is only a way of saying how much; the engine never rests there. It starts the next step as soon as the run is done, wherever the prices are the same. The forecast now draws it that way: a charge that stopped, "held" and charged again is one charge, and the sale after it follows at once. Where a price change makes the engine wait for the clock, the wait is shown as self-use when Hold is only for the car and the reserve (the 2.2 beta), and as a hold only where Hold is allowed. This changes only what is drawn: the plan, the levels it expects and what the engine does are exactly as in 0.9.140.

### Behind the scenes

- `publish.as_run` re-times the timeline items for `sensor.pe_v2_timeline` (remainder items dropped, the next step pulled forward where `Executor._same_prices` says the prices match, a gap filled with self-use or hold). The attribute shape is unchanged.

## 0.9.140 (beta)

### Behaviour changes

- **The 2.2 beta levies now apply to the plan's own choices.** Turning a charge into a sale (or the reverse), or restarting a charge or sale that ended in the last 10 minutes, now has to clear the margin even when it is the plan that asks for it. Before, only the live price comparison was held back, so a re-plan (a smart-slot change, for example) could turn a run round a couple of minutes after it began. In the simulator this cut flip-flops from 20 to 13 on real days and from 15 to 8 on synthetic ones, at a cost inside the noise. It only applies where the levies are above 0 ("2.2 beta"); other profiles are unchanged.
- **The history shows what happened to the second.** The Engine v2 history and the plan chart's hours already run now draw each mode where it started and ended, with the battery level where each change happened, instead of the mode that ran longest in each half-hour. Prices and the value of a stored kWh stay by half-hour. Needs this card release to be drawn (an older card keeps the half-hours).
- **Every journal row names the settings profile in use**, so a mode can be tied to the profile that chose it.

### Behind the scenes

- `sensor.pe_v2_history` and `sensor.pe_v2_recent` gain `runs`: `[at, mode, level at the start, 1 if only previewed]` per mode change, and `runs_until` (empty for today, where the card's clock ends the last run). The history file's `changes` now carry `level` and `preview`. The history sensor can reach 15,000 bytes on a very busy day (the limit is 16,000).
- `execute._inertia_ok`, called from `_pick_now`; `EngineV2.profile`.

## 0.9.139 (beta)

### Behaviour changes

- **A new engine v2 profile, "2.2 beta".** Choose it on the Engine v2 tab to try it; nobody is moved to it. It is "2.1 beta" plus five settings. Every other profile, and the default, behaves as before (the new settings are off or neutral there).
  - **Hold only for the car and the reserve.** The battery no longer holds to wait for a price or to fill out part of a half-hour. Hold is used while the car is charging and when the battery is at its reserve. Everywhere else it charges, sells or covers the house (Self-use).
  - **Free power may sell as well as charge.** During a free-power session the battery may sell what it holds, as the plan decides, instead of only charging to 100%.
  - **Two levies against flip-flopping.** Turning a charge into a sale (or the reverse) needs the price 4p/kWh further past the line, and so does restarting a charge or sale that ended in the last 10 minutes. Carrying on with a run costs nothing. Both are settings you can change.
  - **A width for stopping part-way.** A charge or sale may stop anywhere whose expected cost is within 0.5p of the best stop. A new setting, "Corridor width".
- **The plan executor follows the plan by level** (engine v2 with "How the mode is chosen" set to Plan: "2.1 beta" and "2.2 beta"). A charge or sale runs until its battery level reaches the edge of its range, however early or late that is against the plan's clock, and a run that reached its edge is not restarted by the same plan. The battery's expected range during a run is the run's whole range, so a battery a little ahead of or behind the plan no longer asks for a re-plan. In the simulator this cut re-plans by about 40% for the same cost. This changes "2.1 beta" too.
- **The engine v2 settings list is leaner.** A setting with no unit or that is a plain number no longer says so in the settings sensor, and a few help texts are shorter, so the five new settings fit under Home Assistant's size limit. The card shows the same page.

### Behind the scenes

- New settings: `hold_only_car_reserve`, `free_arbitrage`, `reversal_levy_p`, `reentry_levy_p`, `corridor_cost_p`. `value.CORRIDOR_COST_P` is now the setting.
- `sensor.pe_diag_v2_settings` rows leave out `unit` when it is empty and `kind` when it is `number` (the card reads a missing one as no unit and a number). The sensor is under 15,000 bytes.
- The simulator's Hold now sells spare sun, as the real inverter does (it let the sun charge the battery), and its scoreboard splits Hold at a target into waiting for a price, car and idle. Cached simulator results from before are not reused.
- Plan: `docs/plans/engine-v2-2-beta.md`.

## 0.9.138 (beta)

### Behaviour changes

- **Costs and history now say which settings profile engine v2 ran on.** Each half-hour cost record from now on carries the engine v2 profile in use ("defaults", "2.1 beta" or one of yours). The Engine v2 history, the plan history's "in control" and the nightly same-day comparison show it next to "engine v2", and a day where you changed profile is marked "mixed". Days recorded before this release have no profile and show as before. Costs themselves are unchanged: they were always worked out from what the inverter did.
- **The engine badge names the profile.** The badge on the Engine v2 tab reads "Engine v2 · 2.1 beta" while engine v2 is the engine in use.
- **The settings section "Plan-following and battery limits"** no longer says "(2.1 beta)" in its title, since any profile can use it.
- **The diagnostics export and `tools/diag_summary.py`** record and print the profile in use ("engine in use: v2 (profile 2.1 beta)").

### Behind the scenes

- `costbook.add(profile=...)` writes `profile` on each record; `day_engine_info` returns `profile` for a day engine v2 ran (the name, or `mixed`), and the plan history, the engine v2 history and the comparison rows carry it as `profile`. `sensor.pe_state_engine` has a `profile` attribute while engine v2 is the engine. No attribute is near the 16 KB limit. Nothing in control or planning changed, so the replay is unchanged.

## 0.9.137 (beta)

### Behaviour changes

- **Engine 2.1 beta is now a settings profile of engine v2, not a third engine.** Under "Planning engine" there are two choices again, engine v1 and engine v2. Engine v2 has a **Settings profile** box: "defaults" (engine v2's own starting values), "2.1 beta" (the plan-following set from 0.9.136) and any profiles you save yourself. If you were on engine 2.1 beta, you are now on engine v2 with the "2.1 beta" profile, and the engine behaves exactly as it did.
- **Loading a profile replaces the tuning settings.** Before, a value you had saved (for example "Grid charging above the comfort band" at 5p) silently beat the beta's value, so switching to 2.1 beta needed a manual fix. Now loading a profile drops the changes you made on top of the previous one, so the engine runs exactly the profile's numbers. Your reserve, the comfort band's edges, what the engine may do (selling, grid events, free power) and the wear and grid-event figures belong to your house, not the profile, and are never changed by loading one.
- **Save your own.** Change settings on the page, then "Save as a new profile…" (up to 10), "Save changes to" a profile of yours, "Revert" to the profile in use, or delete one. A saved profile keeps only what differs from engine v2's defaults, so later improvements to a default still reach you for settings you did not tune. Loading, saving and deleting send only the profile change; other unsaved edits on the page stay unsaved.

### Behind the scenes

- New config keys `v2_profile` (the profile in use) and `v2_profiles` (yours). A profile gives a value wherever `engine_v2:` does not set one, before the v1-seeded values. `system.engine: v2_1` is still accepted and means engine v2 with the "2.1 beta" profile (unless `v2_profile` is set); `sensor.pe_state_engine` still says `choice: v2_1` while that profile is in use.
- New sensor `sensor.pe_diag_v2_profiles` (state: the profile in use; attributes: built-in profiles with their values, your profile names, the house-only settings a profile never holds). It is separate because the settings sensor is at 14.9 KB of the 16 KB limit. `sensor.pe_diag_v2_settings` `engines` is now `["v1", "v2"]`.
- `MIN_CARD_VERSION` is 0.9.137: an older card does not know the new config keys and would drop them on its next save. Replay and engine behaviour are unchanged.

## 0.9.136 (beta)

### Behaviour changes

- **Engine 2.1 beta, a third choice for the planning engine.** On the config page, under "Planning engine", you can now pick "Engine 2.1 beta". Nothing changes unless you do: engine v1 and engine v2 behave exactly as before. It is engine v2 with a different way of deciding and two new limits:
  - **It follows its own plan.** Each tick it takes the mode the plan has for now (charge, sell, hold, self-use), instead of working it out again from the live prices with a stack of rules on top. It works the values out again as soon as a charge or sale reaches its target. The limits, grid events, free power, the car and your reserve still apply. On the test days this made the day-to-day choices much steadier than engine v2 without those rules.
  - **A smooth cost keeps the battery off the very top and bottom.** Each kWh held above 90% (and below 70%) costs a rate per hour that doubles every few points, so the plan finds its own limit: about 94 to 96% at the top, and a cushion above your reserve at the bottom. There is no rule for it.
  - **A sharper bottom limit inside a smart slot.** A smart slot can be cut short, so inside one the bottom cost is much steeper (it starts at 80%): the plan no longer sells the battery down counting on the slot to refill it. On a test day where the afternoon slot was withdrawn at 12:05, engine v2 ran flat and held at the reserve for over four hours at the 28.84p rate; engine 2.1 beta stayed above about 33% through the afternoon and evening.
- **It uses engine v2's settings page.** Engine v2 and engine 2.1 beta share one set of settings. Ten new settings are on that page (section "Plan-following and battery limits"). Under engine 2.1 beta, any setting you have not saved yourself starts from the values it was tested with; anything you have saved is kept. **If you saved a "Grid charging above the comfort band" cost, it stays**: the beta was tested with it at 0 (the top limit does that job), so you may want to set it to 0. Switching back to engine v2 puts every setting you did not save back to engine v2's own starting value.
- **What was and wasn't tested.** Seven simulated days (three real days, four made-up ones: sunny, cloudy with a grid event, and two with a smart slot withdrawn, one also with a grid event). Over them it came out about 9% cheaper than engine v2, but it makes about two and a half times as many mode changes (about 39 commands a day against 14), and holds the battery more often by day, mostly to export spare sun instead of storing it. Late-event insurance and "prefer Self-use" start off in the beta, as in all the tests. It is a beta: watch it for a few days (the Health tab and the history show every mode change) before you rely on it.

### Behind the scenes

- `system.engine` accepts `v2_1`. The app treats it as engine v2 everywhere (`sensor.pe_state_engine` still says `v2`, with a new attribute `choice`); a settings profile ("2.1") fills in the starting values for the keys the config does not set. `sensor.pe_diag_v2_settings` has a new attribute `engines` (the choices the app offers), and shows the beta's starting values as the defaults while it is in use. The card needs 0.9.136 to offer the choice; an older card still works with the app.
- Eleven new engine v2 settings: `executor` (`lines` or `plan`), `replan_on_level`, `level_top_*`, `level_bot_*`, `slot_bot_*`. All default to off for engine v2, so its decisions and the whole-app replay are unchanged. The bottom curve's rate is set at your reserve level.
- Some help texts on the engine v2 settings were shortened so the settings sensor stays under the 15 KB limit (it is 14,987 bytes).

## 0.9.135 (beta)

### Behaviour changes

- **A "Re-plan now" button under the expected timeline.** Pressing it makes Engine v2 work its values out again from the live readings straight away, and the card says "Re-planning..." and then "Re-planned at 07:41" once the new plan has arrived, so you can tell the plan on screen is the latest one. It changes nothing else: Active, Passive and Pause stay as they are. Only a Home Assistant administrator can press it (the same as the other card buttons that ask the app to do something).

### Behind the scenes

- New event `pe_v2_replan` and a `replan` trigger ("You asked for a re-plan"); it clears the cached solar forecast and learned figures before it asks for the new values. Needs card 0.9.135.

## 0.9.134 (beta)

### Behaviour changes

- **The forecast price for a smart slot is the rate EDF offers.** A smart slot that was only a chance (not yet started) was published at the normal price (28.84p) with the offered price beside it, so the dotted forecast price showed the high rate through a window EDF had offered at 6.66p. Every offered slot is now published at its offered price, whether or not the plan counts on it; the plan itself is unchanged (it still prices the chance as one expected price). The normal rate it replaces is published beside it as `standard_p`.

### Behind the scenes

- `publish._prices`: `import_p` is the slot price for any offered slot; new additive field `standard_p`. No shape change to the sensors, so no card version change.

## 0.9.133 (beta)

### Behaviour changes

- **Smart-slot certainty is now a setting, 60% by default, and the plan prices an uncertain slot as one expected price.** Engine v2 no longer learns how likely a smart slot is. Outside the overnight window a slot counts as 60% likely (new setting "Smart slot certainty"); inside the overnight window, and for the half-hour already running, it is certain. The plan prices the slot at that chance of the slot price plus the rest at the normal price, so it is cautious by itself: at 60% that is about 16p instead of 7p, so it still charges when the battery is low before the evening peak but no longer sells the battery down to refill in a slot it cannot rely on.
- **Fixed the plan cycling on a smart-slot window (from 0.9.132).** The later half-hours of a window were each an independent chance, so a long window at 51% looked like a near-certain cheap half-hour and the plan sold and bought back through the evening, showing "charge at 6.66p" in the 28.84p evening. That is gone: no per-half-hour chances at all.
- **"This ends when" no longer promises a level the power cannot reach.** A sale to 32% from 81% showed "reaches 32% at 18:00" because the plan's step for it ended then. The time is now never earlier than the power allows (nearly two hours for that sale).

### Behind the scenes

- `value._make` prices an uncertain slot at its expected price; `Executor._finish`. The learned certainty still feeds the v1 planner and the statistics. The new setting needs adding to the card's config page (until then it keeps its default).

## 0.9.132 (beta)

### Behaviour changes

- **The plan no longer treats the rest of a running smart slot as certain.** Once an EDF smart-charge window had started, every half-hour still to come in it was counted as certain, so the plan sold the battery down hard on the strength of a window that can be cut short or re-listed (9 Oct: 89% down to 31% in an afternoon window). The half-hour in progress is still certain, because its price is on the tariff now. The later ones now carry a chance: the share of later half-hours that past windows really kept. The plan prices them like any other uncertain slot (the slot price with that chance, the normal price otherwise), so it sells less deeply when windows often shrink, and goes as deep as before when they rarely do. No new setting, no new rule.
- The figure is shown as `running` in the smart-slot certainty summary (plan sensor and diagnostics export), so you can see what the plan used.

### Behind the scenes

- `Certainty.hold()` and a new `slot_hold` input to the engine 2 forecast; the "started window is certain" shortcut in `forecast.py` is gone. Added to the grid-events backlog: a weather and wind outlook could feed both late-event and smart-slot certainty.

## 0.9.131 (beta)

### Behaviour changes

None. This release is for the card only: nothing about planning, control or the app's sensors changes.

## 0.9.130 (beta)

### Behaviour changes

None. This release is for the card only: nothing about planning, control or the app's sensors changes.

## 0.9.129 (beta)

### Behaviour changes

- **The plan no longer rests in Hold between a charge and a sale.** In a cheap overnight window where buying at 6.66p and selling at 15p both pay, the plan sometimes held the battery for a whole half-hour between a charge and a sale, losing that half-hour's profit. The cause was how changes were priced: turning a charge straight into a sale cost 3p, but going charge, hold, sale cost only 0.6p, so the plan took the cheaper route through Hold wherever the gain was small.
  - **One cost for every change.** Every change of mode now costs the same, so going through Hold is never cheaper than turning round directly. The separate "Cost of reversing" setting is gone, and "Cost of a mode change" now defaults to 2p (it was 0.5p). A saved reversal setting is ignored.
  - **Tested before release** on the closed-loop evening simulation (fewer mode changes, fewer holds, no reversals or flip-flops; cash within 2.4p of before) and on the four demo days (same saving, same flip-flops). A cost of 1p let charge-to-sale turns through, so 2p is the lowest that holds.
  - **Charge and sale cycling is unchanged.** Short rests of a few minutes remain where a charge or sale reaches its level part-way through a half-hour, which costs almost nothing.
  - **The "does this change pay for itself" check is stricter** (it uses the same cost), so changes the plan did not ask for, such as spare-sun toggles between Self-use and Hold, need a bigger gain. Watch for any case where a change comes late.
  - **One small difference:** in the evening simulation the battery tops out at 91.7% before the last charge, against 91.2% before (the comfort top is 90%, and 100% before the 0.9.113 top-up cost). That is a few tenths of a kWh above the top, costing about 1p.

## 0.9.128 (beta)

### Behaviour changes

None. This release is for the card only: nothing about planning, control or the app's sensors changes.

## 0.9.127 (beta)

### Behaviour changes

- **The plan now follows a sale that is already running.** When the battery was selling and the plan would carry on selling, the plan could still open with "charge now" (and the status text said "Grid-charge to 81%") while the battery kept exporting. The plan is now worked out knowing which mode is running, so its first step matches what the battery is doing, then it recharges and sells again as before.
- **Fewer recalculations of the plan.** The "battery had been outside its expected range for 10 minutes" recalculation fired every ten minutes during such a sale, because the plan expected the level to rise while it fell. With the plan and the battery agreeing, those should stop.
- Nothing else about planning changes: from charge, hold or self-use, or with nothing running, the plan is made exactly as before. No change to control, inverter writes or the card.

## 0.9.126 (beta)

### Behaviour changes

- **The plan chart on the engine card now shows the last 18 hours as well as the forecast.** It opens a few hours before now and scrolls back to 18 hours, showing the modes, battery level and import price as they actually ran, shaded to the left of the "now" line. The percentage and price scales stay in view while you scroll. This needs the new card (0.9.126); with an older card nothing changes on the chart.
- **The "now" line on that chart no longer goes stale.** It used to sit where PowerEngine last worked out the plan, because the card only redrew when the plan changed. It now follows the clock and the chart redraws about once a minute while it is on screen.
- New sensor `sensor.pe_v2_recent` carries those 18 hours (made from the records engine v2 already keeps; nothing new is recorded, and it does not depend on the day picked on the history page).
- Display only: nothing about planning, control or inverter writes changes.

## 0.9.125 (beta)

### Behaviour changes

- **While the car is charging and the battery is held, the status now says "until the car stops charging".** It used to say "until the price changes to 6.66p at 23:00", although the battery is held because the car is charging and stays held past 23:00 if the car is still going. "The car stops charging" is now the first line under "This ends when" and in the subtitle; the price change stays in the list below it, since a worthwhile charge can still start then.
- Display only: when the battery is held, and for how long, is decided exactly as before.

## 0.9.124 (beta)

### Behaviour changes

- **The plan's import price line now shows the smart-charge slot price while a slot is running.** When a slot had started (for example the car plugged in and the house on 6.66p), the dotted import line on the expected timeline still showed the standard rate (28.84p), with the slot price only held in the detail, so the "now" line crossed the high price. A slot that has started is now drawn at its own price. A slot that has not started yet is unchanged: it stays at the standard rate with the chance and slot price beside it.
- Display only: the plan and the decisions use the same prices as before.
- **The "Charging at" box on the Engine v2 page now shows a power.** It read the power the mode asks for, which was only filled in when something limited it (the battery's own limit or the cold caution), so with no limit the box always showed "–". It now shows the full rate that is being asked for (and "Exporting at" the full export rate). Display only: what is sent to the inverter is unchanged.

## 0.9.123 (beta)

### Behaviour changes

- **The charge slowdown near full is now learned from the battery's own charge limit.** The battery reduces the power it accepts as it fills (for example 2.4 kW at 96% and 1.2 kW at 99%, against about 5 kW lower down). PowerEngine already tried to learn this, but only from half-hours in which it had asked for full power from 90% or 95%. It rarely asks for that, so "Charge taper near full" stayed empty and plans assumed full power all the way to the top.
  - From now on, each cycle records the battery's charge limit against its level from 80% upwards, and the taper is learned from that in six steps (from 85, 90, 93, 95, 97 and 99%). A step needs a few readings before it is used, and a step with no real slowdown is left out.
  - It is used by both engines (the planner and engine v2), so the last few percent of a charge are planned at the speed they really take, and the expected timeline stops assuming a fast finish.
  - The first learning needs a day or two of readings to fill the steps. The learned row on the Settings page shows what was found. Setups without a charge-limit sensor keep the old method.
  - Switch "Use the learned charge and discharge tapers" off to ignore it.

## 0.9.122 (beta)

### Behaviour changes

- **The end of the expected timeline no longer shows a heavy discharge.** Engine v2 plans over a look-ahead, and what was left in the battery at its end was valued as if it could always be bought back at the cheap rate. When the look-ahead ended inside the cheap window (8 Oct: 03:45, with the window running to 06:00), the last steps sold the battery from 63% to 25%, although there was not time left to refill it and the house needs that energy in the dear stretch that follows. 0.9.119 fixed only the case where the look-ahead ends just after the dear rate begins. This is the same fault at its root, fixed in two parts:
  - **The plan is always worked out over 48 hours, and the timeline shows the first 36.** The last hours, where an end effect is most likely, are worked out but not shown, and they have little pull on what happens now. A plan takes about 30% longer to work out (about 1.7 s to about 2.3 s on your system).
  - **Energy left at the end is now valued by what happens next, not at one price.** The engine reads how much could still be bought back before the dear stretch begins and how much the house takes from the battery in it. Below the level that keeps that much, a kWh is worth the dear stretch's price; above it, it is worth the cheap refill price (or a sale, if nothing can be refilled). This replaces the 0.9.119 rule, which is now just one case of it. A flat tariff, or an end value set to Fixed, behaves as before.
- What the engine does right now changes little: the end of the plan was already being re-planned as the look-ahead rolled forward. The replay of a recorded night is unchanged.
- The setting's help text for "Energy left at the end of the look-ahead" says the same.

## 0.9.121 (beta)

### Behaviour changes

None to how PowerEngine controls the battery. This release only changes what the Costs page shows.

- **New fold-out under "Where the savings came from": how the PowerEngine saving was made.** The chart's PowerEngine step is the battery and PowerEngine together, so on a day that ran down energy bought earlier it can look much bigger than PowerEngine's own part. The fold-out (closed until you open it) splits that step into three parts that add up to it: energy carried in from an earlier day, what a plain self-use battery would have saved, and what PowerEngine's timing added on top. It has a day-by-day table for the last 7 days with the battery's kWh in and out. For 7 Oct, the £2.98 was £0.70 carried in, £1.96 from the battery itself and £0.32 from PowerEngine's timing.
- The fold-out explains why "Engines compared, same day" gives a different figure for the same day: that is a replay from the morning's forecast with a simulated battery, so use it to compare engines, and the new table for what your meters saw.
- The chart, the headline and the numbers are unchanged.

## 0.9.120 (beta)

### Behaviour changes

- **The savings chart no longer charges ordinary use to Axle.** Axle's step now holds only what Axle really exported: the export payment plus its £1/kWh. Before, a whole half-hour counted as Axle if an event touched any part of it, so the house load, car charging or battery charging bought in the same half-hour was billed to Axle with no income against it. On 6 Oct that made Axle look like a £0.27 cost for 0.12 kWh exported. That ordinary use now stays in the Tariff and PowerEngine steps. "You paid" is unchanged; only the split between steps moves.
- **Past days are re-valued** the first time this version starts, so the Costs page, the week, the month and the 30 days all use the new split. Free-power half-hours are treated as before (kept out of the everyday figures as a whole).
- The chart and Costs page explanations say the same.
- **New setting, on by default: stop the car charger during grid events.** While an Axle event is running, PowerEngine sets the car charger (the Zappi's charge mode) to Stopped, and when the event ends it puts the mode back to what it was (Eco+, for example). On 6 Oct an EDF smart-charge slot ran through an event: the car took 7.2 kW, the battery's 5 kW went into the car and the house, and the event exported only 0.12 kWh. Switch it off under Grid events on the Config page ("Stop the car charger during grid events"). It acts only in Active mode, needs the Charger mode entity mapped, never touches a charger that was already stopped, and leaves a mode you change yourself during the event alone. Stopping the charger can cost EDF's smart slot for that half-hour: it will show as unused.

## 0.9.119 (beta)

### Behaviour changes

- **Engine v2 no longer plans to empty the battery at the very end of its look-ahead.** Energy left at the end of the look-ahead was valued at the cheapest import price (a cheap refill). When the look-ahead ended just after the dear rate began (for example half an hour into the 28.84p peak), nothing could be refilled at that point, so the last steps sold the battery down to the floor. Such leftover energy is now worth at least what a sale brings. A look-ahead that ends inside the cheap price is valued as before.
- This mostly shows in the timeline and plan far ahead; as the look-ahead rolls forward the end moves, so it affects what the engine would have done only slightly, and the replay of a recorded night is unchanged.

## 0.9.118 (beta)

### Behaviour changes

None. This release only changes the Engine v2 page in the card; PowerEngine's planning and control are exactly as in 0.9.117.

### Other

- The card has two changes on the Engine v2 page (see the card notes). No action is needed from you beyond updating the card.

## 0.9.117 (beta)

### Behaviour changes

None. This release only changes the Engine v2 page in the card; PowerEngine's planning and control are exactly as in 0.9.116.

### Other

- The card has two changes on the Engine v2 page (see the card notes). No action is needed from you beyond updating the card.

## 0.9.116 (beta)

### Behaviour changes

None. This release changes only how releases are built; PowerEngine's planning and control are exactly as in 0.9.115.

### Other

- The project's release process now tests once and then releases (before, the same tests ran several times), so releases are quicker. No action is needed from you.

## 0.9.115 (beta)

### Behaviour changes

- **Engine v2 prefers Self-use to Hold by day (new setting, on by default: "Prefer Self-use to Hold by day").** Outside cheap-rate
  times (the overnight window and smart slots) the battery runs the house and stores spare sun, instead of holding the battery and exporting
  the sun at the export rate. Hold is kept for cheap-rate times, the car, the reserve and manual overrides. This gives up a small gain on sunny
  days (selling spare sun at the export rate and refilling at the night rate) in return for a plan that matches how you expect the house to run,
  and the overnight plan fills the battery to 100% more often. Switch it off on the Engine v2 settings to get the earlier behaviour. Engine v1 is unchanged.

### New

- **Engine v2 publishes the sun and the house load its plan was made with.** The expected-timeline sensor (`sensor.pe_v2_timeline`) gains a
  `sun` attribute: average kW for each half hour, with the low, middle and high sun forecast and the expected house load. The Engine v2 page's
  chart draws it (needs the matching card).
- **The diagnostics export's `engine_v2` section now also holds** the mode, value, value curve and learning sensors' attributes.
- **Engine v2 can plan for a grid event that is announced at short notice (new setting, on by default: "Be prepared for a late grid event").**
  The plan allows a small chance, in every future half hour, that a grid event is running although none is announced yet (default: 1.5 a week,
  each lasting an hour; both are settings). Energy in the battery is then worth a little more at those times, so the plan keeps more in the
  battery and fills it nearer the end of the cheap rate. It is a plan assumption only: nothing is sold unless an event is really announced.
  Needs grid events on. The calculation takes about twice as long.

## 0.9.114 (beta)

### Behaviour changes

- **A blip of a sensor no longer switches the inverter's remote control off.** If a required input (on 7 October, your
  battery charging and discharging power sensors, at every 5-minute check overnight) reads "not ready", remote control
  now carries on for up to 90 seconds instead of being switched Off at once and back on 27 seconds later. If the inputs
  return inside that time nothing is written. If they don't, remote control goes Off as before. Pause, Passive, a
  tripped guard and a configuration error still switch it Off straight away. The wait is shorter when the RAM refresh
  interval is long (the refresh interval plus the wait stays under 4 minutes, so there is none at the 4-minute maximum),
  and the inverter's own failsafe (about 5 minutes) still returns it to Self-use if PowerEngine itself stopped.
- Engine v1 and engine v2 decisions are unchanged.

### Fixes

- **The forecast records that the nightly engine comparison depends on now fit a real day.** The first build capped a
  day's record at 1 MB, but a real day is 1 to 1.5 MB (the supplier's smart-slot list alone is 13 KB and changes about 40
  times a day), so the recording would have stopped in the evening and lost late smart slots and the next day's rates.
  The limits are now 3 MB (slower recording past it) and 5 MB (stop).
- **"Inputs not ready" now says why**, for example "battery_charge_power: Entity is unavailable (sensor.x)", so a
  diagnostics export shows the cause, not only the names.

## 0.9.113 (beta)

### Behaviour changes

- **Engine v1: none.**
- **Engine v2 keeps its overnight cycling under your top (90%), and still fills before the morning.** A kWh charged from
  the grid above the comfort band's top now counts as costing 5p in its plan (new setting "Grid charging above the
  comfort band", in the Comfort band group). Cycling above the top pays that every time while the same cycle below it
  earns the same, so the cycle stays under 90%; the last charge before the cheap slot ends saves far more than 5p (about
  23p a kWh against the morning rate), so it still fills to 100%. Sun is not charged it. The comfort band's upper edge
  starts from your v1 arbitrage top until you save engine v2's settings.
- **Engine v2 no longer flips between charging and selling every minute or two.** Reversing direction (charge to sale
  or back) now has its own cost, 3p (new setting "Cost of reversing", in Responsiveness), and when buying and selling
  both pay, a charge or sale runs to the end of its planned step instead of turning round at the next recalculation.
  On a replay of 6 October's evening: 8 mode changes instead of 22, no charge or sale turned round in under 25 minutes
  (was 13), at most 90% until the last charge, 100% when the slot ended, about 7p dearer on the night (the price of the
  soft top).
- **Engine v2 recalculates less often after learning:** only when what it learned moved by 1% or more (or a whole day
  of house profile), not on every small change.
- On the four recorded demo days engine v2's adjusted saving went from £25.52 to £26.44 (v1 £24.04, best possible
  £27.23), with no charge/sale reversals left. Brief self-use/hold toggles on spare sun and sales pausing at the sell
  line remain (to look at next).

### Fixes

- The Active / Passive badge no longer slides under the card below it on a phone (card).

## 0.9.112 (beta)

### Behaviour changes

- None. This release carries a card fix only.

### Fixes

- The Engine v1 and Engine v2 tab icons now show their 1 and 2 (card fix; see the card's notes).

## 0.9.111 (beta)

### Behaviour changes

- **A configuration save is answered at once.** PowerEngine used to reply to the card only after reloading everything
  (checking inputs, rewriting the dashboard, a full cycle), which on a slower host, or while engine v2 was working out its
  values, could take longer than the card waited, so switching engine showed "No reply from PowerEngine" although the
  switch had worked. It now replies as soon as the file is saved, then reloads. Nothing else about saving changes.

### Fixes

- The card now draws the Engine v1 and Engine v2 tab icons (card fix; see the card's notes).

## 0.9.110 (beta)

### Behaviour changes

- **None to how either engine plans or controls the battery.**
- **The Plan page is now "Engine v1"**, and both engine pages have their own icon (an engine with 1 or 2 on it) and a badge at the
  top: **Active** (sending commands), **Paused**, or **Passive** (working out what it would do; nothing is sent).
- **Plan history has moved** to the bottom of the Engine v1 page (the separate tab is gone). Its "As run" plan is now kept only while
  engine v1 is in control; on a day engine v2 ran it shows v1's plan from the start of the day, labelled as passive.

### New

- **Engine v2 history** at the bottom of the Engine v2 page: for any of the last 30 days, the battery level as it ran against the level
  expected at the start of the day, the modes as they ran, prices, the value of a stored kWh, and each mode change with its reason.
  Recorded in preview too (shown dimmed).
- **Engines compared, same day** on the Costs page. Each night at 03:20 PowerEngine replays yesterday through both engines in a
  simulated battery, with what really happened (house, car, sun, prices, smart slots, grid events) and the forecasts as they were at
  the time, and shows for the last 7 days what each engine would have saved against plain self-use, the best possible with perfect
  hindsight, the difference, and which engine was really in control. A calibration line shows how close the replay of the engine
  that ran came to your meter. It runs in the background (about 3 minutes a day on a fast machine, perhaps 15 on a Raspberry Pi)
  and can be switched off ("Engine comparison" on the Config page).
- **The first comparison appears the morning after the first full day on this version**: it needs a day of forecast records first.
  PowerEngine now keeps those records (forecasts, the rates known at the time, smart slots and when they were announced, grid events,
  the house profile) for 14 days, on your Home Assistant only.

### Other changes

- Each half-hour cost record notes which engine was in control and whether PowerEngine was live.
- Diagnostics export: sections `forecast_snapshots`, `v2_history` and `engine_compare`; `tools/diag_summary.py` prints them.
- The card needs to be 0.9.110 or newer (the dashboard uses its new cards and icons).

## 0.9.109 (beta)

### Behaviour changes

- **PowerEngine now looks after its own Home Assistant package files.** When it starts (so after every update), and when you change *Other battery controller* and save, PowerEngine writes or updates `powerengine_handover.yaml` in your Home Assistant `packages` folder, and `powerengine_predbat_handover.yaml` as well while the setting is Predbat. If you choose another setting it removes the Predbat file. The old file is kept beside it as `<name>.bak-<date>`. A file in that folder that PowerEngine did not write is never touched, and PowerEngine never creates the `packages` folder.
- Home Assistant only uses a changed package after a reload. PowerEngine notifies you when it has changed a file, and the new sensor `sensor.pe_diag_package` says whether a reload is needed (the Config page shows a **Load** button for it).
- To remove Predbat there is nothing to copy by hand any more: set *Other battery controller* to *No other battery controller*, save, press Load, then uninstall Predbat.
- If you copy the package files by hand today, nothing breaks: a file with PowerEngine's older headers (including the old single combined file) is replaced by the new one, with a backup. If you keep a git copy of your Home Assistant configuration, pull before you push, or the push will put the old file back.

### Other

- The package files are now shipped inside the app (`apps/powerengine/ha_packages/`), and start with a line saying PowerEngine manages them.
- The install guide's "Install the packages" and "Removing Predbat" sections are rewritten around this.

## 0.9.108 (beta)

### Behaviour changes

- **Predbat is now optional.** There is a new setting, **Other battery controller**, on the Config page under Inverter control: *No other battery controller*, *Predbat, switched over with the handover package*, or *Another controller*. With *No other battery controller*, PowerEngine no longer needs or checks the handover guards, and nothing on the dashboard, in the log or in notifications talks about Predbat. Active is then allowed as far as the guards go (it is still your choice to switch it on; PowerEngine never changes Active, Passive or Pause for you).
- If you have not chosen, nothing changes for you: PowerEngine works it out from the guards you already mapped (a Predbat read-only switch means Predbat, any other guard means another controller), and does not save anything. With no guards mapped and no choice, Active is still refused, and the reason now reads "Choose whether another battery controller is installed (Config page, Inverter control)".
- **The Home Assistant package is split in two.** `powerengine_handover.yaml` now holds only what every install needs (the Update button, the AppDaemon restarts and the watchdog) and no longer mentions Predbat. The Battery controller switch, the two handover scripts and the Predbat restart automation moved to a new optional file, `powerengine_predbat_handover.yaml`, for homes that run Predbat alongside. The watchdog now runs when PowerEngine reports Active or Paused, instead of when the Battery controller switch says PowerEngine.
- The handover scripts no longer switch off a list of personal automations. If you have automations of your own that must stay off, map them as *Other control off* guards and turn them off yourself.

### Other

- The install guide has a short "Removing Predbat" section.
- The version sensor now publishes `other_controller` (`none`, `predbat`, `other` or `unset`).

## 0.9.107 (beta)

### Behaviour changes

- **The plan no longer buys dear energy to fill a short cheap half-hour.** If the learned overnight window contained a lone half-hour (a smart-charge slot that happened to be cheap on every day learned), the optimiser charged the battery before it, to be full when it ended. On the demo "sunny" day that meant 6.7 kWh bought at 30.28p from 19:30, to be sold at 15p an hour later. The "full when the cheap window closes" penalty now applies only to a window long enough to refill the battery (or one already running when the plan is made). A normal overnight window is planned exactly as before.
- **Preview of engine v2 while engine v1 is in control.** Engine v1 still makes every decision. But engine v2 now works out, on your real house, what it would do and shows it on the dashboard's Engine v2 page, marked "Preview: engine v1 is in control. Nothing is sent." It never sends a command, never writes to the activity log and never changes what v1 does. It also learns from your house meanwhile. Turn it off with "Preview while engine v1 is in control" in the engine v2 settings (it is on by default).

## 0.9.106 (beta)

### Behaviour changes

- **None while you stay on engine v1** (the default). Everything v1 plans and sends is unchanged.
- **A second planning engine, engine v2, that you can choose on the Config page.** Instead of a plan of half-hours, it works
  out what a stored kWh is worth at every battery level over the next day and a half, and acts the moment a condition is
  met: the battery reaches the level where more charge isn't worth the price, a price changes, a smart slot starts at its
  real minute, the sun falls short of the forecast, the car starts. It doesn't wait for the next half-hour.
- **Engine v2 needs RAM remote control.** On timed windows it shows what it would do, and Active is refused with "Engine v2
  needs RAM remote control".
- **Switching engines doesn't change Active, Passive or Pause.** The inverter keeps its command until the other engine has
  decided (a few seconds). Switch back the same way.
- **On the four recorded demo days** (sunny, dull, grid event, car), run closed loop in the demo world, engine v2 saved
  £25.52 against plain self-use where engine v1 saved £21.82 (the best possible with perfect hindsight: £26.74), with 2
  or fewer back-and-forth changes a day on three days and 8 on the grid-event day. It hasn't run on a real house yet.

### New

- **Engine v2 page** on the dashboard (after Plan): what it is doing now and what ends it, the value of a stored kWh against
  today's prices, the expected timeline, the value map, and a health card (why it worked the values out, mode changes,
  flip-flops, the comfort band's cost, what it has learned).
- **Config page:** the engine choice at the top (it asks before switching, either way), then three groups: Your house
  (used by both engines), Engine v1 settings and Engine v2 settings. The engine not in use is dimmed but can be set up in
  advance.
- **Engine v2's own settings** start from your v1 values where there is one (selling, grid events, free power, reserve):
  a comfort band (20 to 90%, a soft guide with a small cost per hour outside it, never a hard limit), wear for the battery
  supplying the house and for sales (both 0p: the battery treated as a sunk cost), a switching cost (0.5p), the starting
  weights for the solar and house forecasts (learned from then on), and how quickly it reacts.
- **Battery's hard floor** (new, under Battery, 12%): the battery's own lowest level. Engine v2 never goes below it, not even
  for a grid event; grid events may go below your reserve. Engine v1 doesn't use it.
- **Diagnostics export:** a section `engine_v2` (its health and the last 300 decisions with the prices and values behind
  them).

### Other changes

- `tools/engine_compare.py` compares the two engines on the demo days.
- The card needs to be 0.9.106 or newer (the dashboard's new page uses its new cards).

## 0.9.105 (beta)

### Behaviour changes

- **Overnight sales and refills are planned again when the night is far off.** Two small tie-breakers in the optimiser (charge early, sell early) counted half-hours from now without a limit, so by tonight's refill they cost about 1p per kWh each and cancelled a cycle's margin (about 4p per kWh at a 79% round trip). No sale was planned and the battery sat on hold for hours at 90% (5 Oct 2026). They now count from the start of the overnight window and stop growing after 12 half-hours, so a night's sell and refill that pays is planned. Expect more overnight sales, and a changed evening: a sale may start earlier.
- **With remote control, an overnight switch costs the configured 0.5p, not 3p.** The *Overnight switch cost* setting forced every overnight change between charging and selling to cost at least 3p, whatever the remote-control switch cost said. It now applies only to inverters on timed windows, where a change is an EEPROM write. The setting's help says so.
- The overnight window itself is still learned from the rates by default, exactly as before (see New).

### New

- **You can fix the overnight window instead of learning it.** New setting **Overnight window** on the Config page, under *Tariff and planning*: **Learned from the rates (recommended)** or **Fixed times**. With Fixed times, set **Fixed overnight window: starts** and **ends** (hours since midnight in half-hour steps: 23.5 is 23:30, 5.5 is 05:30; a start after the end runs over midnight). Use it on a new install (the learned window needs two full days of rates), if a smart-charge slot at the same time every day has been learned as part of the window, or if the learned window looks wrong. It is one range for now.
- **The Config page shows what is in use and what has been learned.** The window in use and the one learned from the rates (and from how many days) appear next to the choice. They are also on the new diagnostic sensor `sensor.pe_diag_overnight`.
- Changing the window re-values past half-hours with the new window and remakes the plan at once. The plan also now remakes when any system setting changes.

## 0.9.104 (beta)

### Behaviour changes

- **A smart-charge slot now counts as used when the charger says the car was charging for 2 minutes without a break, not when it drew 0.2 kWh.** The car waking up and probing for a few seconds at a time (0.01 kWh, 4 Oct 2026) no longer counts, however many times it happens, and a short real top-up (3.7 minutes that day) does. This one figure replaces two copies of the 0.2 kWh line, and feeds how likely PowerEngine thinks a planned smart slot is to happen (so it moves the expected price of future smart slots) and the Health tab's "used" and "done, no car" counts.
- **The figure is a setting that learns.** New setting **Shortest real charge** (2 minutes, 0.5 to 10) and tick box **Learn: shortest real charge** (on) in the car section of the Config page. With learning on, once 8 smart slots the supplier confirmed have been seen it moves slowly towards half of a typical short real charge, never below half or above double the setting. The figure in use is on the diagnostics export (`smart_slots.min_charge_min`).

### Other changes

- Each smart slot's record keeps the longest unbroken charge (`longest_min`); records from before use their total charging time.
- The Config page section is now "Car and smart charging" (it carried the supplier's name).

## 0.9.103 (beta)

### Behaviour changes

- **The plan no longer waits for the car.** Until now, any smart-charge slot with the car plugged in was planned as car charging: the battery was held and nothing was sold, even when the car was not charging (on 4 Oct a one-minute blip of car draw kept the plan flat at 90% from 19:00 to 04:00). Now the plan only believes what the charger says: smart-charge slots are planned as cheap time with no car, so the battery can sell and refill as usual. When the car is charging, that half-hour is planned as car charging with the live power, and the plan assumes the charge ends by the end of it. When the car starts or stops, or is still charging in the next half-hour, the plan is remade, and the battery is held while it charges, as before.
- The guesses about whether the car is full (the last slot's draw, the charger saying "complete") no longer shape the plan. The `smart_skip_full_car` setting still uses them, and while it is off, smart-charge slots are asked for even when the car is full.

## 0.9.102 (beta)

### Behaviour changes

- **The arbitrage band is now a soft guide, so cycles use whole half-hours.** A sale at full power takes the battery down
  about 15 points but a charge restores about 13, so the plan used to charge for a slot and a bit and then sit idle (Hold)
  for the rest of the second slot. The band's top is now soft: a charge may run up to 5 points past it, and a sale may go up
  to 5 points under its bottom (outside the overnight window), paying the existing outside-band cost. The battery still never
  sells below 5 points under the band (70% by default) or the minimum reserve, the final overnight top-up and free-power
  charging are unchanged, and a cheap car top-up keeps its level. Expect tighter sell, charge, sell, charge cycles and fewer
  Holds in the plan. Control is otherwise unchanged. The two band settings' help text says so (docs/plans/soft-band.md).
- **The Plan page's Actions list shows again.** Home Assistant stopped it from drawing because it asked for a "manual"
  marker that only exists while a manual override is on, and Home Assistant treats a missing marker as an error.

### Tests

- A test renders the Actions list the way Home Assistant does, with a window that has no "manual" marker, and one checks
  that charge slots in a cycle run whole half-hours. The replay is re-recorded: the night's plan has fewer flips (20 decision
  changes become 13) and more half-hours at full power.

## 0.9.101 (beta)

### Behaviour changes

- **A tiny car draw no longer stops the plan selling during smart-charge slots.** If the last smart-charge slot drew less than 0.2 kWh (for example a one-minute blip of 0.01 kWh), PowerEngine now treats the car as full, as it already did for no draw at all. Before, that blip made it plan every later smart-charge slot as car charging: no selling and the battery held at 90% from 19:00 to 04:00 (4 Oct 2026). If the car does start charging, the battery is held at once, as before.

## 0.9.100 (beta)

### Behaviour changes

None. The control and the plan are unchanged.

### Report a problem

- A new **Report a problem** form for GitHub issues (`.github/ISSUE_TEMPLATE/report-a-problem.yml`): description, versions, and a box for the diagnostics file. It is the target of the card's new button; it needs card 0.9.100.

## 0.9.99 (beta)

### Behaviour changes

- **A charge target reached early no longer just holds.** When the plan's charge target for the running half-hour is reached with 5 or more minutes left, PowerEngine now replans straight away from the live battery level for the rest of that half-hour, so the best action takes over: keep charging if the plan wants more, start the next half-hour's sale early, or hold. With less than 5 minutes left, or no plan, it holds as before. It looks once per half-hour, so it cannot flip back and forth.
- Every such moment is recorded (the time left, the battery level, the rates, what the plan had next, and what the replan chose, or why the hold stayed). The records are kept in `early_target.json` beside the config and are in the diagnostics export, so the effect can be reviewed after a week (#175).

## 0.9.98 (beta)

### Behaviour changes

- The plan no longer assumes a Hold stores surplus sun. On the inverter, Hold is a forced charge at 0 W: with 1.9 kW of spare solar on 4 Oct the battery sat idle under Hold, where Self-use charged it at 1.9 kW. The plan used to count that sun as stored, so its battery path ran ahead of the real battery and it liked Hold in sunny half-hours. Now Hold exports the spare sun, so the plan prefers Self-use in sunny half-hours unless holding the battery really pays. Expect fewer sunny-day holds and a battery chart that stays closer to the real one.

## 0.9.97 (beta)

### Behaviour changes

- **Manual override.** The Monitoring page has an **Override** button in the top panel. It switches the inverter to Self-use, Hold (the grid runs the house), Charge or Export for this plan window, a number of half-hours, until a time you pick (up to 12 hours), or permanently until you cancel it. Periods end on a half-hour so they line up with the plan. It works only in Active. A grid event in progress still takes over, Export and Self-use stop at the minimum reserve, Charge holds at the charge target, and Pause, the fuse limit, the battery limits and the write budget still apply. It survives a restart. The plan is made around the override's half-hours (so the rest of the day starts from what the battery will really hold), and it replans when you set it, cancel it or it ends. Those half-hours show in pink on the plan chart and as "(manual)" in the Actions table.
- A reading that goes missing for up to three minutes (for example the import rate, which dropped out for one minute on 4 Oct) no longer hands the inverter to Self-use and straight back. PowerEngine keeps its last decision through the gap. A longer gap behaves as before.

### Other changes

- The top panel's "Mode" tile is now called **Power Engine** (Active, Paused, Passive or Blocked), and a new **Inverter** tile says what the inverter is doing.
- Plan history, "As run": the day no longer lists every window a replan replaced (52 windows, 17 KB, over Home Assistant's 16 KB limit, so its history was not recorded). It shows the windows that actually ran, merged, and is held under the limit.

## 0.9.96 (beta)

### Behaviour changes

- The plan no longer holds the battery in a dear-rate half-hour just because the solar forecast says the sun will cover the house. Holding stops the battery helping, so when the sun fell short (4 Oct: forecast 2 kW, actual 0.4 kW) the house bought power at 28.84p with the battery at 84%. Those half-hours now plan Self-use, which only uses the battery when the sun falls short. Holds that the plan chose to save charge for a dearer slot, and holds at cheap rates, are unchanged.

## 0.9.95 (beta)

### Behaviour changes

None. PowerEngine plans and controls exactly as before.

### What's new

- **The Config page's Setup wizard is replaced by a "Your system" section.** It lists the equipment PowerEngine uses (inverter and battery, tariff, car charger, solar forecast, grid events, extra solar and other devices) and shows each one's live value. Nothing in the list is editable. **Change your system** opens a panel where you add, edit, replace and remove equipment, whether the install is new or already running.
- Changes in the panel are a **draft** until you choose **Apply to System**. **Save draft** keeps the draft in your browser, and closing the panel with unsaved edits asks whether to save or discard it. Apply sends only the equipment changes; your other settings are not touched.
- The inverter and the tariff can be replaced but not removed. Removing the car charger, solar forecast, grid events, an extra solar plant or another device first says what goes with it (features that switch off, inputs that stop being used). Active, Passive and Pause are never changed by Apply. Changing the inverter still switches to Passive and asks for the supervised tests, as before.
- The dashboard's Config page now uses the new card, so this release needs the matching card (0.9.95): the app warns if the card is older.

## 0.9.94 (beta)

### Behaviour changes

None. PowerEngine plans and controls exactly as before.

### What's new

- **The Costs page's daily bars now end on the same figure as the savings chart.** The "Daily cost by scenario" chart has a new last bar, **You paid**: the PowerEngine bar plus the Axle and free-power half-hours, less Axle's payment. It matches the savings chart's "You paid" figure for the same day. Before, the bars stopped at the PowerEngine level, so a day with an Axle event showed a different final figure from the savings chart. On a day with no events the new bar equals the PowerEngine bar, and below zero means the day paid you.
- The text under the chart describes the new bar and no longer says events are left out of the bars.

## 0.9.93 (beta)

### Behaviour changes

None. PowerEngine plans and controls exactly as before, and a home with one inverter publishes exactly what it did.

### What's new

- **Other devices (read only).** You can now add more devices beyond your main inverter: for example a second hybrid inverter with its own battery. PowerEngine reads each one's battery level, battery power and solar power, shows them, and counts their solar in your total. It does not control them or plan their battery; only the inverter you chose stays in charge. Add them under **Your system, Other devices** on the Config tab (it needs the card from this release), or in `config.yaml` under `devices:` (see docs/SITE.md). Each input you choose becomes a sensor, `sensor.pe_state_dev_<id>_soc`, `_battery_power` and `_solar_power`.
- A configuration saved from an older card keeps your devices rather than dropping them.
- This is the first step of a longer plan for homes with more than one battery (docs/plans/multiple-devices.md). Controlling a second battery comes later and will need its own testing.

## 0.9.92 (beta)

### Behaviour changes

None. PowerEngine plans and controls exactly as before.

### What's new

- **The daily notification now matches the Costs page.** It used to report "Solar, smart charge, battery" from older cost layers, so its numbers didn't add up against the Costs savings waterfall. It now says what you paid yesterday against what you'd have paid with no solar or battery, then each step of the waterfall: solar, your tariff, PowerEngine, and grid events and free power when they moved money. A minus sign is a saving.

## 0.9.91 (beta)

### Behaviour changes

None. PowerEngine plans and controls exactly as before.

### What's new

- **Low-write study.** Each night PowerEngine now plans your last recorded days again under four rules (leave the inverter alone, the full plan, an overnight cycle only, overnight charging only) and shows what each would have saved, how many times the inverter's timed windows would change and roughly how many writes that is. It is the first step of a low-write mode for inverters that wear when settings are written. It only measures: nothing here is switched on and nothing is sent to your inverter. The results are on the Health tab under "Low-write study" and in the diagnostics export's sensor list.
- The planner can now be limited to fewer kinds of action (used only by this study; the normal plan is unchanged).
- `tools/low_write_study.py` runs the same comparison on the demo days.

## 0.9.90 (beta)

### Behaviour changes

None. Nothing PowerEngine does to your system changes: the replay of a recorded night is identical, and your Solis keeps its three charge and three discharge windows, the same update button and the same remote-control words.

### Licence and contributing

- PowerEngine is now licensed under **Apache-2.0** (it was MIT), in both the app and the card. New `LICENSE`, `NOTICE` and `CONTRIBUTING.md` files; the README says how to send a device list from the setup wizard, write an inverter definition and open a pull request, and what rules the code keeps.

### Ready for other inverters

- An inverter definition now says how many timed charge and discharge windows the inverter has (1 to 8, instead of always three) and which control is its "apply the window times" button, so another brand's timed windows need no code change.
- The inverter's remote-control mode is read back through the same word mapping used when it is written, so a definition can name the modes anything.
- Docs: docs/INVERTERS.md describes the new definition keys.

## 0.9.89 (beta)

### Behaviour changes

None. This changes what the Config page and setup wizard show and how some settings are worded; nothing PowerEngine does to your system changes.

### Setup wizard and Config page

- On a system that is already set up, the **Setup wizard** is folded away until you open it. A new system still sees it open.
- The wizard now reads what PowerEngine is already using for each part and keeps it chosen. With both the EDF and Octopus integrations installed it shows EDF (the one in your configuration) as "in use now", and lists Octopus as "also found, not used by PowerEngine" instead of picking it.
- Where Home Assistant has more than one device for a part, all are listed and the one in use is marked. PowerEngine controls one inverter with its battery, and you choose which; the design for supporting more is in docs/plans/multiple-devices.md.
- **Solar plants:** a second solar-only inverter, or plug-in panels, can be added as a read-only solar plant (counted in total solar and drawn on the energy-flow card). The wizard shows the plants you already have, offers other solar-looking devices to add (guessing their power and energy-today entities), and lets you add or remove one without touching anything else. A hybrid inverter with a battery that PowerEngine has no support for yet is listed as "not supported yet", with a way to send its entity list.
- **Your system** lists the solar plants it counts and the same "also found" devices, and its labels now match the wizard (Inverter, Car charger, Electricity tariff, Solar forecast, Grid events).
- The setup checklist box at the top of the Config tab hides itself when everything is in place. **Check for updates** is now the first box on the page.
- Wording: grid events are now called grid events throughout the Config page (the role names, the on/off switches, the settings, the notification and the section), with the provider named in the description (Axle for you). The settings keep their names, so nothing needs re-saving.

## 0.9.88 (beta)

### Behaviour changes

None for a home with a car charger, a solar forecast and grid events set up, which includes yours: nothing PowerEngine does to your system changes. One change for homes that leave a part out: when the system settings say there is no car charger, no solar forecast or no grid-events provider, the inputs for that part are no longer counted as required, so they no longer hold PowerEngine at "inputs missing".

### Setup wizard (needs card 0.9.88)

- A new **Setup wizard** card at the top of the Config tab gets a new home to a working Passive install in five steps: what you'll need (required and optional parts, with what it found in Home Assistant and links to install what's missing), your devices, mapping the inputs from each device's own entities, live checks of the battery and grid signs and that the numbers add up, and a review before saving. It starts in Passive, keeps a backup of any existing configuration, and never turns control on.
- Parts you don't have (car charger, solar forecast, grid events) can be skipped. Smart-charge optimisation, grid events and free-power sessions are switched off when their part or inputs are missing.
- On a system that is already set up, each part has a "Change this part" tick, so you can redo one part without touching the others.
- For hardware PowerEngine doesn't support yet, the wizard can write a **candidate entities** file: a list of the device's entities with their units, options and limits, with long numbers (meter and account numbers, serials), email addresses and postcodes removed first. It's what's needed to write a definition for that hardware. `tools/candidates_summary.py` reads one.
- The app now tells the wizard how to recognise each inverter, tariff, car charger, forecast and grid-event provider, so a new inverter definition shows up in the wizard by itself (docs/WIZARD.md).

### Other

- The dashboard's Config tab carries the wizard card, and the app now asks for card 0.9.88 or newer.
- Docs: new docs/WIZARD.md; the install guide, the inverter guide and the site guide mention the wizard.

## 0.9.87 (beta)

### Behaviour changes

None. This only changes what the Costs page shows; nothing PowerEngine does to your system changes.

### Costs page

- The daily cost chart now shows the last 7 complete days, the same days as the savings waterfall's "Last 7 days", and no longer shows today (a day that isn't over always looked cheap). On phones it shows 7 days too, not 5.
- "Cost layers by day" is now "Savings by day": one row per complete day with the waterfall's own steps (No solar or battery, Solar, your tariff, PowerEngine, events, You paid), plus a "Last 7 days" row that matches the waterfall.
- "How the layers are calculated" is now "How the numbers are worked out" and describes the waterfall's lines instead of the older layers.
- Fixed explanations that were out of date: PowerEngine's figure is the real metered cost of your normal half-hours. Energy bought for later counts on the day it was bought, so a single day can look dearer or cheaper than the battery really made it, and it evens out over a week. The old text described a carry-over adjustment that is no longer made.
- "Energy by day" lost the "Ledger correction" column, and its notes no longer refer to "Unexplained", which no longer appears on the page. It still includes today so far.

## 0.9.86 (beta)

### Behaviour changes

- Plan history now picks its day with a date box and previous/next arrows (any day PowerEngine still holds, up to 400 days back). The old Day pulldown is gone. The page shows yesterday until you pick a day, and goes back to yesterday when AppDaemon restarts. Only an admin user can change the day.
- The arrows grey out at the oldest day held and at today.
- The as-run plan, its 400-day retention and the collapsed "Plan against actual" text are as in 0.9.85.

### Other changes

- The retired "History day" entity (select.pe_ui_history_day) is removed at start-up, as "History plan" was in 0.9.85.
- Needs card 0.9.86 or newer (released with this); the app now warns if the card is older.
- To check after updating: the Plan history charts draw and follow the day you pick; the arrows step the day and grey out at the oldest day and at today; after a few half-hours, today shows as-run data.

## 0.9.85 (beta)

### Behaviour changes

- **Fixes the Plan history charts showing "Configuration error" in 0.9.84.** The 5-second refresh added in 0.9.84 is removed, so the charts draw again. They follow the Day select (or the picked day) when you change it.
- Plan history now shows only the plan that actually ran, half-hour by half-hour, so it matches what happened even though the plan is re-made many times a day. The "Plan made at" choice is gone (the retired "History plan" entity, select.pe_ui_history_plan, is removed at start-up). Days before the as-run record began show the plan made at the start of that day instead, and say so.
- The text above the charts is now collapsed ("Plan against actual"); open it for the plan-versus-actual figures.
- The as-run plan is still kept for 400 days. The first plan of each hour (60 days) and the start-of-day plan (400 days) are still kept for diagnostics, but are no longer shown on the page.

### Other changes

- Works with card 0.9.84, which is already released (it has the date picker). No new card release.
- To check after updating: the Plan history charts draw, follow the Day select, and the date picker switches day.

## 0.9.84 (beta)

### Behaviour changes

- Plan history has a new "As run" choice in "Plan made at". It shows the plan exactly as it stood in each half-hour that actually ran, so it matches what happened even though the plan is re-made many times a day. It is kept for 400 days (the first plan of each hour is still kept for 60 days). Days before this release have no "As run" plan and show the start-of-day plan as before.
- Plan history has a "Pick any day" date picker (any day PowerEngine still holds, up to 400 days back). The Day select still works and wins if you use it.
- The Plan history charts now re-read the chosen day every 5 seconds, so changing the day or plan updates the charts, not just the text. (If the charts still don't follow, tell Claude.)

### Other changes

- The note under the Plan history charts describes what is kept and for how long.
- Needs card 0.9.84 or newer (the card is released with this).

## 0.9.83 (beta)

### Behaviour changes

PowerEngine now refuses Active, and stays Passive with the reason shown, when the inverter's definition has not been proven on real hardware (status not "verified") or the firmware is not one that was verified. Nothing changes for the Solis on firmware 420044.

### Other changes

- Documentation updated for Phase 1 (the inverter definition guide and the plan's status box).

## 0.9.82 (beta)

### Behaviour changes

None.

### Other changes

- This release carries a card fix (the Tests page no longer shows "Configuration error"); see the card notes. The only change to the app itself is in the notes for developers.

## 0.9.81 (beta)

### Behaviour changes

- The summary block at the top of the planner page is now collapsed to its headline line; click it to expand.
- Export headlines no longer show the import price in brackets. They now read "sell at 15p, buy back at 6.66p from 13:30".

### Other changes

- Nothing else changes in how the app controls your battery.

## 0.9.80 (beta)

### Behaviour changes

- Arbitrage sales are now planned as early as they can be. Before, when a cheap window had spare half-hours, the optimiser could leave them before the first sale, so the battery sat idle at 90% for an hour and then sold later with the same cash result. It now sells first and the spare half-hours move later in the night, where a replan can still use them.
- No change to the limits, the arbitrage band, the floors, the switch costs or the write budget. Total energy sold is unchanged. On the 27 Sep replay night the first sale moves from 00:00 to 23:00, and there is less flip-flopping (decision changes 53 to 20).

### Other changes

- The recorded replay nights were re-recorded for this intended change.

## 0.9.79 (beta)

### Behaviour changes

- Monitoring tab: a new **Car** gauge shows the car charger's power (0 to 7.4 kW, green while charging). It sits in the Now section beside the Solar and House gauges, which are a little narrower to make room.

### Other changes

- Nothing else changes in how the app controls your battery.

## 0.9.78 (beta)

### Behaviour changes

- Monitoring tab: the Car, smart-charge, event and Free power tiles moved up to the top row, beside Mode, Inverter control and Saved today, so all the status tiles are together. They no longer sit under the Now gauges.
- Each of those tiles turns green while it is active: the car charging, a smart-charge slot under way, an event in progress, free power now. Mode keeps its own colours.
- The smart-charge sensor now reads "In slot", "Slot planned" or "No slots planned", with the time on a second line ("until 06:30", "from 21:00"). It used to read "Slot until 06:30" / "Next slot 21:00"; the fixed words are what let the tile change colour.

### Other changes

- Nothing else changes in how the app controls your battery.

## 0.9.77 (beta)

### Behaviour changes

- With RAM remote control, a force discharge that gives less than the command because the sun is using part of the inverter's output is no longer reported as "inverter not following". The check now allows for the inverter's total output limit (solar plus battery) and expects only what is left for the battery. A battery doing too little for that room is still reported.
- New setting **Inverter max output** (Inverter control, default 6000 W): the most the inverter can put out in total. Set it to your inverter's real rating.
- Charging is checked as before: solar goes into the battery, so it can only help.

### Other changes

- The diagnostics export's remote-control rows now include the solar power and the expected battery power after the inverter limit.

## 0.9.76 (beta)

### Behaviour changes

- Monitoring tab: the current status now comes first (control tiles, then the Now gauges and tiles), followed by the energy-flow card.
- Monitoring tab: the text status and "Earlier today" list moved to the bottom, in an Activity section that is folded by default. The folded line shows what PowerEngine is doing now; open it for the reason, the next change and the earlier decisions.

## 0.9.75 (beta)

### Behaviour changes
- **The savings chart's date-range section is gone.** The "Savings for a date range" section on the Costs tab (the From and To pickers, the summary line and the second chart) did not work the way you wanted, so it has been removed. The two settings behind it, "Costs range from" and "Costs range to", disappear from Home Assistant when the app starts.
- The savings chart itself is unchanged: the combined **PowerEngine** step (with the battery carry-over included), "No solar or battery" and "You paid" show exactly the same numbers as in 0.9.72, and so does the "Daily cost by scenario" chart. Yesterday, Last 7 days, This month and Last 30 days still work as before.
- The app asks for card 0.9.70 or newer again, as it did before 0.9.72.

### Other changes
- Nothing else changes in how the app controls your battery.

## 0.9.74 (beta)

### Behaviour changes
- The app now asks for card 0.9.74 or newer, and warns you if the PowerEngine card is older. The "Savings for a date range" chart on the dashboard needs the new card to work properly. Press Update on the Configuration page to get both.

### Other changes
- Nothing else changes in how the app controls your battery.

## 0.9.73 (beta)

### Behaviour changes
- **Remote control stays within what the battery will take.** If you map the new optional inputs *BMS charge limit* and *BMS discharge limit* (Solis: `sensor.solis_bms_battery_charge_limit`, `sensor.solis_bms_battery_discharge_limit`), PowerEngine lowers a charge or discharge command to the battery's own limit (amps times 52 V) when that is below the command. A limit of 0 turns a charge into a hold and a discharge into Self-Use. Nothing changes if the inputs are not mapped or read unavailable or nonsense.
- **Cold battery, no BMS sensor:** while the cold-battery caution says the battery is cold, the charge command is capped at the caution's charge rate (the rate the plan already assumed) instead of always asking for the full rate.
- **The "inverter not following" check** now compares the battery with the lower of the command and the BMS limit, so a charge the battery itself limits (cold, nearly full) is no longer reported as not following.
- **A command that doesn't take is stepped down.** If a charge or discharge above 3000 W is still not being followed after 3 minutes, and the battery is doing less than a fifth of it (idle, or going the other way, as when a refused setting leaves the previous one), PowerEngine re-sends it 1000 W lower (for example 5000, 4000, 3000 W) and sends one notification; each step is in the log. A battery that is doing some of it (limited by its own BMS or by taper) is not stepped down. These are remote-control commands, not EEPROM writes, so the write budget is untouched. The lower ceiling is dropped when the kind of command changes or after an hour. At 3000 W the usual "not following" report applies.

### Diagnostics
- The diagnostics export has a new `bms` section: the charge limit and discharge limit sensors now, and a short history (one row every 2 minutes and on each command change) of the command, the expected power, the actual battery power, SoC, the follow state and the limits, so the first cold spell can be read back.

### Setup
- Two optional inputs in the battery section (see docs/INSTALL.md, "Battery limits"). Existing set-ups keep working without them. The role catalogue grows by about 0.45 KB (still under the 16 KB limit).

### Not done
- Reading the force-power registers back (43136 / 43129) with a Modbus probe: the SolaX Modbus integration here gives no read-back entity for them, so it is left for a later release.

## 0.9.72 (beta)

### Behaviour changes
- **Costs tab, savings chart:** "Battery on self-use" and "Battery carry-over" were very small, so they are gone as separate bars. They are now part of **PowerEngine**, which shows everything the battery and PowerEngine did on top of solar and your tariff. The "Day-to-day cost" subtotal is gone too, because it would now equal the PowerEngine bar. "No solar or battery" and "You paid" are exactly the same as before.
- **Costs tab, "Daily cost by scenario":** it now has the same steps as the savings chart (No solar or battery, Solar, tariff, PowerEngine). The "+ battery on self-use" bars are gone, and each PowerEngine bar is that day's real cost including the carry-over, so the days add up to the savings chart. The headline sentences and the "What the scenarios mean" text say the same.
- **New: savings for any date range.** Under the savings chart there is a new "Savings for a date range" section with two pickers, **From** and **To** (for example "7 days ago" and "Yesterday"). Both days are included, up to 30 days back, and only complete days count. If From is after To the two are swapped, and if some days have no costs you are told how many days were used. Above the chart you see the first and last date and how many days it covers. It starts on the last 7 days.
- The "Savings waterfall" sensor's value is now what the battery and PowerEngine saved yesterday (before it was only PowerEngine's part on top of a plain self-use battery), so it is larger.

### Other changes
- Two new dashboard settings, `select.pe_ui_cost_from` and `select.pe_ui_cost_to`, hold the chosen range.
- The range is worked out from the days PowerEngine already keeps, so there are no extra calls to your supplier or any other outside service.

## 0.9.71 (beta)

### Behaviour changes
- None.

### Versions and tooling
- The app and the card no longer have to be at the same version. Each says the oldest version of the other it works
  with: the app needs card 0.9.70 or later, and the card needs app 0.9.69 or later. A warning shows only when one is
  older than that. The card is now released only when it changes, so its version may skip numbers.
- The app publishes `min_card_version` on `sensor.pe_diag_version`.
- Developer tools: `tools/release.sh`, which releases in one command, and `tools/diag_summary.py`, a one-screen summary
  of a diagnostics export.

## 0.9.70 (beta)

### Behaviour changes
- None for a set-up system. These are demo and first-install fixes from the second clean-install test.

### Demo and first install
- No more "Invalid callback handle" warnings when the demo starts, changes day or exits. Each daily timer was
  cancelled twice; the first cancel always worked, so no timers were left running.
- No "Unknown Plugin Configuration" warning at start-up when there is no MQTT plugin.
- The dashboard reloads itself once after starting, changing or leaving the demo, so the demo's own dashboard,
  including the solar panel, shows without a manual refresh.

## 0.9.69 (beta)

### Behaviour changes
- None for a set-up system using MQTT. Every fix below is on the demo and first-install path, found on the first
  clean-install test, on HA 2026.9.4 with AppDaemon add-on 0.19.2.

### Demo and first install
- **Timers are cancelled properly on newer AppDaemon.** A demo start, day switch or exit left the old repeating
  timers running alongside the new ones, because newer AppDaemon hands back a task instead of the timer's handle.
- **Values are written exactly (direct publishing).** Newer AppDaemon turned `true` into the text "true" and dropped
  zeros and `false` from attributes. That broke saving settings in the demo ("feature ... must be true or false") and
  shifted the plan chart's solar and forecast series by hours.
- **The demo keeps the recorded home's clock (Europe/London)** whatever time zone the HA install uses, so solar,
  prices and events appear at the recorded times. The sunny day's event appeared an hour late on an install set to
  Europe/Amsterdam.
- **A quiet start:** no "Entity ... not found" warnings when PowerEngine creates its own entities, no health-check
  crash before setup, and "no house-load input" and "no config.yaml" are information, not warnings.
- **Before setup,** Mode and Health read "Not set up yet" instead of a red "Blocked" and "Unknown". The welcome card
  names the demo days the same way as the banner.

## 0.9.68 (beta)

### Behaviour changes
- None. Late-notice Axle events already worked: with no cheap slot before an event, PowerEngine charges at the peak
  rate, but only as much as the event can sell at full power, because £1.15/kWh is worth far more than about 30p.

### Fixes
- The plan now says what a pre-event charge is for: "charge at 28.84p for the Axle event at 16:00: £1.15/kWh (£1
  Axle + 15p export)". It used to read "charge at 28.84p to sell at 15p from 02:00 tomorrow", which looked like a
  losing trade. New tests cover a late-notice event, and a battery that already holds enough.

## 0.9.67 (beta)

### Behaviour changes
- **Deep overnight selling is back.** PowerEngine learns the fixed overnight window from the half-hours at each day's
  lowest rate. On 30 Sep EDF priced the smart slots at its new 6.66p while that night still ran at the old 6.99p, so
  the night dropped out of that day's cheap set and the window came out empty. Overnight arbitrage then stayed inside
  the 75–90% band, as it does outside the window. A half-hour within 15% of the day's lowest rate now counts as cheap.
  The window, and deep overnight selling with a guaranteed refill, return at the next plan.

## 0.9.66 (beta)

### Behaviour changes
- None for your setup. On first start PowerEngine adds a `site:` section to your configuration describing the plant
  it has always controlled: inverter Solis on firmware 420044, car charger Zappi, tariff detected automatically,
  forecast Solcast and grid events Axle. A backup of the configuration is kept first. The firmware is recorded as
  assumed, because the inverter doesn't report it.

### Your system (making PowerEngine generic, step 8)
- The configuration now says which plant this home has, and PowerEngine builds each driver from it by name instead
  of assuming this one. The car charger, forecast and grid events can each be set to None.
- The Configuration page has a new **Your system** block at the top, with a dropdown for each part and each option's
  test status (verified, community or draft). If the inverter reports its firmware, the block shows whether it
  matches the one chosen.
- Changing the inverter, or to a firmware that works differently, switches PowerEngine to Passive. A banner then asks
  you to run the supervised tests on the Tests page before going Active again.
- `docs/SITE.md` describes the new `site:` keys.

## 0.9.65 (beta)

### Behaviour changes
- None. PowerEngine does exactly what it did before, with the same commands, texts and suggestions.

### Inverters as definition files (making PowerEngine generic, step 7)
- Everything PowerEngine knows about the Solis inverter now lives in a data file (`adapters/devices/solis.yml`):
  the remote-control entities and options, the timed windows, the clock, the entity suggestions on the Configuration
  page, and firmware variants. A small generic driver reads it.
- Supporting another inverter should mostly mean writing one of these files. See `docs/INVERTERS.md` for the format.
- The file ends `.yml` on purpose: AppDaemon treats any `.yaml` in the apps folder as app configuration.

## 0.9.64 (beta)

### Behaviour changes
- **No more flip-flopping at a charge target.** Once the battery reaches the plan's target for a half-hour it holds
  for the rest of that half-hour, unless the charge falls 2% or more below the target or the plan changes. Before,
  the inverter's whole-number charge reading (93% while charging, 94% while holding) made it switch between charge
  and hold every 30 seconds.
- **No forced charge at the peak rate when the plan only expects solar.** When the plan's charge in a non-cheap
  half-hour would come entirely from solar surplus, it now uses self-use instead of a forced charge. A forced charge
  draws its full power whatever the sun does, so on 29 Sep a cloudy spell meant a few pence of power were bought at
  30.28p to sell at 15p. Cheap, free-power and car smart-charge slots still force-charge as before.

### Fixes
- A car smart-charge slot that EDF has planned but not yet priced now reads "tariff still 30.28p" rather
  than naming the peak price as the slot's price.
- When a required input is briefly missing, the log and status now say "waiting for inputs" instead of
  "unconfigured".
- Smart-slot statistics: the planned energy per slot is now scaled to the slot's actual length, and the total counts
  only slots that ran. The total had included withdrawn and re-listed slots, giving impossible figures such as
  5,345 kWh over two weeks.

## 0.9.63 (beta)

### Behaviour changes
- None for your setup. The demo can only start on a PowerEngine that isn't set up yet, so it can never take over a
  real system.

### Demo mode (first working version)
- A PowerEngine with no configuration now opens on a welcome card with two choices: **Try the demo** or **Set up your
  system**.
- The demo runs on four recorded, scrubbed days from a real home: a sunny day, a dull day, a grid-services event day
  and a car-charging day. A simulated battery follows PowerEngine's own commands, so the plans, decisions, costs and
  charts behave as they would for real.
- **Nothing is controlled.** In demo mode every service call goes to the simulated home, never to Home Assistant, and
  the only things written are PowerEngine's own sensors. Its data goes in a separate demo folder.
- A banner on every page says it's a demo. It has buttons to switch day or exit. Settings can be changed in the demo
  and are reset on exit.
- It works without MQTT: PowerEngine can publish its entities directly, and their switches work too.
- Your dashboard gains a hidden demo card at the top of each page. It shows nothing on a set-up system.
- **Don't run a second PowerEngine in demo mode alongside your real one**: they would share entity names.

## 0.9.62 (beta)

### Behaviour changes
- None for your setup.

### Easier install (the first steps of the demo-mode plan)
- **New: PowerEngine setup card**, at the top of the Configuration page. It checks each piece PowerEngine needs:
  - HACS, and its AppDaemon option;
  - the AppDaemon add-on;
  - the PowerEngine app, and whether it's running;
  - the chart cards;
  - MQTT.

  Anything HACS can install gets a button (admins only). When everything is in place it shows one line: "All set". It can also be added to any dashboard from the card picker, which is how a new user will start.
- **New setting: Entity publishing** (Config page, under Other): auto, MQTT or direct.
  - **MQTT**, as today, keeps entities across restarts and lets their switches be changed from Home Assistant.
  - **Direct** needs no MQTT broker, for the coming demo mode. Entities are rebuilt at each start and switches are read-only for now.
  - **Auto** (the default) uses MQTT when it's there, so nothing changes for you.

## 0.9.61 (beta)

### Behaviour changes
- None, and no visible changes for your setup: every screen and message reads word for word as before.

### Under the hood (making PowerEngine generic, step 6)
- Supplier and device names (EDF, EDF smart slot, Zappi, Solcast, Solis, Axle) now come from the adapters rather
  than being written into the texts. Someone on Octopus would see "intelligent dispatch" where you see "EDF smart
  slot".
- The dashboard is written with those names when PowerEngine starts or the configuration is saved. A test checks
  that your dashboard comes out byte-for-byte the same as before.
- No entity names, attributes, settings or history changed.

## 0.9.60 (beta)

### Behaviour changes
- None unless you change the new settings: their defaults match what PowerEngine did before.

### Smart-charge requests, configurable (Config page, car and smart charge)
- **Requests per day** (4–10, default 6).
- **Time between requests** (10–120 minutes, default 20). Unsuccessful requests also back off: 30, 60, 120, then
  240 minutes. That usually limits things more than the daily cap.
- **Skip if a slot is due within** (1–8 hours, default 3).
- **Smart slots cover the whole house** (default on). Tick if your supplier charges the whole house the slot rate
  even when the car isn't charging (EDF does). Off: slots are planned at your normal rate for the house and battery,
  and PowerEngine doesn't ask for extra slots.
- **Don't ask when the car is full** (default off). This skips requests while the charger says the charge is
  complete, or the car drew nothing in the last slot. The success rate on the Config page shows whether requests for
  a full car work.

## 0.9.59 (beta)

### Behaviour changes
- None. This release reorganises how PowerEngine reads the solar forecast, so other forecast services can be
  supported later.

### Under the hood (making PowerEngine generic, step 5)
- The Solcast-specific part (the half-hourly forecast list and how its estimates become kWh) now sits in a forecast
  "adapter". The planner works from neutral half-hourly points.
- One robustness gain: a malformed entry in the forecast list is now skipped rather than stopping the plan.
- The recorded-night replay passed unchanged.

## 0.9.58 (beta)

### Behaviour changes
- None. This release reorganises how PowerEngine reads your car charger, so other chargers can be supported later.

### Under the hood (making PowerEngine generic, step 4)
- The Zappi-specific part now sits in a car-charger "adapter": turning the plug status ("Charging", "EV
  Disconnected" and so on) into charging, plugged in or unplugged, and spotting "charge complete".
- Spotting a full car from the smart-slot history and the check meter were already charger-neutral, so they stay
  as they are.
- The recorded-night replay passed unchanged.

## 0.9.57 (beta)

### Behaviour changes
- None. This release reorganises how PowerEngine reads your tariff, so other suppliers can be supported later.
  What it does should be exactly the same.

### Under the hood (making PowerEngine generic, step 3)
- Everything specific to how EDF's tariff reaches PowerEngine now sits in a tariff "adapter" for the Octopus
  Energy integration, which serves EDF and Octopus alike. That covers:
  - the half-hourly rates, standing charge and off-peak flag;
  - smart slots;
  - free-electricity sessions;
  - how PowerEngine asks for smart slots (the ready-by time and charge target).

  Axle events come through their own small adapter.
- When and how often PowerEngine asks EDF for slots is unchanged, including the back-off and settle rules.
- The recorded-night replay passed unchanged.

## 0.9.56 (beta)

### Behaviour changes
- **Exports in cheap slots now run their full half-hour** (#168). The planner treated the current half-hour as a
  whole 30 minutes even when it re-planned part-way through. A sale that had to stay above the 75% selling floor
  looked impossible after a minute of discharging, so the next re-plan switched to charging.
  - What that caused: it sold about 1 kWh at a time instead of the 2.6 kWh planned, and switched export ↔ charge
    every hour (every 5 minutes just after a restart).
  - Now: the current half-hour is planned for the time it has left. A planned sale completes, then the battery
    refills, with fewer, longer actions. On the recorded test night, timed-window mode also made about 30% fewer
    inverter writes.
- **Charge labels show where the charge is heading:** "Grid-charge to 90%", not the next half-hour's step (the
  label said 47% on 29 Sep while charging to 90%). Control still works half-hour by half-hour.

## 0.9.55 (beta)

### Behaviour changes
- **Axle events export at full power.** Before, discharge during an event was capped at 4 kW whatever your battery
  could do. On 28 Sep the battery ran at 3.97 kW, with about 2.5 kW reaching the grid, and there was 71% charge
  left at the end. Events now run at your battery's maximum discharge (5.2 kW), within the RAM remote-control limit
  (5 kW). At £1.15/kWh that's about £1.15 more per hour of event.
- **Axle preparation is sized to match.** The plan and the pre-event reserve now allow for the higher power, so
  the battery is topped up a little more before a scheduled event.

### Fixes
- **Last special event:** the Costs page now shows the whole event (all its half-hours added up), not just its
  last half-hour. Costs are recalculated once after updating (cost method 7) to rebuild it.
- **Plan sensor size:** a choppy plan could push `sensor.pe_plan` over Home Assistant's 16 KB limit (18 KB on the
  morning of 28 Sep), and HA then stops recording its history. The Actions list now shows the soonest changes
  that fit, with "…and N more later" underneath. The chart still covers the whole plan.

## 0.9.54 (beta)

### Behaviour changes
- None to control. Cost reporting only: past days are re-valued once after updating (cost method 6).

### Costs page, clearer
- **Tap a column** in the savings waterfall to see its full name and exact value. The old hover tooltip didn't
  work on phones.
- **"Everyday cost" is now "Day-to-day cost"**, with a key under the chart explaining every column, left to right.
- **The headline** says what you paid first, then the day-to-day comparison with a self-use battery and with no
  solar or battery.
- **Special events table** split into columns:
  - Axle kWh;
  - what Axle paid you;
  - what that energy was worth otherwise;
  - Axle net;
  - free power;
  - day-to-day energy (metered);
  - the standing charge, on its own, so it no longer hides in the everyday figure.
- **Axle net is fairer:** the energy an event uses is now costed at the overnight rate the battery is refilled at
  (with its losses), and solar at the export rate. Before, grid-charged energy was costed at the peak rate, which
  made Axle look worth less than it is.

## 0.9.53 (beta)

### Behaviour changes
- None. Card only.

### Costs page
- The savings waterfall is now drawn as columns, left to right. It starts at "no solar or battery" on the left,
  cascades down through each saving, and ends with what you paid on the right. On a phone the labels and values
  shorten to fit; tap a bar for its exact value.

## 0.9.52 (beta)

### Behaviour changes
- None to control. Cost reporting and the Costs page only.

### Costs page (#162)
- **Whole-day scenarios.** Each day is now costed five ways, using the same measured house, car and solar:
  - no solar or battery;
  - solar only;
  - plus your EDF tariff;
  - plus a battery on plain self-use (simulated);
  - PowerEngine (what you actually paid).

  Unlike the old S0–S4 steps, these can be compared directly with what you paid.
- **Where the savings came from:** a waterfall chart for yesterday, 7 days, this month or 30 days. It starts from
  "no solar or battery" and steps down through solar, the tariff, a self-use battery and PowerEngine to your
  everyday cost. It then adds the battery carry-over and Axle (including Axle's £1/kWh payments) to reach what you
  paid.
- **Battery carry-over:** charging tonight for tomorrow no longer makes today look dear. The day's change in
  battery charge is valued at the overnight rate, with the metered figure shown alongside. Axle and free-power
  events are kept out of it.
- A headline for yesterday, the last 7 days and this month, and a plain table of what each scenario assumes.
- The old cost-layer and energy tables are under **Detail and checks**. They attribute one day's cost for checking
  and aren't alternatives to compare.
- The page title no longer shows "Costs" twice.
- Past days are recalculated once after updating (cost method 5).

## 0.9.51 (beta)

### Behaviour changes
- None to control. Dashboard only.

### Monitoring
- **The Energy flow diagram now follows your configuration** (#160). Before, it was fixed. Now:
  - Each enabled solar plant gets its own panel, with its name.
  - The battery size comes from the capacity PowerEngine uses, and the floor from your minimum reserve.
  - The car appears only when an EV charger is set up.
- It updates when you save the configuration, and once more after start-up, when the real battery capacity is
  known. Refresh the dashboard to see it.
- New sensors: one power sensor per solar plant, `sensor.pe_state_solar_<id>_power`. They're added and removed
  as plants are. `sensor.pe_state_solar_power` is still the total.

## 0.9.50 (beta)

### Behaviour changes
- None. This release reorganises the inverter code so PowerEngine can later support other inverters. What it
  does to your inverter should be exactly the same.

### Under the hood (making PowerEngine generic, step 2)
- Everything specific to the Solis inverter now sits in one place, a Solis "adapter". That covers timed windows,
  RAM remote control, supervised tests and clock sync. The rest of PowerEngine asks the adapter what to write,
  and keeps the safety rules itself: dampening, the daily write limit, read-back checks and the write journal.
- The recorded-night replay test now also covers pause and resume, a restart mid-night, a failed read-back, the
  daily write limit, supervised tests and clock sync. Every change above passed it unchanged.

### Worth checking after updating
- Control carries on as before (RAM remote control, writes today, the Next line). If anything looks different,
  send a diagnostics export in the morning.

## 0.9.49 (beta)

### Behaviour changes
- None to control.

### Health
- **Dismiss** on each Health finding (admins). A dismissed finding stays hidden. A new or changed one shows again,
  since each day's check has its own title. Dismissals are logged and listed under the findings. The red *Health
  needs a look* tile on Monitoring goes away with the finding.
- The battery-ledger check is fairer: it flags a correction over 3 kWh **or** 10% of the day's battery
  throughput, whichever is larger (a busy arbitrage day moves 30+ kWh).
- **PowerEngine log** card at the bottom of Health: warnings by default, or all recent lines, newest first. The log
  is now saved across restarts (`log.json` beside the config) and still goes into the diagnostics export.

### Updates
- **New version check every 5 minutes, straight from GitHub** (HACS only looks every few hours). A blue *New
  version available* tile appears on Monitoring and taps through to Configuration. There the update card says
  which version is released and shows **what's new**: the changelog of every release since yours, newest first,
  with older ones as links when there's a lot.

## 0.9.48 (beta)

### Behaviour changes
- None to control.

### Dashboard: Monitoring
- **Next:** under the current decision, the plan's next two changes, e.g. "sell 16:00–19:00, then charge 23:30–05:00
  tomorrow" (new `sensor.pe_plan_next`).
- **Saved today** tile: today's saving against no solar or battery, with any Axle or free-power event value
  included (new `sensor.pe_cost_saved_today`); tap for the Costs tab.
- **Health needs a look:** a red tile that only appears when the Health tab has findings; tap to open it. The
  heartbeat, config-OK and version tiles moved to the Health tab.
- **Mode** now reads from `sensor.pe_state_status`: Active (green), Paused (orange), Passive (blue), Blocked or
  **Stopped** (red). Stopped is new: control stopped because inverter writes couldn't be confirmed, which until now
  only showed in the log while the tile still said Active.

## 0.9.47 (beta)

### Behaviour changes
- None to control.

### Dashboard: Monitoring tidied
- **Mode** is coloured by state: green Active, orange Paused, blue Passive, red when blocked (not configured, inputs
  not ready, or Active asked for but refused by a guard).
- The status box and the Activity list are merged. The top line (bold) is what PowerEngine is doing now, with
  today's earlier decisions listed below it, so it grows through the day. The mode's reason shows above it when not
  Active. The activity log now keeps 48 entries (was 20), enough for a busy day.
- **Now** holds the battery charge gauge plus the battery, grid, solar and house gauges, above the car, smart-charge,
  Axle and free-power tiles. The separate Battery % and Decision tiles are gone (the gauge and the activity's top
  line say the same).
- The *Last 24 hours* graphs are removed; the Plan history tab covers them.

## 0.9.46 (beta)

### Behaviour changes
- None to control.

### Updating
- **The Update button now makes HACS check GitHub first.** HACS only looks for new releases every few hours, so the
  button found nothing new straight after a release. It's now a PowerEngine card: it asks HACS to refresh both
  PowerEngine repositories (admins only), then runs the update script. It shows progress, and offers *Reload page*
  once the new version is running.

## 0.9.45 (beta)

### Behaviour changes
- **A full car no longer holds the battery through a smart slot.** If the latest EDF smart slot (run for at least 15
  minutes in the last 12 hours) passed with the car plugged in but drawing nothing, the car is taken as full. Coming
  smart slots are then planned as ordinary cheap time: arbitrage and charging allowed, no car load. Before this, a
  slot that hadn't started yet was always assumed to feed the car, so on 28 Sep the plan held the battery through
  EDF's 09:00–15:30 slot (6.99p) after two slots where the car drew nothing. If the car does start charging, the
  car-charging rule holds the battery at once, and the next slot's evidence resets the assumption.

## 0.9.44 (beta)

### Behaviour changes
- None to control: this only counts.

### Diagnostics
- **Simulated timed-window writes while on RAM control.** The virtual timed-window inverter (the one dampening
  uses) now also runs while PowerEngine drives the inverter by RAM remote control. It follows the same plan and
  decisions, so it counts the EEPROM writes timed windows would have made.
  - The Monitoring tile is now **Writes today (real · simulated)**: real EEPROM writes, then the simulated count
    using your dampening settings.
  - Health → Inverter writes today explains it. The plan under RAM switches more freely (RAM switch cost 0.5p
    against 5p for windows), so the simulated figure is an upper estimate of what a timed-window install would see.
  - The dampening 7-day table keeps filling while on RAM control.

## 0.9.43 (beta)

### Behaviour changes
- None.

### Updating
- The version line on Configuration names the app and the card instead of HACS's entity ids. The update script's
  notification says which of them it installed (handover package; picked up at your next HA config sync).

## 0.9.42 (beta)

### Behaviour changes
- None to control.

### Updating
- **One-button update.** Configuration now shows the running version and the installed/available versions of the
  app and card, with an **Update** button. It runs a new script in the handover package,
  `script.powerengine_update`, which:
  1. asks HACS for the latest releases;
  2. installs whichever of the app and card have an update;
  3. restarts AppDaemon if the app changed, and waits for the new version to report in;
  4. leaves a notification saying what's running (refresh the browser for the new card).
  The dashboard updates itself when the new app starts. Needs the updated `docs/ha/powerengine_handover.yaml` in
  `/config/packages/`, and HACS's update entities for both repositories enabled.
- The automatic restart after an update stands aside while the button's script is running, so AppDaemon isn't
  restarted twice.

## 0.9.41 (beta)

### Behaviour changes
- None.

### Dashboard
- Page titles are left-aligned, the same size on every page.
- Configuration and Tests now use the same layout as the other pages (sections, up to three columns wide) instead
  of stretching across the whole screen.

## 0.9.40 (beta)

### Behaviour changes
- None.

### Dashboard
- Every page now starts with its title (Monitoring, Plan, Plan history, Costs, Health, Simulator, Configuration,
  Tests), because on a phone only the tab icons show.

## 0.9.39 (beta)

### Behaviour changes
- **Discharge slow-down near empty is learned** (part of *Learn: charge and discharge slow-down*). From full-rate
  sales, PowerEngine measures how much of the discharge rate is reached in half-hours running below 40%, 30% and 20%.
  Your battery runs at about 4.4 kW below 40% against 4.8–5.2 kW higher up. The plan then runs deep sales and
  evening self-use at that speed. The learned discharge rate is also taken from above 40% now, so the slow tail no
  longer drags it down.
- **Inverter conversion losses are learned** (new *Learn: inverter conversion losses*, on). From full-rate
  half-hours with no solar, it measures how much grid energy reaches the battery when charging and how much of the
  battery's output reaches the house and grid when selling. The plan uses the battery's own round trip × both
  conversions: the grid-to-grid efficiency that arbitrage actually gets. Until now the plan used only the battery's
  own round trip (95.6%, measured at the battery); from 26–27 Sep the grid-to-grid figure looks nearer 80%. So
  arbitrage is now only planned where it pays after all losses. Cost accounting keeps the battery's own round trip,
  since it values the battery's own flows.
- Both re-learn from recent half-hours, so they follow any change of inverter, battery or an added battery. Health
  → Learned from use shows the figures, the samples, and the grid-to-grid round trip in use.

## 0.9.38 (beta)

### Behaviour changes
- **Axle exports are valued at Axle's £1/kWh plus the export rate** (new option *Axle also earns the export rate*,
  on by default, under Axle events): EDF pays its normal 15p on the same export, so an Axle kWh is worth £1.15.
  - Planning counts the full £1.15 when deciding whether to top up before an event.
  - Plan and decision text reads "£1.15/kWh (£1 Axle + 15p export)" instead of "£1/kWh".
  - Event figures on the Costs page: *gross* is kWh × £1.15. *Net* now adds the 15p the battery energy earns; exported
    solar is still credited with Axle's £1 only, since it would have earned the 15p anyway.
  - Past days are re-valued once on start (cost method 4), so earlier Axle events are corrected too.

## 0.9.37 (beta)

### Behaviour changes
- **Check meter energy counters.** Two new optional inputs under Grid and house, *Check meter import today* and
  *Check meter export today* (suggested: the Zappi's `grid_import_today` / `grid_export_today`). With *Use the check
  meter* on, they're preferred over the inverter's counters for the simulator's history import, hour by hour, with
  the inverter's figures filling any hours the check meter doesn't have.
- **Past cost days are rebuilt with the check meter.** The daily cost record now reads the check meter from HA
  history too (it already did live since 0.9.36), and days recorded before are re-measured where history allows,
  so past import/export no longer carry the Solis meter's ~16% overstatement.

### Other
- The configuration catalogue no longer carries role groups (the card places roles by its own topics). That takes it
  from about 15.6 KB to 14.5 KB, well under Home Assistant's 16 KB attribute limit.

## 0.9.36 (beta)

### Behaviour changes
- **The check meter is used for grid power** (new option *Use the check meter*, on by default, under Grid and house).
  While the check meter (the Zappi's grid CT) is reporting, PowerEngine uses it for grid import/export. It corrects
  the inverter's house load by the same difference, because the inverter's house load is its own meter plus its AC
  flow. On 28 Sep the Solis meter read about 16% high both ways. That made the house load about 0.9 kW too high while
  charging and read 0 while selling. If the check meter goes quiet for 3 minutes, the inverter's meter is used again.
  The learned usage profile is corrected too: the 14 days of history are adjusted half-hour by half-hour from both
  meters' history, and replace PowerEngine's own uncorrected record where they overlap.
- **Car charger inputs no longer stop control when they drop out.** The myenergi readings come from a cloud service;
  when they briefly go unavailable, PowerEngine carries on with the car assumed not charging and logs it, instead
  of handing the inverter back to Self-Use (it did for 2.5 minutes at 03:01 on 28 Sep). If they're unmapped or
  missing altogether they still count as not ready, and a long outage still notifies after 15 minutes.
- **Overnight switch cost** (new setting under Selling, 3p): with deeper selling overnight, each switch between
  charging and selling inside the fixed overnight window counts at least this in the plan. At 3p it only settles
  near-ties. Two cycles overnight usually trade more energy than one (about 30p a night more on 28 Sep's prices),
  so a single deep cycle takes about 10p here, or a battery wear figure of about 4p/kWh.

## 0.9.35 (beta)

### Behaviour changes
- **Grid charging stops at the half-hour's target.** Once the battery reaches the target the plan set for this
  half-hour, PowerEngine holds (the grid covers the house) until the next half-hour, as the plan assumed. Before this
  it kept charging to the end of the half-hour: overnight on 28 Sep it charged to 82% against a 76% target. With timed
  windows this can cost one extra write (the charge current set to 0) when a target is reached part-way through.

### Fixes
- Attribute sizes are now measured as Home Assistant stores them (compact JSON). The earlier figure was about 6% high,
  so the configuration catalogue was reported at 16.6 KB when it's about 15.6 KB.

## 0.9.34 (beta)

### Behaviour changes
- None.

### Diagnostics
- Every sensor's published attributes are now measured. If one passes 15,000 bytes, PowerEngine logs a warning once.
  Home Assistant stops recording a sensor's history above 16,384 bytes. The diagnostics export lists the largest
  ones. As of today only the configuration catalogue is close (about 15.4 KB); the next largest is about 10.6 KB.

## 0.9.33 (beta)

### Behaviour changes
- None. Card-only fix (Check meter moved under Grid and house); version kept in step with the card.

## 0.9.32 (beta)

### Behaviour changes
- None. Nothing new is written to the inverter; this release only reads and reports.

### Diagnostics
- **Grid meter cross-check.** A new optional input, Configuration → Grid and house → **Check meter**, takes a second,
  independent grid reading (suggested: the Zappi's grid CT, `sensor.myenergi_…_power_grid`). PowerEngine compares it
  with the inverter's meter, split by what the battery is doing (charging, discharging, idle). It shows the result on
  `sensor.pe_diag_grid_check`: the live difference, averages per battery state over the last 3 days, and a one-line
  verdict. This is to find out why the house load reads about 1.6 kW high whenever the battery charges from the grid.
- The diagnostics export now includes 24 h of history for the raw grid, check-meter, battery, house-load and car
  readings behind PowerEngine's figures, plus the cross-check sensor.

## 0.9.31 (beta)

### Behaviour changes
- After a start (an update or restart), the half-hour that's running is no longer held to whatever the first,
  unsettled plans chose. Mid-half-hour stickiness (which stops near-ties flip-flopping the inverter) now waits until
  the next half-hour begins, instead of only the 5-minute warm-up. This fixes an unexplained "hold" in the running
  half-hour after an update, when selling or charging would have paid.

## 0.9.30 (beta)

### Behaviour changes
- None.

### Dashboard
- The battery and grid gauges have short names (the long ones were cut off on a phone), with a line under them
  saying in words which way each is flowing and how fast (for example "discharging 5.06 kW", "exporting
  3.99 kW"), and a key to the gauge colours.

## 0.9.29 (beta)

### Behaviour changes
- None.

### Fixes
- **Monitoring flow card: the battery's direction was the wrong way round** (dots leaving the battery while it
  charged). The Sunsynk card reads battery power with the opposite sign to PowerEngine's (+ discharging), so it's
  now inverted in the card.

## 0.9.28 (beta)

### Behaviour changes
- None by default.

### New
- **Deeper selling overnight** (Config → Selling, on by default): 0.9.27's behaviour, one deeper overnight sale
  instead of many shallow cycles. Untick it and the arbitrage band's bottom holds inside the overnight window too
  (a hard floor, as it is the rest of the day).

## 0.9.27 (beta)

### Behaviour changes
- **Overnight arbitrage is one deeper cycle, not many shallow ones.** Inside the fixed overnight window the refill is
  guaranteed, so selling below the band's bottom no longer counts the outside-band cost there (down to the
  reserve plus 10%, as before). The plan then sells once and refills once, instead of cycling 90↔75% several
  times: the same energy and the same money (on a typical night the earnings were identical), with far fewer
  switches. Outside the overnight window the band's bottom is still a hard floor, and the top of the band still
  applies overnight until the final top-up.

## 0.9.26 (beta)

### Behaviour changes
- **With arbitrage on, grid charging stays within the arbitrage band, except for the final top-up.** Charging from
  the grid now stops at the band's top (90%) all day and through the night, and cycles stay inside the band. Only
  the last half-hours of the fixed overnight window, just long enough to charge from the band's top to the
  grid-charge target (100%) plus one to spare, may go above it, so the battery still starts the morning full for
  the day (and winter) ahead. Free-power sessions still fill to 100%. Before, the top was only a 2p/kWh cost, so
  evening cycles often ran up to 100%. Solar can still fill the battery above the band in the day.
- The plan chart's legend no longer shows a meaningless value for *Action* (it's for the hover text).

## 0.9.25 (beta)

### Behaviour changes
- None.

### Dashboard (#114, from the Trial tab)
- **Monitoring:** the energy flow is now the **Sunsynk Power Flow Card**, including the car, with a row of needle
  gauges below it: battery (charging ← → discharging), grid (export ← → import), solar, house and battery %.
  Install *Sunsynk Power Flow Card* from HACS if you haven't (Power Flow Card Plus is no longer used).
- **Plan:** hovering or tapping a half-hour on the plan chart now also shows **Action**: what PowerEngine plans then,
  and why. Units are shown for every value.
- The Trial tab is gone.

## 0.9.24 (beta)

### Behaviour changes
- None.

### New
- **Trial tab, B (Sunsynk flow): the car.** Shown as a load within the total. The card's load circle now uses a new
  sensor, `sensor.pe_state_load_power` (house + car), with the car's own power as one of the loads in it.

## 0.9.23 (beta)

### Behaviour changes
- None.

### Fixes (Trial tab)
- **B** Sunsynk flow: cut back to the documented minimum configuration (the extra options gave a configuration
  error).
- **D** Last 30 minutes: now HA's own history graph (the ApexCharts version stayed on "loading" at a 30-minute
  span).

## 0.9.22 (beta)

### Behaviour changes
- None.

### New
- **Trial tab** (#114): side-by-side options for a clearer live picture and plan, to choose from:
  - **A** the current flow card, tuned (coloured values, new flow-rate model);
  - **B** the Sunsynk Power Flow Card (install *Sunsynk Power Flow Card* from HACS first);
  - **C** needle gauges for battery (charging ← → discharging), grid (export ← → import), solar, house and battery %;
  - **D** the last 30 minutes of house, battery, grid and solar power;
  - **E** the plan chart with hover detail: each half-hour's planned action and the reason.
  A line at the top shows PowerEngine's decision and the inverter command. The chosen views will move to the
  Monitoring and Plan tabs, and the Trial tab will go.

## 0.9.21 (beta)

### Behaviour changes
- **RAM remote control never asks for more than the inverter accepts.** Reading the inverter's registers (the new
  PowerEngine Modbus probe) showed it refuses a force power above 5000 W (register value 500): asked for 5200 W,
  it kept the previous 2000 W, which is why charging and selling ran at about 2 kW. A new setting, *RAM max power*
  (default 5000 W, Config → Inverter control), caps the commands, and with RAM remote control the plan uses it as
  the charge and discharge rate. Timed windows are unchanged.

## 0.9.20 (beta)

### Behaviour changes
- **A restart no longer defers the current half-hour's plan.** Right after a start, the first plan is made before
  the load profile (about 30 s later) and the weather have loaded, and it often picks Self-Use. The mid-slot rule
  (0.9.8) then kept that choice for the rest of the half-hour, so a planned charge or sale was skipped until the
  next half-hour. For the first 5 minutes after a start the mid-slot rule is now off, so the plan settles on its
  real choice at once.

## 0.9.19 (beta)

### Behaviour changes
- **RAM remote control: the power is written before and after the mode, and again 5 seconds later.** A force
  charge asked for 5200 W ran at about 1900 W, close to the 2000 W left over from the earlier test: the power
  written straight after the mode change hadn't taken. Now it's written, then the mode, then the power again, and
  once more after 5 seconds; each refresh sends the power either side of the mode too.
- The "not following" notification and log now include what the inverter's power and mode settings read, to tell
  a setting that didn't take from a limit elsewhere (battery, BMS or fuse).

## 0.9.18 (beta)

### Behaviour changes
- **Cheaper switches with RAM remote control.** With the Control method set to RAM remote control, the optimiser
  counts *RAM switch cost* (default 0.5p) per switch between Self-Use, charging and selling instead of the
  *Window change cost* (5p), since a switch is only a temporary setting there. The plan can then take short
  arbitrage cycles it used to pass up. Timed windows keep the 5p cost. Both are on the Config tab under Inverter
  control.

## 0.9.17 (beta)

### Behaviour changes
- **A full car no longer blocks arbitrage in later smart slots.** When the car is plugged in but the charger reports
  the charge complete (Zappi "Completed"), PowerEngine no longer assumes the car will draw power in coming EDF
  slots, so those slots are planned as cheap house slots (charge and sell), not "charge the battery with the car".
  Before, only the rest of a slot already running was freed. If the car does start charging, the car-charging rule
  takes over at once (the battery holds or charges; it never feeds the car).

## 0.9.16 (beta)

### Behaviour changes
- None until you choose it: the Control method defaults to Timed windows, as before.

### New
- **RAM remote control** (Config → Inverter control → *Control method*). Drives the inverter through SolaX
  Modbus's *Battery control override* entities (Solis registers 43135 with the power in 43136/43129) instead of the
  timed windows: grid charge → Force charge at the planned power, hold → Force charge at 0 W, sell or Axle →
  Force discharge at the planned power, Self-Use → Off. Temporary settings, so no EEPROM writes. A change is sent
  at once; a force command is re-sent every *RAM refresh* (default 1 minute) so it stays inside the inverter's
  timeout (about 5 minutes on firmware 420044, measured on the Tests tab). If PowerEngine, AppDaemon or HA
  stops, the inverter returns to Self-Use by itself within about 5 minutes.
  - When it takes over, the timed windows are closed once and then left alone. Pausing, Passive, leaving Active
    for any reason, or switching back to Timed windows turns remote control Off.
  - **Monitoring:** PowerEngine checks the battery follows each command (90 s to respond; charging at least half
    the asked power unless nearly full, discharging at least half unless near the reserve, holding not
    discharging). If it doesn't for 3 minutes you're notified. If the remote-control entities go missing, it falls
    back to the timed windows and tells you.
  - **Status:** a new *Inverter control* tile on the Monitoring tab (method, command, whether the inverter is
    following it); details in the Health tab's control preview. Remote-control changes are journalled but not
    counted as inverter writes; refreshes aren't journalled. The dampening rules and planned-writes forecast
    apply to the timed windows only.

## 0.9.15 (beta)

### Behaviour changes
- **Battery health (SOH) and the inverter's minimum SOC are no longer flagged as stale.** They're settings rather than
  readings and can go years unchanged, so only a missing or unavailable entity is reported now.

## 0.9.14 (beta)

### Behaviour changes
- None.

### New
- **Dampening is measured.** Alongside the real inverter, PowerEngine runs the same plan and decisions on three
  virtual inverters (no dampening, restart hold-off only, and both) and counts the writes each would make. The
  Health tab's *Inverter writes today* shows the last 7 days: writes with no dampening, and what the restart
  hold-off saved and burst damping saved (or would have saved while off). The same summary appears in the
  config page's Dampening tuning section.
- A start-up test now runs PowerEngine's whole start-up against a stand-in AppDaemon, so a fault like 0.9.12's
  is caught before release.

## 0.9.13 (beta)

### Behaviour changes
- **Fix: 0.9.12 didn't start.** The restart hold-off was set up before the config was loaded, which stopped the app
  starting (no entities, dashboard blank). No other changes.

## 0.9.12 (beta)

### Behaviour changes
- **Restart hold-off (on by default).** For 5 minutes after PowerEngine starts, or control resumes or goes Active,
  nothing is written to the inverter: the plan and its inputs settle first, and the inverter keeps running the
  windows already set. Changes driven by safety (an Axle event, free power, the car charging, the minimum reserve)
  never wait; pausing still returns the inverter to Self-Use at once.

### New
- **Dampening tuning** section on the config page: *Restart hold-off* (on) with its time, and *Burst damping* (off
  for now, to evaluate after a few steady days): the first change to a window slot, current or the mode goes straight
  through, and another change to the same thing within the burst window (10 min) waits until the plan has been
  steady for the settle time (5 min), so a burst of changes becomes one write. The Health tab shows how many changes
  were held back today; the control preview shows why writes are being held.

## 0.9.11 (beta)

### Behaviour changes
- **EDF slot requests wait 15 minutes after a restart.** No ready-by change is sent until PowerEngine has been running
  for 15 minutes and EDF's dispatch and ready-by entities have been available for 15 minutes. Straight after a
  restart of AppDaemon, HA or the EDF integration, an empty slot list may only mean it hasn't loaded yet, and a
  request would make EDF re-plan for nothing.
- **Request results count half-hours gained and lost.** A re-plan can also move or drop slots already planned, so a
  request now only counts as a success if it gained more slot time than it lost; the back-off follows the same rule.
  The Health tab shows "+gained / −lost half-hours".
- A ready-by entity coming back after a restart (unavailable → a time) is no longer logged as a request by your
  automations.

### New
- The diagnostics export includes the slot requests and the slot record.

## 0.9.10 (beta)

### Behaviour changes
- None.

### New
- **Planned writes on the plan chart:** purple bars along the bottom of the Plan tab's chart show how many inverter
  writes the plan implies at each coming half-hour, if it runs as it stands (real writes only: update-button
  presses, currents, mode). Below the Actions table: the total for the next 24 hours and today's count so far.
  It's an estimate: replans, the settle delay and mid-slot changes aren't modelled.

## 0.9.9 (beta)

### Behaviour changes
- **New windows share a slot's update where they can.** Each of the inverter's three update buttons sends that
  slot's charge *and* discharge times in one write. When a new charge or discharge window needs a free slot,
  PowerEngine now prefers a slot whose button is being pressed anyway for the other kind, so a new charge window
  and a new discharge window usually cost one press rather than two. (Already the case: one press per slot per
  change, never one per window; the currents are separate single writes shared by all three slots.)

## 0.9.8 (beta)

### Behaviour changes
- **No flip-flopping part-way through a half-hour.** A replan in the middle of a half-hour keeps the action the
  inverter is already doing unless changing it gains at least 15p. The plan's next half-hour still takes over at the
  boundary as normal. Near-ties had been switching between hold, charge, sell and Self-Use every few minutes, each
  switch costing a current write and window updates.
- **The daily write limit remembers a resume across restarts.** Resuming after the limit lets PowerEngine carry on
  for the rest of the day, but an AppDaemon restart (an update, for example) forgot that and tripped the limit again
  straight away: back to Self-Use and more writes each time. The resume point is now saved with the write counts.

## 0.9.7 (beta)

### Behaviour changes
- None.

### New
- **Diagnostics export** on the Health tab (admins): one JSON file with the settings, the inverter write log (48 h),
  the plan, PowerEngine's recent log lines, live entity states (PowerEngine's, the mapped inputs and the inverter
  controls) and 24 h of battery, grid and mode history. Download, Share or Copy it, then upload it when there's no
  shell to pull files. Account numbers, serials and similar attributes are removed. A copy of the app's part is kept
  in `/homeassistant/powerengine/diagnostics/` (last 5).

## 0.9.6 (beta)

### Behaviour changes
- **Write counts (and the daily write limit) now count only real inverter writes.** SolaX Modbus keeps the timed
  windows' start/end times in Home Assistant and only sends them when the update button is pressed (one block
  write), so setting those numbers doesn't write to the inverter. They were counted as writes, which roughly
  tripled the count (last night: 170 journal entries, 43 real writes) and tripped the daily limit early. Button
  presses, charge/discharge currents, the storage mode and the remote-control settings still count. The staged
  times are still journalled and shown separately. The same daily limit now allows about three times as many real
  changes; lower it on the Config tab if you want the old protection.

### New
- **Writes today** tile on the Monitoring tab (tap for details).
- **Inverter writes today** at the top of the Health tab: count, changes, daily limit and what's left, writes seen
  from all sources, a breakdown by reason and the latest 30 writes (time, setting, old → new, why). The EEPROM wear
  table moved up with it. New sensor `sensor.pe_diag_writes_today`.

## 0.9.5 (beta)

### Behaviour changes
- None to normal control. New supervised tests write to the inverter only when you start one.

### New
- **Tests tab** (right of Config) with the supervised tests moved there from Config, a *Pause control* switch, and
  clear instructions per test: what it does, what to watch for on the inverter screen, and what PowerEngine checks.
- **RAM remote-control tests** for the Solis *Battery control override* registers (43135, with power in
  43136/43129): force charge, force discharge, hold (0 W force charge) and a failsafe test that stops the command
  being re-sent (SolaX Modbus reload, no Off written) and times how long the inverter takes to drop it by itself.
  Each samples battery and grid power every 30 s, checks the timed-window settings didn't change, and gives a
  verdict. First step towards control without EEPROM writes (#101).
- Test timelines now include grid power.

## 0.9.4 (beta)

### Behaviour changes
- **With arbitrage on, sitting above the arbitrage band costs a little (battery wear).** Where two plans earn the
  same, the plan now sells or uses the top of the battery first and fills it last, instead of charging to 100%
  early and parking there for hours before selling. Overnight this removes the "stops just after midnight" idle at
  full; the battery still ends the cheap window full.

## 0.9.3 (beta)

### Behaviour changes
- **The arbitrage band's bottom is a hard floor outside the fixed overnight window.** Selling there never takes
  the battery below *Arbitrage band: bottom* (75%): the refill might rely on optional smart-charge slots that EDF
  can withdraw, and the band keeps enough in the battery if it does. Inside the overnight window, where the refill
  is guaranteed, selling may still go deeper when it pays (down to the reserve plus 10%). Before, the band was only
  a 2p/kWh cost everywhere, which a 15p sale easily outweighed, so the plan sold down to ~35% in the middle of a
  daytime smart slot.

## 0.9.2 (beta)

### Behaviour changes
- None. Plan and Plan history charts: charging is green and selling red; the battery level is a bold blue line
  (its plan or alternative a light blue one); the kWh axis is grey; charge and sell blocks are more see-through and
  solar export more solid, so solar export shows through.

## 0.9.1 (beta)

### Behaviour changes
- **The inverter's "update times" button is pressed a few seconds after the new window times, not with them.** The
  Solis button sends the window entities' current values to the inverter. Sent in the same burst, it could go
  before the new values had landed and send the previous ones, so the inverter ran one change behind the plan.
  Last night that looks to be why the battery sold from 00:30 while PowerEngine was holding it: the inverter still
  had an earlier discharge window. The button now waits until the new values read back (up to about 12 s), and
  the read-back check runs after that.

## 0.9.0 (beta)

### Behaviour changes
- **Far fewer inverter writes** (last night: 318 in 9 hours, which hit the daily limit). From the write journal:
  - **A running window's start is no longer moved forward every half-hour.** The plan's period always starts at the
    current half-hour, so a window running since 21:30 was rewritten to 22:00, 22:30… (2–3 writes each time, for
    every running window). A running window with the right end time is now left alone.
  - **Only periods starting within 4 hours are programmed.** Windows for tomorrow afternoon were written overnight
    and rewritten each time the plan changed its mind about them. They now wait until they're 4 hours away; a
    window the plan no longer wants is closed once it's that close.
  - **Changes to later windows wait for the plan to settle.** A change that doesn't touch anything running or due
    within 30 minutes is written only once the plan has wanted it for 10 minutes, so a plan that flips and flips
    back (03:40 and 03:45 last night: 35 writes) writes nothing.
  In a simulated night with the plan remade every half-hour, the windows are programmed once and then only real
  changes are written.
- **The arbitrage band applies overnight too.** Its outside-band cost now counts in the fixed overnight window as
  well; the requirement to be full by 06:00 still wins, so the last top-up happens at the end of the window.

## 0.8.15 (beta)

### Behaviour changes
- None. Dashboard: phones in landscape now get the phone charts too (the phone version is used below 600 px wide
  **or** below 500 px tall; a landscape phone is wide but short), and the phone charts have more room on the right
  so the last time label and the Now marker aren't clipped.

## 0.8.14 (beta)

### Behaviour changes
- **The rest of a running EDF dispatch counts at its real price.** Only dispatches that haven't started yet are
  weighed by how likely they are. The rest of one already running was also weighed (6.99p looked like ~9.7p), so
  the plan paused charging in it to wait for the guaranteed overnight rate. If EDF ends a running dispatch early,
  the plan is remade straight away, as before.

## 0.8.13 (beta)

### Behaviour changes
- **Smart slots inside the fixed overnight window aren't discounted.** The planner weighs an uncertain EDF slot's
  price by its likelihood against the price without it. Inside the overnight window that price is the same cheap
  rate, but it was weighed against the peak rate, so slots there looked dearer (~9.7p) than the unslotted end of the
  window (6.99p). The plan then held for hours and charged at the very end. They're now counted at the real price.
- **Charge early in the overnight window.** With the same price all night, the optimiser now prefers charging
  sooner rather than leaving it all to the last hours (a tiny cost per half-hour of delay, overnight only), so a
  slow or cold battery still finishes by 06:00.

## 0.8.12 (beta)

### Behaviour changes
- **A smart slot the car has finished with is just cheap time.** If the car is unplugged, or plugged in but not
  charging while its dispatch is already running (finished or stopped), the rest of that dispatch no longer holds
  the battery for the car: the plan may charge, hold or sell in it like any cheap period (e.g. sell at 15p and
  refill at 6.99p). Later dispatches still expect the car unless it's unplugged. The plan is remade as soon as the
  car starts or stops charging.
- **Overnight car slots charge straight to full.** Inside the fixed overnight window, charging alongside the car
  goes to the grid-charge target (100%) instead of stopping at the arbitrage band's top (90%) and topping up after
  the car slot ends. Daytime car slots still stop at 90%.

## 0.8.11 (beta)

### Behaviour changes
- **Only real tariff prices are shown.** The plan chart's price line, every reason, the Actions table, the Plan
  history and the EDF-slot notes now show a smart slot's own price (e.g. 6.99p). The certainty-weighted figure is
  used only inside the planner's sums and never displayed. The EDF-slot note now reads *slot at 6.99p: 88% likely
  to happen (the plan allows for it not happening)*.
- Charging the battery alongside the car in a smart slot is decided on the slot's own price, not the weighted one.

## 0.8.10 (beta)

### Behaviour changes
- **EDF slots that carry on count as delivered.** EDF re-lists a running dispatch from the current half-hour
  (18:10–04:00 becomes 18:30–04:00), which was recorded as *cut short* and dragged the slot certainty down (to
  about 53%, so the plan counted a 6.99p slot as ~17.9p). A slot that vanishes while running with a new one starting
  within 10 minutes is now a continuation, and past records like that are re-read the same way (your history
  goes from ~53% to ~88%).
- **Prices shown as on the tariff:** the Actions table and reasons show a smart slot's own price, with how likely it
  is (e.g. *6.99p, 88% likely*), instead of the certainty-weighted figure the plan uses internally.
- **Three windows retried:** if the inverter's `_2`/`_3` window entities weren't found (e.g. at start-up before the
  integration was ready), PowerEngine used the single rolling window until AppDaemon restarted. It now looks again
  every 10 minutes and logs when it falls back.

### New
- **Write journal:** every inverter write is recorded with its time, entity, old and new value and why
  (`/homeassistant/powerengine/write_journal.json`, last 3 days), to find where writes add up.

## 0.8.9 (beta)

### Behaviour changes
- **Notifications go to Home Assistant's notification area by default** (the bell; a persistent notification per
  problem, dismissed automatically when it's over). Before, nothing was sent until a phone notify service was
  chosen. *Send to* now offers the notification area, your phones, or Off; configs that never chose one get the
  notification area.
- Health tab *Inputs*: a handover guard AppDaemon has lost track of is checked with Home Assistant directly (as
  control already does since 0.8.6), so it no longer shows *missing* when it's there.

## 0.8.8 (beta)

### Behaviour changes
- **Riding through restarts:** when required inputs go missing while live (e.g. an HA restart, with the inverter
  integration reconnecting), PowerEngine now stops making changes but leaves the inverter's programmed windows
  running for 10 minutes, instead of immediately closing them and writing them again when the inputs return. If
  they're still missing after 10 minutes, it returns the inverter to Self-Use and notifies you, as before. Fewer
  inverter writes, and a charge in progress isn't interrupted.

## 0.8.7 (beta)

### Behaviour changes
- **The battery charges alongside the car at a cheap rate.** In a car smart-charge slot at a cheap price (with
  *Top up when cheap* on), the plan now grid-charges the battery too, up to the top-up level (the arbitrage band's
  top with arbitrage on, else the grid-charge target), instead of just holding. Live: if the car is charging at a
  cheap rate the plan didn't expect, the battery charges too. The fuse limit still applies (house and car first).
  At a peak rate the battery still holds.

## 0.8.6 (beta)

### Behaviour changes
- **Guards checked with Home Assistant when AppDaemon has lost track of them.** AppDaemon keeps its own copy of
  HA's states and can miss an entity that was re-created after it started (Predbat restarting does this), which
  showed as *switch.predbat_set_read_only is None* while the switch was really on. When AppDaemon has no state for a
  guard, PowerEngine now asks HA directly (a template render) and rechecks every 30 s. Only if HA also has no such
  entity does it count as absent (safe, as in 0.8.5); if HA can't be asked, the guard is *unverified* and never
  counts as safe. This closes a gap in 0.8.5, where a re-created Predbat switch AppDaemon couldn't see would have
  counted as absent even if it was off.

## 0.8.5 (beta)

### Behaviour changes
- **A handover guard that doesn't exist counts as safe.** If a guard entity is missing, unknown or unavailable
  (e.g. Predbat's read-only switch while Predbat isn't connected to Home Assistant), PowerEngine no longer refuses
  Active: something that isn't in Home Assistant can't be controlling the inverter. It logs it, notifies you once
  (*handover guard not available*) and lists it in the Operation mode's `guards_absent` attribute. A guard that
  exists and is in the wrong state still stops control, as before.
- **Handover package:** *Restart Predbat if it didn't connect* also runs if the switch goes missing for 10 minutes
  while HA is running (not only after an HA restart), at most once an hour.

## 0.8.4 (beta)

### Behaviour changes
- **Quicker recovery when inputs come back:** while required inputs are missing (e.g. the SolaX Modbus integration
  reconnecting after an HA restart), PowerEngine now rechecks them every 30 s instead of every 5 minutes, so it
  goes back to Active within half a minute of them returning.

## 0.8.3 (beta)

### Behaviour changes
- **Restart after an update:** when HACS installs a newer version, PowerEngine notices within a minute (the version
  on disk differs from the one running) and fires `pe_update_installed` once. The handover package's new
  *PowerEngine - Restart AppDaemon after an update* automation restarts AppDaemon (add-on `a0d7b954_appdaemon`)
  30 s later and notifies you to refresh the browser.
- **Handover package (HA side):** everything is now named *PowerEngine - ...* (entity IDs unchanged), plus two more
  automations: *Restart AppDaemon if PowerEngine stops* (heartbeat 7 minutes old; at most once an hour) and
  *Restart Predbat if it didn't connect* (5 minutes after HA starts, if Predbat's read-only switch is missing;
  add-on `6adb4f0d_predbat`).

## 0.8.2 (beta)

### Behaviour changes
- None. The Config page is reorganised by topic, with search, filters and colour for required and optional
  inputs (card 0.8.2). The inverter's export-limit entity is now labelled *Inverter export limit (entity)* so it
  isn't confused with the *Export limit* setting (the kW your DNO allows).

## 0.8.1 (beta)

### Behaviour changes
- None by default. The choices behind 0.8.0 are now settings:
  - **Learn features, one per figure:** *Learn: charge slow-down near full*, *where discharging stops*, *export
    ceiling*, *car charge rate* (all on). They replace 0.8.0's single *Use learned limits* (ignored if saved).
  - **Battery location** (*Cold battery* settings): garage or outbuilding (24 h, the default), outside (6 h),
    inside (72 h) or custom (*Battery warm-up time*).
  - **Temperature source:** new optional inputs *Outside temperature* (your own sensor wins over the forecast for
    the hours it has seen) and *Battery temperature* (the battery's own sensor: the estimate ahead starts from it
    and the cold learning uses it). Unmapped: Open-Meteo forecast only, as in 0.8.0.
- The role catalogue leaves out `required` when it is "yes" (the card fills it in), to stay under HA's 16 KB
  attribute limit.

## 0.8.0 (beta)

### Behaviour changes
- **Cold-battery caution** (new feature, on by default): the plan expects charging at 50% of the normal rate while
  the battery is estimated to be below 4 °C, so it starts overnight charging earlier. The battery temperature is
  estimated from Open-Meteo's outside temperature (last 3 days and next 3, fetched hourly for your home zone's
  location), lagging it by the *Battery warm-up time* (24 h). Caution lasts until the battery is 3 °C above the
  threshold. New *Cold battery* settings: threshold, rate, release margin, warm-up time.
- **Learned limits** used for planning once there's enough data (Health tab, *Learned from use*): charge and
  discharge rates (*Use measured* on *Max charge/discharge power*, ticked by default), charge taper near full,
  reserve where discharging stops (only raises it), export ceiling and car charge rate (*Use learned limits*
  feature, on by default), cold threshold and rate (*Learn cold behaviour*, on by default).
- The inverter is still asked for the configured charge and discharge rates; learned figures only shape the plan.
- Each recorded half-hour now also stores what PowerEngine asked the inverter for, the outside temperature and the
  estimated battery temperature (the data the learning uses).

### New
- Entities: `sensor.pe_diag_learned` (the table) and `sensor.pe_diag_battery_temperature`.
- Plan tab: a note listing cold-caution periods. Health tab: *Learned from use* table and battery temperature.

## 0.7.7 (beta)

### Behaviour changes
- None. Dashboard: the Simulator tab moves to just before Config.

## 0.7.6 (beta)

### Behaviour changes
- None. Dashboard only.

### Dashboard
- **Charts on phones** (screens under 600 px wide): each chart has a phone version with only its main axis
  (the others still scale the lines; tap the chart for exact values), no sideways axis titles, smaller labels, the
  legend on top and a shorter span: *Next 18 hours* instead of 36, 5 days of daily costs instead of 8, 14 days of
  losses instead of 30. Desktop and tablet views are unchanged apart from the names below.
- Shorter series names everywhere (Battery, Alt plan, Price, Solar, Load, Charge, Export, Solar export, …), and
  every series now carries its unit (%, p/kWh, kWh), shown in the tooltip.

## 0.7.5 (beta)

### Behaviour changes
- None. Fix: *Operation mode* (`sensor.pe_state_operation_mode`) now accepts **paused**. Before, HA rejected that
  value and kept showing the previous one (usually *passive*), so the Battery controller panel showed ✗ while
  PowerEngine was correctly paused for testing.

### Card
- Battery controller panel: an entity it can't read (e.g. Predbat's read-only switch while Predbat is restarting) now
  makes the status *not fully live*, with a note, instead of *live*.

## 0.7.4 (beta)

### Behaviour changes
- **The Battery controller switch now leaves the chosen controller fully live.** Choosing *PowerEngine* sets
  *Operation* to **Active** (saved in the config, with a backup) and un-pauses it; choosing *Predbat* sets it to
  **Passive** (which closes PowerEngine's windows) before Predbat leaves read-only. Done through a new event,
  `pe_set_control` (`operation: active|passive`), fired by the handover package's scripts. The PowerEngine handover
  waits for *active* and notifies **PowerEngine is live** or **did NOT go live** with the reason.
- Handover package: the old `battery_pause_powerengine` script is gone (nothing uses it).

### New
- Battery controller panel: a status line (live / paused for testing / not fully live), **Pause for testing** and
  **Resume (go live)** buttons while PowerEngine is selected. Pausing is no longer shown as a fault.
- INSTALL.md: *Active mode* rewritten around the switch: how it works, one-off setup, testing, first go-live.

## 0.7.3 (beta)

### Behaviour changes
- **Handover package (`docs/ha/powerengine_handover.yaml`), HA side:** `input_select.battery_controller` now offers
  only **Predbat / PowerEngine** (Predbat first, so it's the fallback if the saved choice was *Legacy automations*).
  Handing to Predbat is now its own script, `script.battery_handover_to_predbat`: pause PowerEngine, wait until it
  has left Active and closed its windows, keep the legacy automations off, then turn Predbat's read-only off. It no
  longer calls the Predbat package's `predbat_handover_to_predbat`. Handing to PowerEngine waits 10 s after Predbat
  goes read-only before resuming. Nothing in the app changed.

### New
- **Battery controller panel** at the top of the Config tab (card 0.7.3, `custom:powerengine-handover-card`):
  Predbat | PowerEngine buttons with a confirm step, *Switching…* while a handover runs, and a table of what Predbat
  read-only, the legacy automations, PowerEngine pause and PowerEngine mode should be against what they are, with
  *Re-apply* when they don't match.

## 0.7.2 (beta)

### Behaviour changes
- None. Health tab: the *Inverter control preview* now shows the three-window design: each charge and discharge
  window PowerEngine wants against what the inverter holds now, both currents and the storage mode (0.7.1 showed
  only "The preview appears once PowerEngine has made a decision").

## 0.7.1 (beta)

### Behaviour changes
- **All three inverter windows** (Active; previewed in Passive). The plan's next charge periods (grid charge or hold)
  and sell periods within 24 hours are programmed into the Solis's three charge and three discharge windows at once
  (the `_2`/`_3` entities are found from the first window's), split at midnight. A window is only rewritten when its
  period has passed and the slot is needed, or when the plan moves it by more than 30 minutes (periods starting
  within 2 hours are always set exactly). Hold and charge share the charge current, set for the window running or
  the next one due. A repeating night therefore costs next to no writes. Without the extra windows it falls back to
  the rolling single window.
- **Watchdog automation** added to `docs/ha/powerengine_handover.yaml`: closes every window (Self-Use) if
  PowerEngine's heartbeat stops for 15 minutes while it's the battery controller.
- **The fixed overnight window** (learned from your rates: 23:00-06:00 for EDF Go Electric) is marked in the plan.
  Inside it the arbitrage band doesn't apply, and with *top up when cheap* on the optimiser must end it at the
  grid-charge target (100%), so it can sell deep and refill once rather than shuffle between 75% and 90%.
- **Window change cost** (new setting under *Inverter control*, default 5p): counted by the optimiser each time the
  plan switches between self-use, charging and selling (a hold/charge change counts a fifth), so it only switches
  when that clearly pays. On 24 Sep: 17 switches down to 10, for 10p less.
- **Car charging follows the plan:** while the car charges, the battery charges if the plan says so (to the plan's
  level, not 100%), and otherwise holds; it never feeds the car.
- Health tab: inverter writes for the three-slot design are counted alongside the others (*would*), so you can see
  the rate on your own plans while in Passive.
- Simulator results are recomputed overnight with the new rules.

## 0.7.0 (beta)

### Behaviour changes
- **The optimiser now plans the battery** (#67; new feature *Optimised planning*, on by default). Every re-plan, the
  optimiser chooses each half-hour's action for the lowest cost over the plan (same forecasts, prices, battery
  physics, fuse and export limits, battery wear, the arbitrage band and its outside-band cost). The rule-based plan
  still runs alongside: its reasons are kept where both agree, and new plain-English reasons explain the rest
  ("charge at 6.99p to sell at 15p from 13:00", "sell at 15p: refilled at 6.99p from 17:00", "keep the charge
  for 16:00 (30.28p)"). Grid charging targets the level the optimiser plans for that half-hour.
- Safety rules the optimiser always keeps: the battery never feeds the car in a smart-charge slot (hold or charge
  only), a sale never takes the battery below the reserve plus 10%, Axle events force-discharge, free-power sessions
  fill the battery, and the live overrides (car charging, reserve, Axle not yet started) still apply.
- Plan tab: says which method planned, and how much more the rule-based plan would cost (thin green line).
  Turning *Optimised planning* off goes back to the rule-based planner.
- Simulator: the planner table now compares the rule-based planner with the optimiser.

## 0.6.5 (beta)

### Behaviour changes
- **The arbitrage band is a guide, not a limit.** Selling below the band's bottom (75%) is allowed when it still pays
  after a new *Arbitrage outside-band cost* (default 2p/kWh, on top of wear) and the forecast load is still covered:
  the battery must reach the next cheap period with the reserve plus 10% and no half-hour before it may import more
  than it would have. Grid-charging above the band's top (90%) works the same way in the optimiser; routine cheap
  top-ups still stop at the top, and a forecast shortfall can still charge higher. Setting the cost to 0 ignores the
  band; a high value keeps arbitrage inside it.
- Simulator results are recomputed overnight with the new rule.

## 0.6.4 (beta)

### Behaviour changes
- **Arbitrage band** (new settings under *Arbitrage*): *Arbitrage lowest charge* (default 75%) and *Arbitrage highest
  charge* (default 90%). Selling from the battery now stops at the lowest charge (before: the reserve plus 10%), so
  arbitrage only ever uses the top of the battery. With arbitrage on, routine cheap top-ups (overnight and in
  smart-charge slots) stop at the highest charge instead of the grid-charge target, keeping the battery out of the
  full zone while it cycles; a forecast shortfall can still charge it higher. Arbitrage runs in any cheap period,
  smart-charge slots included. Passive-only effect until Active is chosen.
- Simulator: the optimiser keeps to the same band; PowerEngine's planner run now uses the planner's own charge
  targets (before, every grid charge went to 100%). Cached results are recomputed overnight.

## 0.6.3 (beta)

### Behaviour changes
- None to your devices.
- **Fix: export income on days rebuilt from HA history.** Half-hours rebuilt before the export-rate sensor had any
  history were valued at 0p for export, which understated export income on the Costs tab (11-24 Sep for you) and
  in the Simulator. They now fall back to your current export rate. The cost history is re-valued once on start-up
  (cost method 3) and the Simulator recomputes its cached results in the next overnight run.
- With that fixed, the planner comparison on 11-25 Sep shows PowerEngine's planner level with the best achievable
  (not £3 a month behind, as 0.6.2 reported); topping up overnight and exporting the next day's surplus solar is
  not a loss when export pays more than the cheap import plus losses and wear.

## 0.6.2 (beta)

### Behaviour changes
- None to your devices.
- **Simulator phase 4: equipment** (#54). New settings on the Simulator tab for a bigger battery (usable kWh, power,
  cost), more solar (your kWp now, extra kWp, cost; recorded solar scaled, same roof direction) and a second car
  (miles a year, kWh a mile, charger power; charged in each day's cheapest half-hours). Each ticked item, and all
  of them together, is run on your tariff and the best other tariff and compared with the same tariff without it,
  with payback once there's a year of history.
- **Simulator phase 5: PowerEngine's planner against the best achievable.** Your tariff and the three best others
  are also run by PowerEngine's own planner (knowing each day's load and solar). The tab shows best achievable,
  planner and the gap per month, next to what you actually paid.

## 0.6.1 (beta)

### Behaviour changes
- None to your devices.
- **Simulator phase 2: a year of history** (#54). The new set-up card on the Simulator tab reads up to a year of
  hourly energy (house, car, solar, grid) from Home Assistant's long-term statistics whenever an admin has the
  page open, a month at a time, and hands it to the app (the app has no admin token, so it can't read them
  itself). Imported days are priced on your tariff at today's rates (from the tariff code on your rate sensor)
  and marked estimated. The tab then shows a year-long ranking alongside the last 30 days; notifications use the
  year once there are 90 days or more.
- **Simulator phase 3: heat pump.** Settings on the Simulator tab (gas used a year, boiler efficiency or a
  heat-loss figure, hot water, COP at -3 and 12 C, pump size, pre-heating hours, gas prices, installed cost).
  Overnight, hourly outside temperatures for your home location come from Open-Meteo (free, no key; cached).
  Heat demand = the house's heat loss (from a year of gas use) x degrees below 15.5 C, plus hot water; hot water is
  heated in the cheapest half-hours and heating may run up to the pre-heat hours early. Your tariff, heat-pump
  tariffs and the five best others are each run with the heat pump and compared with the same tariff plus gas:
  cost a month, heat-pump kWh, seasonal COP, and payback once there's a year of history.
- Your region is now taken from your tariff code (was fixed at A).

## 0.6.0 (beta)

### Behaviour changes
- None to your devices. The Simulator only reads your records and public tariff lists.
- **Tariff Simulator, phase 1** (#54). Each night at 01:30 (spread over short slices so AppDaemon stays
  responsive) PowerEngine:
  - reads the current household tariffs from the Octopus and EDF public tariff APIs (region A), once a day;
  - fetches each tariff's historical half-hour rates and standing charges for your recorded days (cached, so
    later nights only fetch the new day);
  - replays each recorded day on each tariff with the optimiser running the battery, carrying the charge from day
    to day (your tariff with the prices you paid is the baseline; other tariffs have the car's charging moved to
    the cheapest half-hours; selling from the battery only if Arbitrage is on; battery wear counted in choices);
  - ranks them on the new **Simulator** tab (last 30 recorded days, per month, against your tariff and what you
    actually paid).
  - **Notifications** (new *Tariff opportunities* type, on by default): when a tariff would have saved at least
    £5 and 5% a month over at least 14 days (at most once a month per tariff), and when new tariffs are published.
  - Switch it off with the new *Tariff simulator* feature. Data is kept in `/homeassistant/powerengine/simulator/`.
- The optimiser can count battery wear in its choices (used by the Simulator; the Plan tab comparison is unchanged).

## 0.5.18 (beta)

### Behaviour changes
- **Smart-slot certainty** (#14). Each planned EDF slot gets a certainty from the slot history (delivered, cut
  short or cancelled), grouped by overnight/daytime and how far ahead it was announced; it starts at 70% and
  learns as slots finish. The plan prices an upcoming slot at certainty × slot price + the rest at the normal
  price, so it only relies on slots that usually happen (e.g. a slot that's often cancelled no longer counts as
  cheap for charging the battery). The slot in progress keeps its real price. Shown on the Plan tab (each slot's
  certainty and the price used) and the Health tab (overall and per group).
- **Estimated prices no longer copy yesterday's smart slots.** Beyond the published prices, each half-hour is
  estimated from the same time the day before; a cheap half-hour that was a smart slot (outside the usual
  overnight window) is now estimated at that day's normal (peak) price instead.

### Other
- **Plan history tab** (#58). Pick a day (up to 30 days back) and a plan (start of day, or the first plan of any
  hour) and see it against what happened: battery %, price, grid charging, battery and solar export, house load
  and solar (plan dashed, actual solid), the plan's actions, and the plan's expected cost against the actual
  cost for the half-hours it covered. The first plan of each hour is now kept for 60 days (plans-<date>.json);
  plans saved before this version only have the battery level.
- #15 (value charge left at the end of the plan) checked: the 36-hour horizon means the end of the plan never
  changes what happens now; a regression test covers it. No change.

## 0.5.17 (beta)

### Behaviour changes
- **Battery round-trip efficiency is now an input** (*Battery and inverter*, default 90.25%, i.e. 95% each way as
  before) with the same **Use measured** tick box as capacity. Ticked (default, as before), the measured figure is
  used once there are 14 days of data; unticked, the configured figure is always used. Changing it re-values the
  cost history.
- **Health tab: losses as a % line.** The system losses chart now plots each day's losses as a % of the energy
  handled (purple line, right axis, labelled) alongside the kWh bars. The % should be steadier than the kWh, so a
  jump points at a sensor or inverter problem.

## 0.5.16 (beta)

### Behaviour changes
- **"Use measured" for battery capacity.** A tick box next to *Usable battery capacity* on the config card, showing
  the measured figure once there is one. Ticked (the default, and what earlier versions did automatically), the
  measured capacity replaces the configured one for planning, simulation and costs once it's measured; unticked,
  the configured figure is always used. Changing it re-values the cost history with the capacity now in use.
- `sensor.pe_diag_battery_capacity` shows `use_measured` and `in_use_kwh`.

## 0.5.15 (beta)

### Behaviour changes
- None to your devices.
- **Plan chart: solar export** (#59). Forecast export of surplus solar (the battery full, or charging as fast as it
  can) is drawn as amber half-hour blocks on the kWh axis, separate from battery export (red). Battery export now
  counts only energy from the battery; before, a half-hour selling from the battery also included any solar
  exported alongside it.

## 0.5.14 (beta)

### Behaviour changes
- **Active mode is available.** Choose *Active* under Operation on the Config tab. PowerEngine then writes the
  Solis timed-slot settings (and, with smart-charge optimisation on, EDF's ready-by time), but only while every
  handover guard is safe and control isn't paused. Passive stays the default; nothing changes until you choose
  Active.
- **Inputs failing while in control:** the inverter is now returned to Self-Use (windows closed) and you're
  notified; control resumes when the inputs recover. (Before, writing just stopped.) A tripped handover guard
  still writes nothing.
- **Daily write limit** (new setting, *Inverter control*, default 150): if PowerEngine's own writes reach it in a
  day, control pauses (the pause switch turns on, returning the inverter to Self-Use) and you're notified.
  Resuming allows the limit again.
- **Inverter clock:** new inputs *Inverter clock* (`sensor.solis_rtc`) and *Sync inverter clock*
  (`button.solis_sync_rtc`). Drift is checked every 10 minutes (Health tab; a finding above 2 minutes). In
  Active mode the clock is synced weekly, and within 10 minutes if it's a minute or more out (e.g. when the clocks
  change on 25 October).

### Other
- Inverter writes: PowerEngine's own writes are counted separately (Health tab), and no longer double-counted in
  *observed*.
- `docs/ha/powerengine_handover.yaml`: an optional HA package with a PowerEngine / Predbat / Legacy selector
  that runs the right handover.
- Shorter role catalogue (sensor-only inputs no longer repeat their domain).

## 0.5.13 (beta)

### Behaviour changes
- **Supervised inverter test: the first thing that can write to the inverter.** Only when an admin starts it
  from the Config tab, ticks *I'm watching*, and the handover guards are safe. It writes one action's timed-slot
  settings (hold at 0 A, grid charge, force discharge or self-use) for 1 to 10 minutes, reads them back, logs
  battery power and SoC each minute, then returns the inverter to Self-Use (windows closed) and reads that back.
  Refused while PowerEngine is in control, while another test runs, or if any control output is unmapped. The
  window it writes ends 2 minutes after the test so a restart can't leave it open. Results on
  `sensor.pe_diag_test_write`, the log, the logbook and (if on) the health notification.
- **Handover guards.** New read-only inputs (*Handover guards*: another controller's read-only switch must be on;
  up to two other automations must be off). Active mode is refused unless at least one guard is mapped and all
  are safe, re-checked whenever one changes. If a guard trips while in control, PowerEngine stops writing and
  notifies you; it does not write anything more.
- **Pause control** (`switch.pe_ctl_pause`, top right of the Monitoring tab). In Active mode, pausing (or choosing
  Passive) returns the inverter to Self-Use once and then writes nothing until resumed; the mode shows *paused*.
  No effect in this Passive-only build other than the mode reason.
- Every build is still Passive-only for normal control.

### Other
- **Losses as a % of energy handled** (#53): system losses are also shown as a percentage of the energy supplied
  each day, labelled above each bar on the Health tab losses chart, with yesterday's and the average % in the text.
- Shorter input descriptions on the config card (the catalogue must stay under HA's 16 KB attribute limit).

## 0.5.12 (beta)

### Behaviour changes
- None to your devices: Passive-only. The control layer is built but cannot write: every build still forces
  Passive, and writing happens only in Active mode.
- **Inverter control layer (Active design).** Each decision is turned into the Solis timed-slot settings it needs
  (storage mode Self-Use; a charge window for grid charge/hold with the current from power ÷ 52 V, 0 A to hold; a
  discharge window for Axle/export; both closed for self-use; rolling windows at most 35 minutes ahead, never past
  midnight), compared with what the inverter holds now, and reduced to only the writes needed, with one press of
  the update button after any window-time change. In Active mode (not yet available) writes are rate-limited,
  read back after 6 seconds, retried once, and on a second failure control stops and you're notified; bump/boost
  entities are never written.
- **Health tab: Inverter control preview** (`sensor.pe_diag_control`): for the current decision, each setting
  PowerEngine would want next to what the inverter holds now, and how many writes that would take. Use it to check
  the control mapping before Active mode.

## 0.5.11 (beta)

### Behaviour changes
- None to your devices: Passive-only. The optimiser is for comparison only; it never drives decisions.
- **Optimiser alongside the planner.** Each re-plan, a dynamic-programming optimiser works out the lowest-cost
  plan over the same horizon (battery charge in 1% steps, the same physics, prices, limits and forecasts, charge
  left at the end valued at the cheapest price). The Plan tab says how much cheaper, if at all, its plan is than
  the planner's, and draws its battery line (thin green) on the chart. In tests it matches the planner on your
  normal tariff and finds more with arbitrage on (mainly more export cycles), which is what this comparison is
  for: seeing whether a smarter planner is worth switching to.

## 0.5.10 (beta)

### Behaviour changes
- None: documentation only.
- **Install guide consolidated (#10):** seven steps in order, with the config page walked through section by
  section as it is now, a new *First-day checks* step (what each tab should show, and when measured values take
  over), where PowerEngine keeps its files, an *Active mode* placeholder, and troubleshooting for costs, the
  unsigned battery sensor and inverter writes.

## 0.5.9 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- **Second write model for EEPROM wear (#44).** Alongside the rolling 35-minute window design, PowerEngine now also
  counts what a one-window-per-block design would write on the same decisions: each window runs to the end of
  the plan's block and usually expires by itself (no write to close), with an HA automation as the safety net
  instead of short windows. Both show on the Health tab against the 25-year target (about 11 writes a day).

## 0.5.8 (beta)

### Behaviour changes
- None to your devices: Passive-only. Nothing is sent to EDF in Passive mode.
- **Smart-charge optimisation (FR-8), Passive.** When it's worth it (car plugged in and not charging, no slot in
  the next 3 hours, import not already cheap, and the battery has room or arbitrage is on so cheap power could
  be sold for more), PowerEngine picks the next nearest ready-by time (different from the current one) with the
  charge target kept at 100%, checks 15 minutes later whether EDF added slots, and backs off 30 min, 1 h, 2 h,
  then 4 h if not, with at most 6 requests a day and at least 20 minutes between any two. In Passive mode these
  are recorded as "would" requests. Every other ready-by change (e.g. your four IO Schedule automations) is logged
  with whether EDF added slots, as the baseline. Health tab: *Asking EDF for slots*.

## 0.5.7 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- **Arbitrage planning.** With *Energy arbitrage* on (Config tab, off by default), the plan sells stored energy in
  the half-hours just before a cheap refill, latest first, while:
  selling beats buying it back (export price − refill price ÷ round trip − wear ≥ the minimum profit setting);
  the battery still reaches the refill with the reserve plus 10%; and nothing before the refill imports more.
  Uses the export limit. New plan action **Export**, shown on the Plan tab (reason gives the profit per kWh), in
  the Passive simulation, and in the Costs tab's arbitrage layer. On your tariff (15p export, 6.99p refill, 2p
  wear) that is about 5p/kWh. Check your export tariff allows exporting grid-bought energy before relying on it.

## 0.5.6 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- **Learned battery parameters (#6).** From the recorded half-hours PowerEngine now measures the battery's
  usable capacity (energy in/out against change in charge), the highest charge and discharge rates it has
  actually run at, and the lowest charge seen. New `sensor.pe_diag_battery_capacity`; shown on the Health tab.
  Once 14 full days and 100 samples agree with the configured capacity within 30%, the measured capacity is used
  for planning, simulation and costs (as the measured efficiency already is). Rates are shown, not used: they
  reflect how the battery has been run, not necessarily its limit. (Checked against your data for 25 Sep:
  18.1 kWh measured against 18 kWh configured.)

## 0.5.5 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- **Automatic cheap threshold (#12)**, on by default. What counts as cheap is worked out from the prices ahead:
  the bottom fifth of the price range, and only where storing the energy pays (the other prices x round-trip
  efficiency, less battery wear). The *Cheap import threshold* setting becomes the maximum. On today's EDF tariff
  (6.99p / 30.28p) nothing changes; it matters if the tariff changes or prices vary more. A flat tariff has nothing
  cheap; free or negative prices always count. The Plan tab shows the threshold used. Turn it off on the Config
  tab to use the fixed setting as before.

## 0.5.4 (beta)

### Behaviour changes
- None. Version kept in step with the card (config page shows suggested entities for unmapped inputs).

## 0.5.3 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- **Car charging at peak holds the battery again.** The 0.5.2 "cover the house, not the car" rule is dropped:
  a 7.4 kW boost dwarfs the house load, so it isn't worth the extra control.
- **Inverter write tracking (EEPROM wear).** New `sensor.pe_diag_inverter_writes` and a Health tab section:
  writes per day by your current setup (observed changes to the mapped control entities, e.g. Predbat pressing
  the update button) and what PowerEngine's Active design would make, each with how many years 100,000 writes
  would last at that rate.
- **Control inputs for the Solis timed slots** (Config tab, *Control outputs*): charge and discharge window
  start/end hour and minute, charge and discharge current, the update button and the storage mode. They replace
  the override inputs (pre-FB00 firmware has no override); old override mappings are dropped from config.yaml
  automatically. Map the new ones so writes can be counted.
- The settings list moved to its own sensor (`sensor.pe_map_settings`) to keep each under HA's attribute size
  limit. Needs card 0.5.3.

## 0.5.2 (beta)

### Behaviour changes
- None to your devices: Passive-only. These change what PowerEngine *would* do (decisions, Passive simulation):
- **Car charging at peak: the battery covers the house, not the car.** Previously PowerEngine would hold the
  battery; now it would self-use with the battery's discharge limited to the house's own load, so the house isn't
  pushed onto the peak rate. At the minimum reserve it still holds. (How to apply the limit to the Solis is part
  of the Active design; hold remains the fallback.)
- **Fuse limit in live decisions.** When grid charging would take house + car + battery over 90% of the main fuse,
  the decision says so and shows the reduced charge rate (the plan already allowed for it).
- **Smart-charge slot record.** Every EDF slot is tracked: ran (confirmed when EDF lists it as completed),
  cancelled before starting, or cut short, with the energy the car actually drew. Shown on the Health tab; it is
  the history for the slot-certainty score (#14).

## 0.5.1 (beta)

### Behaviour changes
- None to your devices: Passive-only. (Notifications are messages to your phone, not control.)
- **Phone notifications** through the HA companion app, set on the Config tab under *Notifications* (needs card
  0.5.1). Off until you choose a notify service. Then, each switchable: Health problems, inputs not working for
  15 minutes, Axle events scheduled, free-power sessions announced, and (off by default) a daily summary at 08:00.
  Each thing is sent once (an input that recovers can alert again later), at most 10 a day.

## 0.5.0 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- **Health tab** (new, between Costs and Config):
  - Findings: inputs PowerEngine's checks flag, plus sanity checks on yesterday's data: gaps in recording,
    battery power that never shows charging (an unsigned sensor), energy unaccounted for (or more used than
    supplied), and large battery-ledger corrections.
  - Battery round-trip efficiency and system losses, with losses by day for 30 days.
  - Plan vs what happened: each day's first plan is kept and compared the next day (house-load and solar
    forecasts vs actual, and how far the battery was from the plan).
  - Uptime: version, start time and heartbeat.
- New entities: `sensor.pe_diag_health` (ok / warnings / problems, with findings and accuracy as attributes) and
  `sensor.pe_diag_started`.

## 0.4.13 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- **Measured losses.** New diagnostic sensors `sensor.pe_diag_battery_efficiency` (round trip, measured over the
  last 30 days from the battery's energy in and out and its change in charge) and
  `sensor.pe_diag_system_losses` (yesterday's inverter/standby losses, with a per-day history). Once 14 full days
  are recorded, the measured efficiency replaces the configured 95% in the battery ledger, the default-battery
  simulation, the Passive simulation and the planner; costs are re-valued when it moves.
- **Fix: days weren't rebuilt after re-mapping the battery sensors.** Cost records now carry a flow id made from
  the method version and the inputs they were read from; when those inputs change (on save, or at start-up), the
  last 14 days are rebuilt from HA history automatically.

## 0.4.12 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- Costs tab: "Costs" on the left and a small **Alignment** switch at the far right of the same row, with no box
  around it. The icon shows the current alignment. Needs PowerEngine card 0.4.12 (new `powerengine-toggle-card`).

## 0.4.11 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- Costs tab: the alignment control is a small one-row tile at the far right, labelled "Alignment", with the toggle
  beside the name.

## 0.4.10 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- Costs tab: the Right-align numbers control is now a normal on/off toggle with its full name (0.4.9 showed two
  lightning-bolt buttons, HA's style for a switch whose state it can't confirm, and truncated the name).

## 0.4.9 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- Dashboard title is now "Power Engine". The sidebar name comes from `configuration.yaml`: change `title:` under
  `powerengine-dash:` to `Power Engine` (the install guide now shows this).
- Costs tab:
  - The **Right-align numbers** control is now a toggle switch at the top right, and starts **on** (it came up
    off in 0.4.8 because HA starts such switches off). Set once by PowerEngine; your choice is kept after that.
  - Chart: each day's bars are centred on its label; only the last 7 days and today are drawn (no clipped group
    at the left); the legend no longer shows misleading "0 GBP" values.
  - "£-0.00" now shows as £0.00; "1 half-hour recorded" grammar; tiny Axle flags (< 0.1 kWh) are not reported as
    the last event; the method text no longer points at an old version number.
- Cost backfill now also fills **today's** missing half-hours (before PowerEngine started, or cut short by a
  restart), so today's figures cover the whole day rather than just since the last restart.

## 0.4.8 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- Costs tab: a **Right-align numbers** toggle (`switch.pe_ui_right_align`, on by default) right-aligns every number
  column in the Costs tables; the day/month column always stays left-aligned. The setting is kept by HA and the MQTT
  broker, so it survives restarts.

## 0.4.7 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- With **Battery charging power** and **Battery discharging power** both mapped, the single **Battery power**
  sensor is no longer used or required (it can stay mapped or be cleared), and the pair become required. If either
  of the pair is unavailable, battery power is treated as unknown rather than falling back to the unsigned sensor.
  PowerEngine's check shows "Not used" for the single sensor. Needs card 0.4.7 for the matching config page.

## 0.4.6 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- Plan chart: grid charging (blue) and Axle export (red) are drawn as blocks filling each half-hour they happen
  in, instead of thin bars at the start of the half-hour that looked like short spikes.

## 0.4.5 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- **Fix: battery charging was counted as discharging.** Solis (via SolaX Modbus) reports battery power without a
  sign, so PowerEngine never saw the battery charge: the cost records showed 0 kWh in and all of it out, which is
  most of the "unexplained" cost. Two new optional inputs, **Battery charging power** and **Battery discharging
  power**, are used instead when both are mapped (Solis: `sensor.solis_battery_input_energy` /
  `..._output_energy`, which are power in W). **Map them on the Config tab.** This also fixes the battery direction
  on the Monitoring tab.
- Cost records now carry a flow-method version; days recorded the old way are rebuilt from HA history
  automatically (the nightly backfill, or ~90 s after a restart).
- The plan always covers at least 36 hours, using yesterday's prices (marked *est.*) beyond what EDF has
  published, so the chart has no empty tail.

## 0.4.4 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- **Fix: "Carried in battery" drifted negative every day.** Whenever the battery ledger ran empty it was re-seeded
  without counting the seed, so its later use showed as a loss. The ledger is now matched to the battery's real
  state of charge every half-hour; any correction lands in Unexplained. Cost method is now version 2 and all stored
  half-hours are re-valued automatically on start-up.
- **Energy by day** table on the Costs tab (import, export, solar, house, car, battery in/out, battery → export,
  unaccounted, ledger correction) to check the figures against the inverter's counters.
- Labels: "Battery (app)" is now "Battery control", and in Passive mode the page says these layers describe what
  your current setup (e.g. Predbat) did.

## 0.4.3 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- Plan chart: each vertical axis (labels, title and axis line) is drawn in the colour of what it measures:
  green for battery %, orange for import price, blue for kWh per half-hour (solar, load and the bars).

## 0.4.2 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- Config page settings are grouped into sections (Battery and charging, Supply limits, Axle events, Arbitrage);
  the app now tells the card which section each setting belongs in. Needs card 0.4.2.

## 0.4.1 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- **Cost backfill:** on start-up (and nightly at 00:20) PowerEngine fills any of the last 14 days that it didn't
  record, or only partly recorded, by replaying HA history through the same code it uses live. Live half-hours are
  never overwritten. Afterwards every stored half-hour is re-valued in time order so the battery ledger is continuous.
- Re-valuing also re-decides which half-hours were smart slots using the latest overnight window, so early days
  improve as more days are seen.
- History half-hours are marked `source: history` in the cost records.

## 0.4.0 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- **Cost accounting.** Every 30 s PowerEngine splits the energy flows (solar, battery, grid → house, car, battery,
  export) and values each half-hour at the rates seen. A first-in-first-out battery ledger tracks where stored
  energy came from and what it cost. Records are kept in `/homeassistant/powerengine/costs/`.
- **Costs tab:** daily layers S0 (no solar or battery) → solar → smart charge → battery (default) → battery (app)
  → arbitrage → actual, with "carried in battery" and "unexplained" so every day reconciles; today so far; a
  7-day chart; special events (Axle, free power) by month; and the full method.
- New entities: `sensor.pe_cost_today`, `sensor.pe_cost_days`, `sensor.pe_event_last`, `sensor.pe_event_months`.
- The standing charge input is now read (for costs).
- Counting starts from install; history backfill follows in 0.4.1, measured losses in 0.4.2.

## 0.3.8 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- New feature **Top up when cheap** (on by default): the plan charges to the grid-charge target in every cheap slot,
  as a buffer in case the forecast is wrong, instead of buying only what the forecast needs.
- The plan's saving now counts the value of extra charge left in the battery at the end (at the cheapest price in
  the period), so topping up isn't shown as a loss. Part of #15.
- Plan chart's grid-charge bars show only energy going into the battery, not the house's import.
- New settings for arbitrage (not built yet): **Export limit** (6 kW default), **Battery wear cost**,
  **Arbitrage minimum profit**. The arbitrage option on the config page warns to check export tariff terms.

## 0.3.7 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- Car charging is now taken from the Zappi plug status ("Charging"), not from charging power above 100 W. Charging
  power is only used if the plug status is unavailable.
- New settings **Main supply fuse** (A, default 60) and **Car charger power** (kW, default 7.4). The plan and the
  Passive simulation cap battery grid charging so house + car + battery stay under 90% of the fuse, reducing the
  battery first. **Set the fuse to 80 A on the config page.**

## 0.3.6 (beta)

### Behaviour changes
- None to your devices: Passive-only.

### Plan tab
- Summary text at the top is normal size.
- Actions table and headline show the day ("tomorrow", "Sun") for anything not today, including windows that run
  past midnight.
- Back-to-back grid-charge slots are shown as one window, and its target is the level the plan actually reaches
  (e.g. 87%) rather than the 100% ceiling.
- Charge reasons read "to avoid buying at 30.28p from 21:00 tomorrow" instead of the ambiguous "to cover 21:00 onwards".
- Chart: lines stop where the plan ends instead of being stretched flat to the edge, and the kWh axis is shown so
  the bars can be read.

## 0.3.5 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- Input checks no longer mark a power sensor as stale when it is sitting at 0 W. The Zappi's charging power only
  updates when it changes, so an idle charger was flagged stale, which made the inputs "incomplete" and stopped
  PowerEngine planning and simulating until the car next charged.
- The Axle direction input is no longer a warning when it is "unknown" between events.

## 0.3.4 (beta)

### Behaviour changes
- None to your devices: Passive-only.

### Fixed
- Logbook entries failed with `got multiple values for argument 'domain'` (AppDaemon reserves `domain`). A test now
  guards the logbook call's arguments.

## 0.3.3 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- House-load history is now read from HA **one day at a time** in the background (values only). A busy power sensor
  logs ~15,000 readings a day, and asking for 14 days at once returned nothing ("0 house readings").
- Logbook entries are written without an entity reference (AppDaemon turned it into a target, which the logbook
  service rejected with `invalid_format`).

### Fixed
- Plan tab: costs read "this plan earns £0.52 … plain self-use costs £0.00" instead of "£-0.52 vs £0.0".
- No more "Excessive time spent in callback" warning while loading history.

## 0.3.2 (beta)

### Behaviour changes
- None to your devices: Passive-only.

### Fixed
- Load history failed to load when an input had no readings in the period (typically the car, if it hasn't
  charged recently): the error `zip() argument 2 is longer than argument 1` discarded the house-load history too.
  The load forecast now learns from HA history as intended.

## 0.3.1 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- Until a load profile exists, the planner assumes a steady 500 W house load instead of the live house power
  (a momentary spike was being planned as a 36-hour load).
- PowerEngine keeps its own half-hourly record of house-only load in `/homeassistant/powerengine/load_history.json`
  (15 days), so the load forecast learns even if HA history can't be read, and survives restarts.

### Fixed
- Plan tab: the chart no longer overlaps the actions table.
- Reading load history from HA now copes with every shape AppDaemon returns, logs what it found, and retries hourly
  on failure.
- Plan tab warns while the load forecast is still learning.

## 0.3.0 (beta): "Plan"

### Behaviour changes
- None to your devices: still Passive-only.
- PowerEngine now **plans the next 24–48 hours** and the live decision follows the plan (live overrides first:
  Axle event now, free power now, car charging now). The fixed pre-Axle rule is replaced by the plan.
- New entities: `sensor.pe_plan` (full plan in attributes), `sensor.pe_plan_headline`, `sensor.pe_plan_next_mode`,
  `sensor.pe_plan_next_start`, `sensor.pe_plan_next_target_soc`, and `sensor.pe_state_sim_soc` (simulated battery).
- On start and daily at 00:10, PowerEngine reads 14 days of house-load history (and car power) from HA.

### Added
- Forecasts: half-hourly import prices (estimated from the previous day beyond published prices), Solcast solar,
  and a learned house-load profile (weekday/weekend, recency-weighted, car removed).
- Explainable planner: defaults per half-hour, forward battery simulation, then the cheapest earlier slot is chosen
  to fix each avoidable shortfall. Multi-hour Axle events are handled as a need to be met (top-ups worth doing at
  any price below £1/kWh). Every window has a reason.
- Simulated battery (Passive): the SoC PowerEngine would have produced, re-synced at midnight; shown next to the
  real battery on the Monitoring tab.
- Dashboard **Plan** tab: headline, 36-hour chart, actions table with reasons, expected cost vs plain self-use.

### Docs
- Install guide: ApexCharts Card is now needed (Step 5.1); Plan tab described; new troubleshooting rows
  (including HACS forgetting the repositories).

## 0.2.0 (beta): "Decide"

### Behaviour changes
- None to your devices: still Passive-only.
- PowerEngine now **decides** every 30 seconds what it would do, and why. The status sentence starts with it,
  e.g. *"PASSIVE. Would grid-charge to 100%: import is cheap (6.99p ≤ 10p)."*
- Each change of decision is added to an **Activity** log (last 20, kept across restarts) and to the HA logbook.
- New entities: `sensor.pe_state_decision` (self_use / grid_charge / hold / force_discharge) and `sensor.pe_state_activity`.
- *House power* only subtracts the car when **House load includes the car charger** is ticked (default: on).

### Added
- Rule stack, highest priority first: Axle event active → keep charge for an upcoming Axle event →
  free-power session → car charging (never let the battery charge the car) → cheap import → minimum reserve → self-use.
- Charge hysteresis so decisions don't flap near the target.
- Settings: minimum reserve, cheap-import threshold, grid-charge target, restart margin, Axle look-ahead and margin,
  house-load-includes-car.
- Dashboard: Decision tile, Activity list, and a **Config** tab (the config card now lives here).

### Docs
- Install guide: the config card is on the dashboard's Config tab (no separate dashboard); the dashboard step is now
  Step 5 with a copy-paste terminal method; Step 6 covers the new settings.

## 0.1.0 (beta): "See"

### Behaviour changes
- None to your devices: still Passive-only.
- PowerEngine now reads every mapped input each 30 seconds and publishes a normalised picture of the
  system: 13 new `sensor.pe_state_*` entities (status sentence, battery, grid, solar, house, car, rates,
  smart charge, Axle, free power). Power is in W with fixed signs: battery + = discharging, grid + = importing.
- The car's charging power is subtracted from the house load, so *House power* is the house only.
- PowerEngine writes its dashboard to `/homeassistant/powerengine/dashboard.yaml` on start.

### Added
- Status sentence, e.g. *"PASSIVE (monitoring only). Battery 71%, discharging 1.0 kW. Solar 0.1 kW,
  house 1.2 kW, exporting 0.2 kW. Import 30.28p, 6.99p from 21:00; export 15p. Next smart slot 21:00."*
- PowerEngine dashboard (Monitoring view): status, mode, battery, energy flow, rates, car, smart charge,
  Axle, free power, solar forecast, 24-hour history, health.

### Docs
- Install guide Step 7: register the dashboard (one-off) and install Power Flow Card Plus.

## 0.0.4 (beta)

### Behaviour changes
- None to your devices: still Passive-only.
- PowerEngine now reads `config.yaml` written by the config card, checks every mapped input
  (exists, right domain and unit, available, recently updated) and stays **unconfigured**
  until all required inputs are OK.
- New diagnostic entities: `sensor.pe_map_config` (mapping status and per-input checks) and
  `sensor.pe_map_catalogue` (the input catalogue the card uses).

### Added
- Input catalogue: every input's description, units, sign convention and suggested entity.
- Saving from the config card: validated, written atomically, previous version backed up
  (last 10 kept), logged in the HA logbook with who saved and what changed.
- Hard rule: control outputs can never be bump/boost entities.
- Features section in `config.yaml` (smart charge, arbitrage, Axle, free power).

### Docs
- Full installation guide (`docs/INSTALL.md`), including Step 6: configure PowerEngine.

## 0.0.3 (beta)

### Behaviour changes
- None to your devices: this build is Passive-only and never controls anything.
- New: PowerEngine now creates a **PowerEngine** device in HA (via MQTT) with five entities:
  `sensor.pe_diag_version`, `sensor.pe_diag_heartbeat`, `binary_sensor.pe_diag_config_ok`,
  `sensor.pe_cfg_operation_mode` and `sensor.pe_state_operation_mode`.

### Added
- Operation mode (`operation.mode: passive | active`, default passive). Active is refused by this build.
- Optional additional solar plants (`solar_plants:` list) in `config.yaml`.
- `remove_entities: true` removes every PowerEngine entity.

### Changed
- `operation.dry_run` replaced by `operation.mode`.

### Setup
- Requires AppDaemon's MQTT plugin (see README, "MQTT (one-off)").

## 0.0.2 (beta)

### Behaviour changes
- None. Still a scaffold that never controls anything.

### Fixed
- 0.0.1 read its own AppDaemon definition file as the settings file (AppDaemon passes every app a `config_path` argument). The optional override is now called `settings_file`, and unknown top-level keys in `config.yaml` are rejected.

### Docs
- README: AppDaemon add-on setup (`app_dir`) so it loads apps installed by HACS.

## 0.0.1 (beta)

### Behaviour changes
- None. Scaffold only: loads and validates `config.yaml` and logs the result. Never controls anything.

### Added
- Repository structure, HACS metadata, CI (lint, tests, HACS validation).
