# Karuselių šablonų duomenys

Biblioteka sudaroma tik iš JSON failų, todėl naujiems šablonams kodo keisti nereikia.

- `styles.json`: dizaino stiliai (Minimal, Luxury, ...).
- `topics/*.json`: viena kategorija viename faile, su jos temomis.
- Savi failai: `<Jarvis duomenys>/.jarvis/carousel_studio/library/templates/*.json`. Formatas tas pats. Juose gali būti `styles` ir (arba) `category` + `topics`. Tema su tuo pačiu `key` pakeičia įdiegtąją.

## Temos formatas

```json
{
  "category": {"key": "food", "label": "Maistas", "icon": "🍰", "order": 11},
  "defaults": {"styles": ["soft", "beige"], "button": "Išsaugok"},
  "topics": [{
    "key": "food_recipe",
    "title": "Receptas",
    "kicker": "Receptas",
    "cover_title": "Mano *mėgstamiausias* receptas",
    "subtitle": "Greita ir skanu",
    "items": [{"title": "Ingredientai", "body": "..."}, {"title": "Gaminimas", "body": "..."}],
    "cta_title": "Išsaugok *receptą*",
    "cta_body": "Parašyk, ar išbandysi",
    "button": "Išsaugok",
    "labels": ["Prieš", "Po"],
    "layouts": ["steps", "card", "photo_cover"],
    "styles": ["soft", "beige"],
    "tags": ["maistas", "receptas"],
    "popularity": 70,
    "added": "2026-10-03"
  }]
}
```

- Kiekvienas `items` punktas tampa viena vidurine skaidre (iš viso skaidrių = punktai + 2).
- `*žodžiai*` žvaigždutėse rodomi akcento spalva.
- Kiekvienas sąrašo `layouts` išdėstymas tampa atskiru šablonu. Išdėstymai: classic, photo_cover, big_number, card, split, editorial, checklist, quote, two_panel, qa, steps, product, poll, centered, collage.
- `labels` naudoja `two_panel` ir `qa` (pvz. „Mitas“ / „Faktas“).
- Netinkamas failas ar tema praleidžiami, o priežastis parodoma bibliotekos viršuje.
