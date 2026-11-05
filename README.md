# PowerEngine Card

The configuration page for [PowerEngine](https://github.com/durkimat/ha-powerengine-controller):
input mappings, feature switches and safety margins, edited with Home Assistant's
own entity pickers and validated before saving.

> **Status: early development (0.0.x).** Configures inputs, solar plants, features
> and mode. PowerEngine itself is Passive-only in 0.0.x.

## Install

This card is installed as part of PowerEngine. Follow the
**[PowerEngine installation guide](https://github.com/durkimat/ha-powerengine-controller/blob/main/docs/INSTALL.md)**
(Step 4 installs the card; Step 5 creates the admin-only config page).

The card and app don't need the same version. Each warns only if the other is older than the minimum it needs
(the card needs app 0.9.69 or newer; the app publishes the oldest card it works with). The card is only released when
it changes, so its version can be behind the app's.
