# AIS v2: zoncoverage och passagevalidering

Status: **förslag i PR, ej driftsatt**. Pilotens pågående Windows-uppgift och SQLite-fil ska inte ändras under utvärderingen.

## Vad v2 mäter

V2 räknar separata **giltiga AIS-positionsmeddelanden** och **unika pseudonymiserade fartyg** per UTC-dag för tre grova longitudzoner: W, M och E. De unika fartygsposterna lagras endast under 15 dagar, medan aggregerade dagsvärden kan finnas kvar längre. Följande kan **inte** utläsas: full geografisk AIS-täckning, faktisk trafikvolym, lastvolym, militär aktivitet eller om fysiska passager saknas.

Äldre v1-dagvärden innehåller inte historiska zonräknare. Migrationen lägger till nollställda nya räknare men markerar tidpunkten `ais_zone_tracking_since`. **Noll för en äldre dag betyder okänd historisk zoncoverage, inte bevisligen noll observerade fartyg.** På migreringsdagen är de nya räknarna partiella.

## Passageregler

- Måste gälla samma saltade, pseudonymiserade fartyg.
- Två separata sidor W→E eller E→W krävs; enbart M räcker inte.
- Den senaste positionen på föregående **bekräftade sida** används som tidsankare.
- Tidsskillnad måste vara 4 minuter till 6 timmar, och den nya positionsrapportens SOG 1–40 knop.
- En konservativ geografisk minsta sträcka mellan zonernas longitudgränser ger ytterligare kontroll att den implicita medelhastigheten inte överstiger 40 knop.
- Observationer som inte uppfyller villkoren får inte radera den föregående bekräftade sidan. Efter över sex timmars observationslucka sätts den nya sidan som baslinje **utan passage**.
- Daggräns 00:00 UTC rensar inte tillståndet. Passage bokförs på datumet för observationen på den nya sidan; varje fartyg/riktning räknas högst en gång per datum.
- Statisk fartygstyp kan bekräfta redan bokförda passager i efterhand utan dubbelräkning.

Datatolkning: leverantörens mottagnings-/ankomsttid används som tidsreferens; upstream-meddelandets individuella sändningstid kan skilja sig. Den konservativa minsta distansen utesluter inte alla fysiskt orimliga geografiska förflyttningar utan råa positionshistoriker. Dessa begränsningar måste följas upp innan användning utanför shadow-test.

## Kontrollerad migration utan driftstopp

PR:n innehåller `scripts/validate_migration.py`. Den använder SQLite:s online-backup för att läsa en konsekvent **kopia** av en existerande databas. Den ursprungliga databasfilen öppnas med `mode=ro` och migreras **aldrig**. Kopian skapas i en temporär katalog och raderas efter kontroll.

När grenen har hämtats till en separat arbetskatalog:

```shell
python scripts/validate_migration.py --db "<existing-pilot-database>"
```

Skriptet kontrollerar SQLite-integritet, schema v2, att samtliga tio äldre dagsräknare bevaras och att uppgraderingen är idempotent. Det skriver bara ut resultat av PASS/FAIL samt antal dagar och kontrollerade kolumner. Ingen fartygsinformation, sekretessbelagd sökväg eller credential ska skrivas ut.

## Releasevillkor

1. GitHub Actions grön för Python 3.12 och 3.13 samt syntetiskt Windows DPAPI-test.
2. Lokal v1→v2-övning på **SQLite-backup av den verkliga databasen**, utan att avslöja innehållet.
3. Granskning av output och ändringsdiff. Eventuell produktionsmigrering kräver planerat avbrott, backup, rollbackplan och explicit godkännande.
4. Efter driftsättning observeras W/M/E-räknare under ett tillräckligt lång tidsfönster för att kunna bedöma det mottagna urvalet. AIS-källa ensam bekräftar aldrig faktisk trafik eller fullständig täckning.

Den här PR:n installerar, startar, stoppar eller migrerar ingen produktionsuppgift.
