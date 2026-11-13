# Aktieanalys -- beslutsstöd för en liten svensk portfölj

Ett verktyg som varje vecka analyserar en portfölj med sju svenska bolag,
ett per kategori, och mejlar en rapport. Det är ett komplement till en
global indexfond.

**Programmet handlar aldrig åt dig.** Det hämtar data, räknar poäng,
flaggar risker och föreslår. Alla beslut, alla order, fattar du själv.

---

## Vad som är byggt så här långt

Projektet byggs i nio steg. Just nu är **steg 1 klart**: strukturen och
konfigurationen.

| Steg | Innehåll | Status |
|------|----------|--------|
| 1 | Projektstruktur, konfiguration, validering | **klart** |
| 2 | Datalager: yfinance, SQLite-cache, CSV-komplettering | **klart** |
| 3 | Universum: filtrering och datatäckningskontroll | kommer |
| 4 | Poängmodellen | kommer |
| 5 | Kvalitativt lager (Claude) | kommer |
| 6 | Bytesregler och kostnadsberäkning | kommer |
| 7 | Veckorapport som HTML och mejl | kommer |
| 8 | Schemaläggning via GitHub Actions | kommer |
| 9 | Backtest mot OMXS30 | kommer |

---

## Komma igång

Du behöver Python 3.11 eller senare. Kontrollera med `python3 -V`.

Installera programmet. Punkten på slutet betyder "det som finns i den
här mappen" och ska vara med:

```bash
pip install -e .
```

Kontrollera att allt hänger ihop:

```bash
python -m aktieanalys kontrollera-config
```

Du ska få en sammanfattning av dina inställningar. Får du ett
felmeddelande står det vilken fil och vilken rad-nyckel som är fel.

---

## Kommandon

Kontrollera konfigurationen och visa den i sammanfattning. **Kör det här
varje gång du ändrat i en YAML-fil** -- det tar en sekund och fångar
stavfel innan de blir felaktiga analyser:

```bash
python -m aktieanalys kontrollera-config
```

Lista alla nyckeltal som får användas, med förklaring av vad varje mäter
och om högt eller lågt är bra:

```bash
python -m aktieanalys visa-matt
```

Visa kategorierna med bolag och vilka mått de bedöms på:

```bash
python -m aktieanalys visa-universum
```

Kör testerna (42 stycken just nu). Gör det efter varje ändring i koden:

```bash
pytest
```

---

## Hämta data

Hämta allt (90 bolag, tar några minuter första gången):

```bash
python -m aktieanalys hamta-data
```

Prova med bara några bolag först:

```bash
python -m aktieanalys hamta-data --antal 5
```

Ett enskilt bolag:

```bash
python -m aktieanalys hamta-data --bolag VOLV-B.ST CAST.ST
```

Data som är nyare än `data.cache_dagar` (standard: 1 dag) hämtas inte
igen utan tas ur databasen. Vill du tvinga om en hämtning:

```bash
python -m aktieanalys hamta-data --tvinga
```

Kommandot avslutas med en täckningstabell: för varje nyckeltal, hur
många bolag som har det och hur många som saknar det. Det är där du ser
vad som behöver fyllas i för hand.

Se allt databasen vet om ett bolag, med källa för varje siffra och en
rad per mått som visar om måttet går att räkna:

```bash
python -m aktieanalys visa-bolag VOLV-B.ST
```

Se vad vi visste ett visst datum -- samma punkt-i-tiden-läsning som
backtestet använder:

```bash
python -m aktieanalys visa-bolag VOLV-B.ST --datum 2026-11-13
```

Statistik över databasen:

```bash
python -m aktieanalys visa-databas
```

---

## Fylla i det som saknas för hand

Åtta nyckeltal finns inte i yfinance och går inte att räkna fram. Utan
dem kan fastighetsbolag, investmentbolag och banker inte bedömas
rättvist. De fyller du i i `data/manuellt/`:

| Fil | För vilka | Vad du fyller i |
|---|---|---|
| `substansvarden.csv` | Investmentbolag, fastighet | substansvärde per aktie, och totalt |
| `fastighetsdata.csv` | Fastighet | förvaltningsresultat per aktie, belåningsgrad, räntetäckningsgrad |
| `bankdata.csv` | Bank/finans | kärnprimärkapitalrelation (CET1) |

**Filerna är redan förifyllda med rader** för de bolag som behöver dem
-- 7 investmentbolag, 7 fastighetsbolag och 7 banker, tre år tillbaka.
Du behöver bara skriva in talen i rätt ruta. Så här ser en rad ut före
och efter:

```
INVE-B.ST,2025,,            <- före: tomma celler, uppgift saknas
INVE-B.ST,2025,385.50,1180000000000
```

Formatet är en rad per bolag och år:

```
ticker,ar,substansvarde_per_aktie
INVE-B.ST,2025,385.50
INVE-B.ST,2024,342.10
INVE-B.ST,2023,298.40
```

Filerna går att öppna i Excel, Numbers eller Google Sheets -- spara som
CSV. Kolumnen `ar` är det räkenskapsår uppgiften gäller. Systemet bygger
två saker av varje kolumn: historiken (alla år) och senaste årets värde.
Minst 3 år behövs för de mått som mäter utveckling över tid.

Fyra regler, som alla ger tydliga felmeddelanden om du bryter dem:

* **Punkt som decimaltecken**, inga tusenavskiljare: `385.50`. Komma
  fungerar också, men punkt är säkrare.
* **Andelar skrivs som andelar:** belåningsgrad 45 procent skrivs `0.45`,
  inte `45`.
* **Tom cell betyder "uppgift saknas"** -- aldrig noll. Vet du inte, lämna
  tomt.
* **Ett bolag och år per rad.** Står samma kombination två gånger stoppas
  körningen.

Du behöver bara fylla i de bolag du faktiskt vill kunna jämföra. Ett
fastighetsbolag utan förvaltningsresultat utesluts ur sin kategori och
listas i rapporten med orsak -- det får aldrig en gissad siffra.

---

## Valutor -- en fälla värd att känna till

Åtta av bolagen i universumet handlas i kronor men **redovisar i en
annan valuta**: AstraZeneca i dollar, och Evolution, Hexagon, Nordea,
Betsson, Fenix Outdoor, Medicover och TietoEVRY i euro.

Yahoo är inkonsekvent med det. Uppmätt, inte gissat:

| Uppgift | Valuta |
|---|---|
| kurs, börsvärde, bolagsvärde | kronor (handelsvalutan) |
| `trailingEps` (vinst per aktie) | kronor |
| `bookValue` (eget kapital per aktie) | **rapportvalutan** |
| allt ur resultat- och balansräkningen | **rapportvalutan** |

Följden är att **Yahoos eget `priceToBook` är felaktigt** för dessa
bolag. AstraZeneca visas som P/B 56 i stället för 5,6, Nordea som 20,6
i stället för 1,9 -- fel med ungefär en faktor tio. Deras `trailingPE`
är däremot rätt, eftersom båda leden är i kronor.

Det här är inget randfall: Nordea är en bank, och P/B utgör 60 procent
av bankernas värderingspelare. Utan omräkning hade Nordea alltid
rankats sist i sin kategori, och felet hade inte syns någonstans i
rapporten.

Systemet hämtar därför valutakurser (USDSEK, EURSEK) från Yahoo och
räknar om räkenskapssiffrorna till kronor -- varje historiskt år med
kursen som gällde vid det bokslutet, så att historiken blir internt
konsekvent. Kurserna cachas i databasen.

Två skyddsnät, eftersom Yahoo ibland utelämnar valutauppgiften ur sitt
svar när anropen stryps:

1. Rapportvalutan **sparas i databasen**. Saknas den i ett svar används
   den senast kända.
2. Är valutan ändå okänd **utesluts räkenskapssiffrorna** och markeras
   som saknade med orsak. Ett oomräknat tal är värre än inget tal, för
   det ser ut att ha gått bra.

Kontrollera själv:

```bash
python -m aktieanalys visa-bolag AZN.ST
```

Står det "omräknat från USD" i källkolumnen har omräkningen gjorts.

---

## Databasen

`data/cache.sqlite` har två roller samtidigt:

**Cache.** Data som hämtats nyligen hämtas inte igen. yfinance är
inofficiellt och tål inte att pressas.

**Arkiv.** Varje hämtning sparas med datum och skrivs aldrig över.
Frågar man databasen "vad visste vi den 3 maj?" får man svaret som
gällde då, inte dagens siffror. Det är den enda delen av projektet som
inte går att återskapa -- **radera den aldrig.** Den ligger med i
repot med flit.

Fem tabeller: `fundamenta` (ett värde per bolag, fält och hämtdatum),
`kurser` (daglig stängningskurs, även valutakurser), `bolagsmeta`
(namn och valutor), `saknat` (vad som saknades och varför)
och `hamtlogg` (varje hämtningsförsök och hur det gick).

---

## Byta datakälla senare

Hela datalagret ligger bakom ett gränssnitt. Vill du gå över till
Börsdata eller ett eget Google Sheets-flöde skriver du en klass som
ärver `Provider` och fyller samma fält:

```python
from aktieanalys.data.provider import Provider

class BorsdataProvider(Provider):
    namn = "borsdata"

    def hamta(self, ticker, idag=None):
        ...   # fyll ett Bolagsdata
```

Poängmodellen, bytesreglerna och rapporten rörs inte. Fältnamnen som
ska fyllas står i `src/aktieanalys/data/bolagsdata.py`.

Tre regler varje datakälla måste följa: höj aldrig ett undantag utåt
(ett trasigt bolag får inte stoppa veckokörningen), gissa aldrig
(saknade värden markeras med orsak), och ange källa för varje fält.

---

## Filerna du ändrar i

All styrning ligger i tre textfiler i mappen `config/`. Inget är
hårdkodat. Filerna är fullständigt kommenterade -- öppna dem i vilken
textredigerare som helst.

### `config/config.yaml` -- hur analysen räknar

Här bor vikter, gränsvärden, bytesregler, courtage och mejlinställningar.
De vanligaste ändringarna:

| Vill du... | Ändra |
|---|---|
| lägga mer vikt på billiga bolag | `poang.vikter.vardering` upp, `momentum` ner |
| byta oftare | `byten.min_poangforbattring_procent` ner från 20 |
| få med mindre bolag | `universum.min_borsvarde_mdr` ner från 5.0 |
| stänga av Claude-lagret | `kvalitativt.aktiverat: false` |
| ändra courtage | `kostnader.courtage_procent` |

Två regler gäller för vikterna, och programmet säger till om du bryter dem:

* `poang.vikter` måste summera till `1.0`
* vikterna inom varje pelare i en måttuppsättning måste summera till `1.0`

### `config/universum.yaml` -- bolagen och kategorierna

Sju kategorier med 90 bolag. Lägg till ett bolag genom att skriva en ny
rad med `- TICKER.ST` under rätt kategori. Ta bort genom att radera
raden, eller sätt `#` framför för att tillfälligt stänga av den.

Tickern är Yahoo Finance-beteckningen; svenska aktier slutar på `.ST`.
Hittar du inte en ticker: sök på bolagsnamnet på finance.yahoo.com och
titta i adressfältet.

Bolagslistan är ett förslag att utgå från, och enstaka tickers kan vara
fel. Datatäckningskontrollen i steg 3 testar varje ticker mot Yahoo och
talar om exakt vilka som inte svarar.

### `config/portfolj.yaml` -- dina innehav

Ett innehav per kategori, med köpdatum. Köpdatumet styr regeln om
minsta innehavstid, så sätt det datum du **faktiskt köpte** -- inte
dagens datum, annars tror systemet att innehavet är nytt och spärrar
byten i onödan.

Tom portfölj är ett giltigt utgångsläge. Då föreslår rapporten
förstagångsköp i stället för byten.

---

## Så är det byggt

Fem lager. Varje lager känner bara till lagret ovanför genom ett bestämt
gränssnitt, vilket är det som gör att datakällan går att byta ut utan
att poängmodellen rörs.

```
  1. DATAKÄLLA      yfinance  ·  CSV-komplettering  ·  [senare: Börsdata]
  2. UNIVERSUM      filtrera storlek/omsättning  ->  kategorisera  ->  datatäckning
  3. POÄNGMODELL    percentilrank inom kategori, sektorsanpassade mått
  4. KVALITATIVT    Claude flaggar risker, justerar högst ±10 %
  5. BESLUT         bytesregler, kostnad  ->  HTML-rapport och mejl
```

### Mappar

```
config/     de tre filerna du ändrar i
data/       databasen (cache.sqlite) och dina CSV-filer
rapporter/  veckorapporterna som HTML, en per vecka
src/        programkoden
tests/      testerna
```

### Tre principer som koden följer

**Saknad data blir aldrig noll.** Om ett nyckeltal inte finns markeras
det som saknat och bolaget räknas som att det saknar det måttet. En nolla
skulle se ut som "sämst i klassen", vilket är ett helt annat påstående än
"vi vet inte". Bolag som saknar mer än `max_saknade_matt_per_bolag`
nyckeltal utesluts, och **listas alltid i rapporten med orsak**.

**Allt som hämtas sparas med datum.** Databasen `data/cache.sqlite` är
både cache och arkiv. Den gör att yfinance inte anropas i onödan, och den
bygger med tiden upp en historik över vad som faktiskt var känt vid varje
tidpunkt.

**Claude får bara justera, aldrig välja.** Språkmodellen ser bara de
bolag poängmodellen redan rangordnat högt, och får flytta poängen högst
±10 %. Den kan inte föreslå ett bolag utanför modellen. Svaret valideras
som JSON; håller det inte formatet används den rena sifferpoängen.

---

## Om något går fel

**`ModuleNotFoundError: No module named 'aktieanalys'`**
Du står i fel mapp, eller har inte kört `pip install -e .`. Gå till
projektmappen och kör installationen igen.

**`Hittar inte konfigurationsfilen ...`**
Kommandot letar efter `config/` i mappen du står i. Gå till
projektmappen, eller peka ut katalogen: `--config /sökväg/till/config`.

**`... går inte att läsa som YAML`**
Nästan alltid indraget. Använd mellanslag, aldrig tabbar, och håll samma
antal mellanslag som på raderna runt om.

**`okänd inställning -- kontrollera stavningen`**
Du har skrivit ett inställningsnamn som inte finns. Meddelandet säger
vilket. Jämför med kommentarerna i filen.

Loggen från senaste körningen ligger i `data/korning.log` och innehåller
mer detaljer än det som skrivs på skärmen.

---

## Begränsningar du bör känna till

**yfinance är en inofficiell datakälla.** Den kan svara tomt, långsamt
eller med fel. Programmet gör om försök och loggar problem, men enstaka
nyckeltal kommer att saknas för enstaka bolag. Rapporten visar alltid
vad som saknades.

**Vissa uppgifter finns inte alls i yfinance** och måste fyllas i för
hand i CSV-filer i `data/manuellt/`: substansvärden för investmentbolag,
förvaltningsresultat och belåningsgrad för fastighetsbolag,
kapitaltäckning för banker. Det byggs i steg 2.

**Backtestet blir approximativt.** yfinance levererar inte historiska
fundamenta med uppgift om *när* de offentliggjordes, och bara fyra till
fem år bakåt. Kursdata är korrekt historisk, men för nyckeltalen används
en konservativ rapportfördröjning som antagande. Backtestet är därför en
rimlighetskontroll av reglerna, inte ett bevis för att de fungerar. Från
och med nu sparas däremot varje veckas data med datum, så med tiden
byggs en historik upp som går att lita på.
