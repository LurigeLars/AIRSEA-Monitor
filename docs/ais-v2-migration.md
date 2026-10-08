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

## Controlled Windows release (source-code-only procedure)

AIS v2 has been merged to `main`. An explicitly invoked local release wrapper is available at
`scripts/deploy-windows-pilot.ps1`. It **does not** run automatically after a GitHub merge.
The wrapper expects an existing v1 Windows pilot, a valid v1 database, exactly one running
pilot runner and an unmodified checkout of `main`.

1. Fetch a clean copy of the latest public repository. Review the release script before use.
2. Run `pwsh -File ./scripts/deploy-windows-pilot.ps1 -Apply` **from that checkout**.
3. The wrapper runs offline tests and a non-mutating database migration rehearsal,
   copies the existing application to a local backup, and makes a read-only SQLite
   pre-stop snapshot while the old collector remains running.
4. Only after preflight passes, it disables the scheduled task and terminates that
   exact pilot runner tree, captures a **final** consistent SQLite snapshot, atomically
   replaces the application folder with a staged copy containing the three changed
   Python modules, upgrades the stopped local database, and restarts the original task.
5. If a step after task shutdown fails, the wrapper attempts to restore original source,
   restore the final v1 SQLite snapshot including handling old WAL sidecars, and restart
   the original task. It retains local backups for manual recovery.

The required `-Apply` switch prevents accidental execution. The local backup stays on
the Windows machine. No database contents, secrets, absolute user paths or observation
records are uploaded to GitHub. If a host powers off during the atomic switch, a manual
recovery may still be required: do not assume automatic rollback across power loss.

**Validation limits:** GitHub CI checks the PowerShell syntax on Windows and exercises
synthetic SQLite backup/migration tests on Python 3.12 and 3.13. CI cannot simulate
the actual user's Task Scheduler security principal, process tree, or live AIS stream.
Inspect and retain the local backup until several successful collection cycles have
completed. Do not interpret empty eastern-zone counters as evidence of zero shipping.
