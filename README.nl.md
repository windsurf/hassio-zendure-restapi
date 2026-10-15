# Zendure RestAPI – Home Assistant-integratie

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/windsurf/hassio-zendure-restapi.svg)](https://github.com/windsurf/hassio-zendure-restapi/releases)
[![100% lokaal](https://img.shields.io/badge/100%25-lokaal-brightgreen.svg)](#waarom-deze-integratie)
[![Works with HomeWizard](https://img.shields.io/badge/works%20with-HomeWizard%20P1-1e90ff.svg)](#-ondersteunde-meters)

🇬🇧 [English version](README.md) · 🇩🇪 [Deutsche Version](README.de.md)

**Je Zendure SolarFlow-thuisaccu in Home Assistant, 100% lokaal, in drie stappen.**
Geen cloudaccount, geen MQTT-broker, geen YAML-packages om te kopiëren. Installeer via HACS, voeg je accu toe, kies een modus — de accu houdt je netaansluiting op nul, elke seconde.

**🔌 Works with HomeWizard.** Heb je al een HomeWizard P1-meter? Meer heb je niet nodig — een Zendure-meter is niet vereist. YouLess, EcoTracker, Shelly Pro 3EM en de Zendure-meters werken ook.

![Zendure-dashboard](images/dashboard.png)

*`smart_matching` houdt het net op nul: het huis vraagt 175 W, de accu levert het, de meter staat op 0 W. Dit dashboard zit bij de integratie.*

**Snel aan de slag hieronder** · alle details in de [Technische referentie](#-technische-referentie) (Engels)

---

## 📌 Ondersteunde apparaten

| Apparaat | Status |
|---|---|
| **SolarFlow 3000 Mix AC+** | ✅ Dagelijks getest door de maker |
| **SolarFlow 4000 Mix AC+** | ✅ Ondersteund — zelfde platform en accu als de 3000 Mix |
| **SolarFlow 2400 AC · 2400 AC+ · 1600 AC+** | ✅ Ondersteund — zelfde AC-gekoppelde opbouw |
| **SolarFlow 800 · 800 Plus · 800 Pro · 800 Pro 2 · 2400 Pro** | 🧪 Werkt, slimme modi experimenteel (zonnepanelen op het apparaat) |
| **SolarFlow 4000 Mix Pro** | 🧪 Werkt, slimme modi experimenteel (zonnepanelen op het apparaat) |
| Hyper 2000 · Hub 1200/2000 · AIO 2400 · Ace 1500 | ❌ Geen lokale API op deze modellen |

**🙋 Heb je een model met ✅ of 🧪? Dan ben jij precies wie dit project nodig heeft.**
Installeer het, en als er iets niet klopt — of als alles gewoon werkt — open een
[New model report](https://github.com/windsurf/hassio-zendure-restapi/issues/new?template=new_model.yml)
met de diagnostics-download als bijlage. Eén melding is genoeg om een model van 🧪 naar ✅ te brengen.

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
| **Shelly Pro 3EM** (klemmen op de netaansluiting) | 🧪 Gedocumenteerde lokale API, één meting per seconde |
| **Elke vermogenssensor in Home Assistant** (Zonneplan, DSMR, …) | 🧪 Nieuw in v1.5.3 — voor dashboard en vergelijking; regelt alleen als hij elke seconde ververst |

In één keer ingesteld: **Integratie toevoegen → Zendure RestAPI → Externe P1-meter**, kies je meter en vul het IP-adres in. Een HomeWizard P1-meter verschijnt vanzelf onder *Ontdekt*. Hij wordt een eigen apparaat met sensoren voor vermogen, status en energie, en omdat de select **Meter source** op `auto` staat, gebruikt de accu hem meteen.
HomeWizard: zet eerst *Lokale API* aan in de HomeWizard Energy-app.

*Niet gelieerd aan HomeWizard, YouLess, everHome of Shelly; dit zijn compatibiliteitsnotities.*

---

## Waarom deze integratie

- **100% lokaal.** Praat rechtstreeks met de eigen HTTP-server van de accu op je netwerk. Blijft werken als internet of de Zendure-cloud dat niet doet.
- **Nul-teruglevering, elke seconde.** Een snelle lus volgt je meter elke seconde; een tragere regelt de richting en de veiligheid. Gebouwd en afgesteld op metingen, niet op aannames.
- **Zeven werkmodi** — van rustig `standby` tot `smart_matching`, alleen laden, alleen ontladen, snel laden/ontladen en handmatig. Aan te sturen vanuit je eigen prijsautomation.
- **Je eigen meter.** HomeWizard, YouLess, EcoTracker, Shelly Pro 3EM of Zendure — gevonden op je netwerk of toegevoegd op IP-adres, rechtstreeks gelezen, elke seconde, niet via een tragere Home Assistant-sensor. Of elke vermogenssensor die Home Assistant al heeft.
- **Veilig ontworpen.** Geen geldige meterwaarde, geen beweging: de accu stopt. Twee accu's vechten nooit om dezelfde meter.
- **Laag sluimerverbruik, gemeten.** Geeft de accu terug aan zijn zuinige stand als er niets te doen is: ongeveer 5 W in plaats van 29–36 W, elk uur dat hij stilstaat.
- **Spaart het flashgeheugen.** De slimme modi schrijven hun limieten naar het werkgeheugen, niet naar flash — tienduizenden schrijfacties per jaar die het apparaat niet slijten.
- **Laat een volle accu met rust.** Op de eigen SOC-grenzen van het apparaat wordt niets in de dichte richting besteld: een hele nacht op 100% kostte twee limietwijzigingen, en de status zegt waarom.
- **Alles zichtbaar.** Kant-en-klaar dashboard, een controllerstatus die zegt *waarom*, laad- en ontlaadrendement als sensoren, en een optionele CSV-trace per seconde voor wie het precies wil weten.
- **Elke wijziging gemeten vóór de release.** Wijzigingen aan de regellus worden eerst nagespeeld op opgenomen meetreeksen per seconde; de metingen en de correcties op Zendures documentatie staan openbaar in de technische referentie.
- **Spreekt je taal.** Instelschermen en statussen in het Nederlands, Engels, Duits, Frans, Italiaans, Spaans, Portugees, Pools, Tsjechisch, Zweeds en Deens.

---

## 🚀 Aan de slag

**1. Zet de lokale API aan** — onder EN 18031 staat die standaard uit.
In de Zendure-app: voeg **HEMS** toe en sluit de app daarna af. Controleer het vanuit een browser:
`http://<ip-accu>/properties/report` moet JSON tonen.

**2. Installeer via HACS**

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=windsurf&repository=hassio-zendure-restapi&category=integration)

Of: HACS → menu met drie puntjes → Aangepaste repositories → `https://github.com/windsurf/hassio-zendure-restapi`, categorie **Integratie**. Installeer **Zendure RestAPI** en herstart Home Assistant.

**3. Voeg je apparaten toe**
Home Assistant vindt de accu en de P1-meter meestal zelf, onder **Instellingen → Apparaten & diensten**. Anders: voeg **Zendure RestAPI** toe, kies **Zendure-apparaat** en vul het IP-adres in. Zet **Operation mode** op `smart_matching` en zie het net naar nul gaan.

**Je meter:** een HomeWizard P1-meter staat klaar onder *Ontdekt*; klik **Toevoegen**. Andere meters: **Integratie toevoegen → Zendure RestAPI → Externe P1-meter**, kies YouLess, EcoTracker, Shelly of Zendure en vul het IP-adres in. Klaar — `auto` pakt hem op. Een Zendure P1-meter wordt gevonden als eigen apparaat en vraagt verder niets.

---

## Werkmodi

De namen van de modi blijven in elke taal gelijk, zodat automations en documentatie ernaar kunnen verwijzen.

| Modus | Wat de accu doet |
|---|---|
| `standby` | Niets — stopt één keer en laat het apparaat daarna met rust |
| `smart_matching` | Laadt bij overschot en ontlaadt bij vraag, en houdt het net op nul |
| `smart_charge_only` | Neemt alleen overschot op, ontlaadt nooit |
| `smart_discharge_only` | Dekt alleen vraag, laadt nooit |
| `quick_charge` / `quick_discharge` | Vol vermogen, los van de meter |
| `manual` | Volgt een vast instelpunt |

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
- [Changelog](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#changelog)

**Laatste release – v1.5.5:** alleen documentatie — hoe deze integratie zich verhoudt tot de zenSDK van Zendure, een nieuwe meting van twee meters naast elkaar, en de controllerstatus in de recorder houden. **v1.5.4:** de slimme modi bestellen niet meer in een volle of lege accu en zeggen waarom; `manual` op 0 W kan rusten. **v1.5.3:** meerdere externe meters naast elkaar, met een select *External meter* op de accu om er een te kiezen; elke vermogenssensor in Home Assistant als externe meter. **v1.5.2:** de externe P1-meter als eigen apparaat met sensoren voor vermogen, status en energie; HomeWizard P1-meters worden automatisch gevonden; instelschermen in elf talen; externe meter op het dashboard. Bij een upgrade vanaf v1.5.0/v1.5.1 verhuizen je meterinstellingen vanzelf mee.

## Meer

- 🐞 [Issues](https://github.com/windsurf/hassio-zendure-restapi/issues) — fouten, vragen en [modelmeldingen](https://github.com/windsurf/hassio-zendure-restapi/issues/new?template=new_model.yml). Nederlands mag.
- ⭐ Nuttig? Een ster op deze repository helpt anderen hem te vinden.

---

> **Disclaimer:** Niet gelieerd aan of goedgekeurd door Zendure. Geleverd zoals het is, zonder garantie. Gebruik op eigen risico.

Licentie: MIT — zie [LICENSE](LICENSE).
