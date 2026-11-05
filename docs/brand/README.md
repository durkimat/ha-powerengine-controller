# PowerEngine brand images

The engine symbol from the dashboard (the `pe:engine-v1` outline in the card) with a lightning bolt cut through it.
The SVGs here are the sources; the PNGs Home Assistant shows are in
`custom_components/powerengine/brand/` (Home Assistant 2026.3 and later read a custom integration's own `brand/` folder:
`icon.png`, `logo.png`, `dark_icon.png`, `dark_logo.png` and their `@2x` versions).

Regenerate them (librsvg's `rsvg-convert`):

```bash
for n in icon dark_icon; do rsvg-convert -w 256 $n.svg -o ../../custom_components/powerengine/brand/$n.png; rsvg-convert -w 512 $n.svg -o ../../custom_components/powerengine/brand/$n@2x.png; done
for n in logo dark_logo; do rsvg-convert -h 256 $n.svg -o ../../custom_components/powerengine/brand/$n.png; rsvg-convert -h 512 $n.svg -o ../../custom_components/powerengine/brand/$n@2x.png; done
```

The wordmark uses DejaVu Sans Bold (any bold sans-serif will do).
