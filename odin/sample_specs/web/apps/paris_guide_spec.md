Build a Paris travel guide as a static, read-only multi-page React app. No backend, no user input, no feedback/comments. All data is hardcoded.

The UI should feel Parisian — warm color palette (cream, navy, gold accents), elegant typography, and subtle French-inspired design touches throughout. Pages should be visually creative, not plain data dumps. Each section page should have its own visual flavor based on its category (e.g. Food pages feel warm and inviting, Museums feel refined and gallery-like, Day Trips feel open and adventurous).

## Pages

### Home page
- Introduction to the guide.
- Links to all 5 sections below.

### Section page (one per section, all share the same layout component)
- Lists all entries for that section.
- Search/filter by keyword or tag.
- Clicking an entry navigates to its detail page.

### Entry detail page
- Full entry details: title, description, location/area, tags, and any section-specific fields.

## Section & entry contract

Every section page uses the same base layout component. Each entry follows this shape:

```
{
  id, title, description, area, tags: string[], imageUrl (optional)
}
```

Sections must be built in parallel — each section is a standalone data file exporting an array of entries. The shared section page component and entry detail component should be built first, then all 5 section data files should be created simultaneously.

## Sections and entries

### 1. Neighborhoods (5 entries)
- Le Marais
- Montmartre
- Saint-Germain-des-Prés
- Le Quartier Latin
- Belleville

### 2. Food & Drink (5 entries)
- Le Bouillon Chartier (classic bistro)
- Du Pain et des Idées (bakery)
- Marché des Enfants Rouges (market)
- Café de Flore (café)
- Le Comptoir du Panthéon (wine bar)

### 3. Museums & Landmarks (5 entries)
- Musée d'Orsay
- Musée Rodin
- Sainte-Chapelle
- Panthéon
- Palais de Tokyo

### 4. Day Trips (4 entries)
- Versailles
- Giverny
- Fontainebleau
- Provins

### 5. Practical Tips (4 entries)
- Getting around (metro, buses, Vélib')
- Money & tipping
- Safety & scams to watch for
- Useful French phrases


Lastly combine them together in a navbar.

Then update the homepage again with one section content from each. 
## General rules
- All guide content lives in static data files (`src/data/<section>.js`), one file per section.
- Use React Router for navigation.

The dev server is already running at http://localhost:5176/