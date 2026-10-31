# Zendure RestAPI – Home Assistant-integratie

[![Zero export](https://img.shields.io/badge/zero-export-2ea44f.svg)](#waarom-deze-integratie) [![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration) [![GitHub Release](https://img.shields.io/github/release/windsurf/hassio-zendure-restapi.svg)](https://github.com/windsurf/hassio-zendure-restapi/releases) [![100% lokaal](https://img.shields.io/badge/100%25-lokaal-brightgreen.svg)](#waarom-deze-integratie) [![Works with HomeWizard](https://img.shields.io/badge/works%20with-HomeWizard%20P1-1e90ff.svg)](#-ondersteunde-meters) [![Dynamische tarieven](https://img.shields.io/badge/dynamische-tarieven-f5a623.svg)](#-prijssturing)

🇬🇧 [English version](README.md) · 🇩🇪 [Deutsche Version](README.de.md)

**Je Zendure SolarFlow-thuisbatterij in Home Assistant, 100% lokaal, in drie stappen.** Geen cloudaccount, geen MQTT-broker, geen YAML-packages om te kopiëren. Installeer via HACS, voeg je accu toe, kies een modus — de accu houdt je netaansluiting op nul, elke seconde: nul op de meter.

**⚖️ Nul op de meter, elke seconde.** De batterij houdt je netaansluiting op nul (*nul op de meter*, NOM; Engels: *zero export*). Een snelle lus leest je meter elke seconde en corrigeert gelijkmatig aan beide kanten van nul: wat je zonnepanelen aan het net zouden geven gaat de batterij in, en wat het huis uit het net zou halen komt eruit, zonder heen en weer te slingeren. Waarom het loont: een kWh die je teruglevert brengt minder op dan een kWh die je afneemt kost — en zodra salderen stopt, kan terugleveren je zelfs geld kosten. Elke kWh die thuis blijft is de volle inkoopprijs waard.

**☀️ Vast contract met zonnepanelen? Ondersteund.** Geen prijssensor, geen Price Control: `smart_matching` slurpt je zonne-overschot op en geeft het 's avonds terug aan het huis. Als salderen stopt (Nederland, 2027) en je leverancier terugleverkosten rekent, is elke kWh die je opslaat in plaats van teruglevert bijna de volle inkoopprijs waard. [Meer](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#on-a-fixed-price-contract) (Engels)

**💶 Dynamisch tarief? Ingebouwd.** Zonneplan, Tibber, Frank Energie, Vattenfall, Essent of elke andere leverancier: Price Control plant laden en ontladen per kwartier over de prijzen van vandaag en morgen, met omzetverlies en slijtage meegerekend. Salderen zit erin zolang het geldt. Zet het aan — geen automation te schrijven.

**🔌 Works with HomeWizard.** Heb je al een HomeWizard P1-meter? Meer heb je niet nodig — een Zendure-meter is niet vereist. YouLess, EcoTracker, Shelly Pro 3EM en de Zendure-meters werken ook.

![\<afbeelding van het dashboard\>](images/dashboard.png)

*`smart_matching` zet het zonne-overschot in de accu: die laadt met 902 W en de regeling houdt het net binnen 10 W van nul, met de externe meter als stuurmeter. De integratie maakt dit dashboard voor je aan, met nog twee tabbladen: Price Control en History.*

**Snel aan de slag hieronder** · alle details in de [Technische referentie](#-technische-referentie) (Engels)

---

## 📌 Ondersteunde apparaten

| Apparaat | Status |
|---|---|
| **SolarFlow 3000 Mix AC+** | ✅ Dagelijks getest door de maker |
| **SolarFlow 4000 Mix AC+** | ✅ Ondersteund — zelfde platform en accu als de 3000 Mix |
| **SolarFlow 2400 AC** | ✅ Getest door een gebruiker — uitlezen en `smart_matching` bevestigd |
| **SolarFlow 2400 AC+** | ✅ Getest door een gebruiker — uitlezen met een Zendure Smart Meter 3CT |
| **SolarFlow 1600 AC+** | ✅ Ondersteund — zelfde AC-gekoppelde opbouw als de 2400 AC |
| **SolarFlow 800 · 800 Plus · 800 Pro · 800 Pro 2 · 2400 Pro** | 🧪 Werkt, slimme modi experimenteel (zonnepanelen op het apparaat) |
| **SolarFlow 4000 Mix Pro** | 🧪 Werkt, slimme modi experimenteel (zonnepanelen op het apparaat) |
| Hyper 2000 · Hub 1200/2000 · AIO 2400 · Ace 1500 | ❌ Geen lokale API op deze modellen |

**🙋 Heb je een model met ✅ of 🧪? Dan ben jij precies wie dit project nodig heeft.** Installeer het, en als er iets niet klopt — of als alles gewoon werkt — open een [New model report](https://github.com/windsurf/hassio-zendure-restapi/issues/new?template=new_model.yml) met de diagnostics-download als bijlage. Eén melding is genoeg om een model van 🧪 naar ✅ te brengen.

---

## 🔌 Ondersteunde meters

De accu moet weten wat je huis van het net haalt of terug levert. Gebruik de meter die je al hebt.

| Meter | Status |
|---|---|
| **HomeWizard P1-meter** (lokale API v1) | ✅ **Works with HomeWizard** — wordt automatisch gevonden op je netwerk; gedocumenteerde lokale API, elke seconde gelezen |
| **YouLess LS120** | ✅ Gemeten door de maker — ziet elke lastsprong tot een seconde eerder dan de Zendure-meter |
| **Zendure P1-meter** | ✅ Dagelijks getest door de maker |
| **Zendure Smart Meter 3CT** | 🧪 Zelfde API als de Zendure P1-meter |
| **EcoTracker** (everHome) | 🧪 Gedocumenteerde lokale API, één meting per seconde |
| **Shelly Pro 3EM** (klemmen op de netaansluiting) | ✅ Getest door een gebruiker als netmeter voor de regeling, één meting per seconde |
| **Elke meter in Home Assistant** (Shelly, DSMR, SMA, Huawei, Zonneplan, …) | 🧪 Kies het apparaat, de sensoren worden herkend (v1.6.5); regelt alleen als hij elke seconde of twee ververst |

In één keer ingesteld: **Integratie toevoegen → Zendure RestAPI → Externe netmeter via IP**, kies je meter en vul het IP-adres in. Een HomeWizard P1-meter verschijnt vanzelf onder *Ontdekt*. Hij wordt een eigen apparaat met sensoren voor vermogen, status en energie, en omdat de select **Meter source** op `auto` staat, gebruikt de accu hem meteen. HomeWizard: zet eerst *Lokale API* aan in de HomeWizard Energy-app.

*Niet gelieerd aan HomeWizard, YouLess, everHome of Shelly; dit zijn compatibiliteitsnotities.*

---

<a id="waarom-deze-integratie"></a>

## 💡 Waarom deze integratie

- **100% lokaal.** Praat rechtstreeks met de eigen HTTP-server van de accu op je netwerk. Blijft werken als internet of de Zendure-cloud dat niet doet.
- **Nul-teruglevering, elke seconde.** Een snelle lus volgt je meter elke seconde; een tragere regelt de richting en de veiligheid. Gebouwd en afgesteld op metingen, niet op aannames.
- **Zeven werkmodi** — van rustig `standby` tot `smart_matching`, alleen laden, alleen ontladen, snel laden/ontladen en handmatig. Stuur ze met de hand, met Price Control of met je eigen automation.
- **Dynamische tarieven, ingebouwd.** Price Control stuurt de accu op twee vaste prijzen of op een dagplan over de prijzen van vandaag en morgen — zie [Prijssturing](#-prijssturing).
- **Ook voor vaste contracten.** Met zonnepanelen en een vaste prijs slaat `smart_matching` alleen al het overschot op voor de avond; prijzen zijn niet nodig.
- **Blijft werken als het web het laat afweten.** De sturing draait lokaal in je netwerk, zonder cloud. Prijzen kunnen uit EPEX Spot of Nord Pool komen, die doorwerkten terwijl webdiensten offline gingen voor de noodpatches van PHP2Shell, en Price Control houdt bij een storing de laatste prijzen vast (v1.6.9).
- **Instellingen zijn entiteiten.** Elke instelling overleeft een herstart en is live aan te passen vanaf het dashboard of vanuit een automation. Updates via HACS laten je eigen configuratie met rust.
- **Je eigen meter.** HomeWizard, YouLess, EcoTracker, Shelly Pro 3EM of Zendure — gevonden op je netwerk of toegevoegd op IP-adres, rechtstreeks gelezen, elke seconde, niet via een tragere Home Assistant-sensor. Of elke vermogenssensor die Home Assistant al heeft.
- **Veilig ontworpen.** Geen geldige meterwaarde, geen beweging: de accu stopt. Twee accu's vechten nooit om dezelfde meter; elke accu heeft zijn eigen regelaar, meterkeuze en prijssturing.
- **Laag sluimerverbruik, gemeten.** Geeft de accu terug aan zijn zuinige stand als er niets te doen is: ongeveer 5 W in plaats van 29–36 W (Backup mode `closed`), elk uur dat hij stilstaat.
- **Spaart het flashgeheugen.** De slimme modi schrijven hun limieten naar het werkgeheugen, niet naar flash — tienduizenden schrijfacties per jaar die het apparaat niet slijten.
- **Laat een volle accu met rust.** Op de eigen SOC-grenzen van het apparaat wordt niets in de dichte richting besteld: een hele nacht op 100% kostte twee limietwijzigingen, en de status zegt waarom.
- **Alles zichtbaar.** Kant-en-klaar dashboard, een controllerstatus die zegt *waarom*, laad- en ontlaadrendement als sensoren, en een optionele CSV-trace per seconde voor wie het precies wil weten.
- **Elke wijziging gemeten vóór de release.** Wijzigingen aan de regellus worden eerst nagespeeld op opgenomen meetreeksen per seconde; de metingen en de correcties op Zendures documentatie staan openbaar in de technische referentie.
- **Spreekt je taal.** Instelschermen, entiteitsnamen en statussen in het Nederlands, Engels, Duits, Frans, Italiaans, Spaans, Portugees, Pools, Tsjechisch, Zweeds en Deens.

---

## 🚀 Aan de slag

**1. Zet de lokale API aan en de eigen sturing van Zendure uit** — onder EN 18031 staat de API standaard uit. In de Zendure-app:
- voeg **HEMS** toe — dat zet de lokale API aan;
- zet op de apparaatpagina **Add to HEMS** uit — dan blijft de eigen sturing van Zendure buiten spel. Andere manieren en de vermogensinstellingen van de app: [Before you start](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#before-you-start).

Sluit de app daarna af. Controleer het vanuit een browser: `http://<ip-accu>/properties/report` moet een resultaat in JSON-formaat tonen: tekst tussen accolades `{ … }` met `"properties"` erin.

**2. Installeer via HACS**

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=windsurf&repository=hassio-zendure-restapi&category=integration)

Of: HACS → menu met drie puntjes → Aangepaste repositories → `https://github.com/windsurf/hassio-zendure-restapi`, categorie **Integratie**. Installeer **Zendure RestAPI** en herstart Home Assistant.

[![Integratie toevoegen](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=zendure_restapi)

**3. Voeg je apparaten toe** Home Assistant vindt de accu en de P1-meter meestal zelf, onder **Instellingen → Apparaten & diensten**. Anders: voeg **Zendure RestAPI** toe, kies **Zendure-accu** (de eerste optie) en vul het IP-adres in. Het dashboard **Zendure RestAPI** verschijnt vanzelf in je zijbalk; een bestaand dashboard blijft ongemoeid. Zet **Operation mode** op `smart_matching` en zie het net naar nul gaan.

**Eén regelaar.** Zet uit wat de batterij verder nog stuurt: de eigen sturing van Zendure in de app (ZENKI — *Add to HEMS* uit, of *Custom Mode* zonder regels), een andere integratie, of een Home Assistant-automation die de modus of de limieten van de batterij zet. Twee regelaars op één batterij maken elkaars werk ongedaan; de integratie waarschuwt met de sensor *Tweede regelaar*. Zie [Before you start](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#before-you-start) (Engels).

**Je meter:** een HomeWizard P1-meter staat klaar onder *Ontdekt*; klik **Toevoegen**. Andere meters: **Integratie toevoegen → Zendure RestAPI → Externe netmeter via IP**, kies YouLess, EcoTracker, Shelly of Zendure en vul het IP-adres in. Een meter die al in Home Assistant zit: **Externe netmeter uit Home Assistant**. Klaar — `auto` pakt hem op. Een Zendure P1-meter wordt gevonden als eigen apparaat en vraagt verder niets.

---

<a id="werkmodi"></a>

## 🎛️ Werkmodi

De namen van de modi blijven in elke taal gelijk, zodat automations en documentatie ernaar kunnen verwijzen.

| Modus | Wat de accu doet |
|---|---|
| `standby` | Niets — stopt één keer en laat het apparaat daarna met rust |
| `smart_matching` | Laadt bij overschot en ontlaadt bij vraag, en houdt het net op nul |
| `smart_charge_only` | Neemt alleen overschot op, ontlaadt nooit |
| `smart_discharge_only` | Dekt alleen vraag, laadt nooit |
| `quick_charge` / `quick_discharge` | Vol vermogen, los van de meter |
| `manual` | Volgt een vast instelpunt |

**Vast contract met zonnepanelen:** kies `smart_matching` en laat Price Control uit. Wanneer opslaan loont: [On a fixed-price contract](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#on-a-fixed-price-contract) (Engels).

---

## 💶 Prijssturing

![\<afbeelding van het tabblad Price Control\>](images/dashboard_price.png)

*Het tabblad Price Control: het dagplan voor vandaag en morgen, gemeten tot nu en gepland daarna, welke kwartieren laden en welke ontladen, en de verwachte marge. Met salderen aan volgt de terugleverprijs de afnameprijs.*

Twee manieren om de accu te sturen op een dynamisch tarief, in een eigen apparaat:

- **Vaste prijzen** — op of onder een **goedkope prijs** laadt de accu (uit het net, of alleen van zon), op of boven een **dure prijs** ontlaadt hij, daartussen houdt hij het net op nul (de modi per band kies je zelf). Elke prijssensor werkt: Nord Pool, EnergyZero, Zonneplan, Tibber, Frank Energie of je eigen.
- **Dagplan** (nieuw in v1.6.0) — over de prijzen van vandaag en morgen van [EPEX Spot](https://github.com/mampfes/ha_epex_spot), Nord Pool of de eigen integratie van je leverancier bepaalt het per kwartier of de accu laadt, ontlaadt of geen van beide, met aparte import- en exportprijzen, omzetverlies, slijtage en een minimale marge per kWh. Standaard handelt het: laden en ontladen op het volle vermogen van de accu; met de modi per band dekt het desgewenst alleen het huis of laadt het alleen van zon. Klaar voor het einde van de saldering in 2027: zet *Net metering* uit en de exportprijs volgt zijn eigen formule.

Teruggespeeld op 30 dagen van een echte installatie leverde het dagplan **€ 1,58 per dag** op — evenveel als een volledige energieplanner op dezelfde dagen; de vaste prijzen € 0,21.

**Dagplan — wat je nodig hebt:** een prijssensor met de prijzen van vandaag en morgen, en **ApexCharts Card** uit HACS voor de grafiek. De day-aheadprijs is één prijs per land, dus elk dynamisch contract werkt: **EPEX Spot** is de aanbevolen bron en staat hieronder, **Nord Pool** werkt ook, en de opslag van je leverancier, de energiebelasting en de btw stel je in bij Price Control. Zo koppel je elke leverancier met een dynamisch contract — Zonneplan, Tibber, Frank Energie, Vattenfall, Eneco, Essent, Budget Energie, NextEnergy, Coolblue Energie, Vandebron, ANWB Energie, EnergyZero en meer. Klanten van Zonneplan en Frank Energie kunnen ook de eigen integratie van hun leverancier gebruiken. Zonder ApexCharts Card werkt het dagplan gewoon; het dashboard toont dan een melding in plaats van de grafiek.

1. Installeer **EPEX Spot** ([open in HACS](https://my.home-assistant.io/redirect/hacs_repository/?owner=mampfes&repository=ha_epex_spot&category=integration)) en **ApexCharts Card** ([open in HACS](https://my.home-assistant.io/redirect/hacs_repository/?owner=RomRider&repository=apexcharts-card&category=plugin)); herstart Home Assistant en ververs de browser.
2. **Integratie toevoegen → EPEX Spot** met je marktgebied (`NL` voor Nederland).
3. Voeg Prijssturing toe (hieronder) met de sensor *Market price*, `sensor.epex_spot_data_market_price`, en zet *De prijssensor bevat* op **Spotprijs excl. btw**.
4. Zet **Beslissing** op **Dagplan**. In 2026: *Importfactor* 1,21, *Importopslag* 0,1108481 (energiebelasting incl. btw) + de inkoopkosten per kWh van je leverancier ([tabel](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#import-surcharge-per-supplier-netherlands), Engels), en **Salderen** aan tot 1 januari 2027. Vergelijk **Importprijs** met de prijs van je leverancier voor dit kwartier.

**Liever Nord Pool?** Stel hem kaal in: *Include VAT* uit, precisie 7, low price percentage 0 (`sensor.nordpool_kwh_nl_eur_7_00_0`), en kies **Spotprijs excl. btw**. **De eigen integratie van je leverancier** (Zonneplan, Frank Energie): kies **Importprijs (heel tarief)**. Zie [Other price sources](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#other-price-sources-epex-spot-zonneplan-and-more) (Engels).

Volledige uitleg, tariefinstellingen voor andere contracten en landen: [Setting up spot prices](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#setting-up-spot-prices-for-the-day-plan) (Engels).

**Toevoegen:** **Integratie toevoegen → Zendure RestAPI → Prijssturing**, kies de accu en je prijssensor. Het apparaat **Price Control** verschijnt naast je accu; zet het daar aan. Elke instelling is live aan te passen, op dat apparaat of op het tabblad *Price Control* van het dashboard.

Standaard uit, en hij schrijft de werkmodus alleen als de beslissing verandert, dus een modus die je met de hand zet blijft staan. Stuurt een eigen automation of planner de werkmodus al, laat hem dan uit. Details in de [technische referentie](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#price-control) (Engels).

---

## 📏 Gemeten op het referentieapparaat

SolarFlow 3000 Mix AC+, één intern accupakket. Methode en ruwe cijfers in de technische referentie (Engels).

| | Gemeten |
|---|---|
| [Laadrendement](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#conversion-efficiency) bij 3 kW, AC → cellen | **95,2%** (drie vensters, 95,1–95,2%) |
| [Ontlaadverlies](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#conversion-efficiency) | vast ~33 W plus 97,4% omzetting — 95%+ bij hoog vermogen, 81–84% bij 100–200 W |
| [Bruikbare capaciteit](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#usable-capacity) | **8,8 kWh** aan de cellen; 9,3 kWh aan de AC-aansluiting voor 0 → 100% |
| [Sluimerverbruik](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#known-limitations) | 29–36 W zolang hij wakker is; ongeveer 5 W zodra `standby` hem laat slapen (Backup mode `closed`) |
| [Vertraging Zendure P1-meter](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#the-delay) | 0,4–1,1 s achter een YouLess op dezelfde poort; herhaalt in 28% van de seconden zijn vorige waarde |

---

## 📖 Technische referentie

Alles wat op deze pagina ontbreekt staat in **[README2.md – Technical reference](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md)** (Engels):

- [Supported products](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#-supported-products) — zekerheid per model en waar die op rust
- [Device support](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#device-support) — wat er per model verschilt, en waarom zonnepanelen op het apparaat experimenteel zijn
- [How the controller works](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#how-the-controller-works) — de twee lussen, de boekhouding, de beveiligingen
- [External meters](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#an-external-p1-meter) — HomeWizard, YouLess, EcoTracker, Shelly Pro 3EM, Zendure 3CT
- [Languages](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#languages) — elf talen, verbeteringen welkom
- [Supported entities](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#supported-entities) en [The Energy dashboard](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#the-energy-dashboard)
- [Debug logging en de voorbeeldtrace](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#debug-logging)
- [zenSDK and this integration](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#zensdk-and-this-integration) — wat de SDK van Zendure noemt, en wat deze integratie toevoegt en corrigeert
- [Accucluster](https://github.com/windsurf/hassio-zendure-restapi/blob/main/docs/cluster.md) — tot drie accu's, één per fase, als één systeem (Engels)
- [Changelog](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#changelog)

**Laatste release – v1.7.0:** een **accucluster**: tot drie accu's, één per fase, houden samen het net op nul — één is primary, de andere secondary; één accu werkt als voorheen.

<a id="meer"></a>

## 🔗 Meer

- 🐞 [Issues](https://github.com/windsurf/hassio-zendure-restapi/issues) — fouten, vragen en [modelmeldingen](https://github.com/windsurf/hassio-zendure-restapi/issues/new?template=new_model.yml). Nederlands mag.
- 💬 [Forumtopic](https://community.home-assistant.io/t/zendure-restapi-local-zero-export-control-for-solarflow-batteries/1029076) — vragen, opstellingen en ervaringen.
- ⭐ Nuttig? Een ster op deze repository helpt anderen hem te vinden.

---

> **Disclaimer:** Niet gelieerd aan of goedgekeurd door Zendure. Geleverd zoals het is, zonder garantie. Gebruik op eigen risico.

Licentie: Apache 2.0 sinds v1.7.0; v1.6.x en eerder onder MIT — zie [LICENSE](LICENSE) en [NOTICE](NOTICE).
