# Zendure RestAPI – Home-Assistant-Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/windsurf/hassio-zendure-restapi.svg)](https://github.com/windsurf/hassio-zendure-restapi/releases)
[![100% lokal](https://img.shields.io/badge/100%25-lokal-brightgreen.svg)](#warum-diese-integration)
[![Works with HomeWizard](https://img.shields.io/badge/works%20with-HomeWizard%20P1-1e90ff.svg)](#-unterstützte-zähler)
[![Dynamische Tarife](https://img.shields.io/badge/dynamische-Tarife-f5a623.svg)](#-preissteuerung)

🇬🇧 [English version](README.md) · 🇳🇱 [Nederlandse versie](README.nl.md)

**Deine Zendure-SolarFlow-Batterie in Home Assistant, 100 % lokal, in drei Schritten.**
Kein Cloud-Konto, kein MQTT-Broker, keine YAML-Pakete zum Kopieren. Über HACS installieren, Batterie hinzufügen, Modus wählen – die Batterie hält deinen Netzbezug auf null, einmal pro Sekunde: Nulleinspeisung ohne Cloud.

**🔌 Works with HomeWizard.** Du hast schon einen HomeWizard-P1-Zähler? Mehr brauchst du nicht – ein Zendure-Zähler ist nicht nötig. YouLess, EcoTracker, Shelly Pro 3EM und die Zendure-Zähler funktionieren ebenfalls.

**💶 Dynamische Tarife, eingebaut.** Du hast einen dynamischen Stromtarif, etwa von Tibber, Ostrom oder Rabot? Price Control plant Laden und Entladen je Viertelstunde über die Nord-Pool-Preise von heute und morgen – Bezugs- und Einspeisepreis, Umwandlungsverluste und Verschleiß eingerechnet; der Einspeisepreis folgt deiner Einspeisevergütung oder dem Marktwert. Keine Automation zu schreiben: einschalten.

![Zendure-Dashboard](images/dashboard.png)

*`smart_matching` hält das Netz auf null: Das Haus braucht 175 W, die Batterie liefert sie, der Zähler zeigt 0 W. Die Integration legt dieses Dashboard auf Wunsch für dich an.*

**Schnellstart unten** · alle Details in der [technischen Referenz](#-technische-referenz) (Englisch)

---

## 📌 Unterstützte Geräte

| Gerät | Status |
|---|---|
| **SolarFlow 3000 Mix AC+** | ✅ Täglich vom Autor getestet |
| **SolarFlow 4000 Mix AC+** | ✅ Unterstützt – gleiche Plattform und Batterie wie der 3000 Mix |
| **SolarFlow 2400 AC · 2400 AC+ · 1600 AC+** | ✅ Unterstützt – gleicher AC-gekoppelter Aufbau |
| **SolarFlow 800 · 800 Plus · 800 Pro · 800 Pro 2 · 2400 Pro** | 🧪 Funktioniert, Smart-Modi experimentell (Solarmodule am Gerät) |
| **SolarFlow 4000 Mix Pro** | 🧪 Funktioniert, Smart-Modi experimentell (Solarmodule am Gerät) |
| Hyper 2000 · Hub 1200/2000 · AIO 2400 · Ace 1500 | ❌ Keine lokale API bei diesen Modellen |

**🙋 Du hast ein Modell mit ✅ oder 🧪? Dann bist du genau die Person, die dieses Projekt braucht.**
Installiere es, und wenn etwas nicht stimmt – oder wenn einfach alles funktioniert – eröffne einen
[New model report](https://github.com/windsurf/hassio-zendure-restapi/issues/new?template=new_model.yml)
mit dem Diagnose-Download im Anhang. Ein Bericht genügt, um ein Modell von 🧪 auf ✅ zu bringen. Deutsch ist willkommen.

---

## 🔌 Unterstützte Zähler

Die Batterie muss wissen, was dein Haus aus dem Netz bezieht oder einspeist. Nutze den Zähler, den du schon hast.

| Zähler | Status |
|---|---|
| **HomeWizard-P1-Zähler** (lokale API v1) | ✅ **Works with HomeWizard** – wird im Netzwerk automatisch gefunden; dokumentierte lokale API, jede Sekunde gelesen |
| **YouLess LS120** | ✅ Vom Autor gemessen – sieht jeden Lastsprung bis zu einer Sekunde früher als der Zendure-Zähler |
| **Zendure-P1-Zähler** | ✅ Täglich vom Autor getestet |
| **Zendure Smart Meter 3CT** | 🧪 Gleiche API wie der Zendure-P1-Zähler |
| **EcoTracker** (everHome) | 🧪 Dokumentierte lokale API, ein Messwert pro Sekunde |
| **Shelly Pro 3EM** (Klemmen am Netzanschluss) | 🧪 Dokumentierte lokale API, ein Messwert pro Sekunde |
| **Jeder Leistungssensor in Home Assistant** (Zonneplan, DSMR, …) | 🧪 Neu in v1.5.3 – für Dashboard und Vergleich; regelt nur, wenn er jede Sekunde aktualisiert |

In einem Rutsch eingerichtet: **Integration hinzufügen → Zendure RestAPI → Externer P1-Zähler**, Zähler wählen, IP-Adresse eingeben. Ein HomeWizard-P1-Zähler erscheint von selbst unter *Entdeckt*. Er wird ein eigenes Gerät mit Sensoren für Leistung, Status und Energie, und da der Select **Meter source** auf `auto` steht, nutzt die Batterie ihn sofort.
HomeWizard: Zuerst in der HomeWizard-Energy-App *Lokale API* einschalten.

*Keine Verbindung zu HomeWizard, YouLess, everHome oder Shelly; dies sind Kompatibilitätshinweise.*

---

## Warum diese Integration

- **100 % lokal.** Spricht direkt mit dem eigenen HTTP-Server der Batterie in deinem Netzwerk. Funktioniert weiter, wenn Internet oder Zendure-Cloud ausfallen.
- **Nulleinspeisung, einmal pro Sekunde.** Eine schnelle Schleife folgt deinem Zähler jede Sekunde; eine langsamere regelt Richtung und Sicherheit. Gebaut und abgestimmt auf Messungen, nicht auf Annahmen.
- **Sieben Betriebsmodi** – vom passiven `standby` bis `smart_matching`, nur laden, nur entladen, schnell laden/entladen und manuell. Steuerbar von Hand, über Price Control oder über deine eigene Automation.
- **Dynamische Tarife, eingebaut.** Price Control steuert die Batterie über zwei feste Preise oder über einen Tagesplan auf den Nord-Pool-Preisen – siehe [Preissteuerung](#-preissteuerung).
- **Einstellungen sind Entitäten.** Jede Einstellung übersteht einen Neustart und lässt sich live im Dashboard oder per Automation ändern. Updates über HACS lassen deine eigene Konfiguration in Ruhe.
- **Dein eigener Zähler.** HomeWizard, YouLess, EcoTracker, Shelly Pro 3EM oder Zendure – im Netzwerk gefunden oder per IP hinzugefügt, direkt gelesen, einmal pro Sekunde, nicht über einen langsameren Home-Assistant-Sensor. Oder jeder Leistungssensor, den Home Assistant schon hat.
- **Sicher konstruiert.** Kein gültiger Zählerwert, keine Bewegung: Die Batterie stoppt. Zwei Batterien streiten nie um denselben Zähler; jede hat ihren eigenen Regler, ihre Zählerwahl und ihre Preissteuerung.
- **Geringer Standby-Verbrauch, gemessen.** Gibt die Batterie in ihren Sparzustand zurück, wenn nichts zu tun ist: etwa 5 W statt 29–36 W (Backup mode `closed`), für jede Stunde Stillstand.
- **Schont den Flash-Speicher.** Die Smart-Modi schreiben ihre Grenzwerte in den Arbeitsspeicher, nicht in den Flash – Zehntausende Schreibvorgänge pro Jahr, die das Gerät nicht abnutzen.
- **Lässt eine volle Batterie in Ruhe.** An den eigenen SOC-Grenzen des Geräts wird nichts in die gesperrte Richtung bestellt: Eine ganze Nacht bei 100 % kostete zwei Grenzwertänderungen, und der Status sagt, warum.
- **Alles sichtbar.** Fertiges Dashboard, ein Controller-Status, der sagt *warum*, Lade- und Entladewirkungsgrad als Sensoren und ein optionaler CSV-Trace pro Sekunde für Genaue.
- **Jede Änderung vor dem Release gemessen.** Änderungen an der Regelschleife werden zuerst an aufgezeichneten Messreihen pro Sekunde nachgespielt; die Messungen und die Korrekturen an Zendures Dokumentation stehen öffentlich in der technischen Referenz.
- **Spricht deine Sprache.** Einrichtungsdialoge, Entitätsnamen und Zustände auf Deutsch, Englisch, Niederländisch, Französisch, Italienisch, Spanisch, Portugiesisch, Polnisch, Tschechisch, Schwedisch und Dänisch.

---

## 🚀 Loslegen

**1. Lokale API einschalten und Zendures eigene Steuerung aus** – unter EN 18031 ist die API standardmäßig aus.
In der Zendure-App, zwei Einstellungen:
- **HEMS** hinzufügen – das schaltet die lokale API ein;
- unter **Energieplan** den **benutzerdefinierten Modus** wählen (englische App: *Custom mode*) – sonst schreibt die eigene Steuerung von Zendure neben dieser Integration weiter an die Batterie.

Die App danach schließen. Im Browser prüfen:
`http://<IP-der-Batterie>/properties/report` muss JSON zeigen.

**2. Über HACS installieren**

[![In HACS öffnen](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=windsurf&repository=hassio-zendure-restapi&category=integration)

Oder: HACS → Drei-Punkte-Menü → Benutzerdefinierte Repositories → `https://github.com/windsurf/hassio-zendure-restapi`, Kategorie **Integration**. **Zendure RestAPI** installieren und Home Assistant neu starten.

[![Integration hinzufügen](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=zendure_restapi)

**3. Geräte hinzufügen**
Home Assistant findet Batterie und P1-Zähler meist selbst, unter **Einstellungen → Geräte & Dienste**. Sonst **Zendure RestAPI** hinzufügen, **Zendure-Gerät** wählen und die IP-Adresse eingeben. **Zendure-RestAPI-Dashboard anlegen** anhaken, dann erscheint das Dashboard in der Seitenleiste. **Operation mode** auf `smart_matching` stellen und zusehen, wie das Netz auf null geht.

**Dein Zähler:** Ein HomeWizard-P1-Zähler steht unter *Entdeckt* bereit; auf **Hinzufügen** klicken. Andere Zähler: **Integration hinzufügen → Zendure RestAPI → Externer P1-Zähler**, YouLess, EcoTracker, Shelly oder Zendure wählen und die IP-Adresse eingeben. Fertig – `auto` übernimmt ihn. Ein Zendure-P1-Zähler wird als eigenes Gerät gefunden und braucht nichts weiter.

---

## Betriebsmodi

Die Namen der Modi bleiben in jeder Sprache gleich, damit Automationen und Dokumentation darauf verweisen können.

| Modus | Was die Batterie tut |
|---|---|
| `standby` | Nichts – stoppt einmal und lässt das Gerät danach in Ruhe |
| `smart_matching` | Lädt bei Überschuss und entlädt bei Bedarf, hält das Netz auf null |
| `smart_charge_only` | Nimmt nur Überschuss auf, entlädt nie |
| `smart_discharge_only` | Deckt nur den Bedarf, lädt nie |
| `quick_charge` / `quick_discharge` | Volle Leistung, unabhängig vom Zähler |
| `manual` | Folgt einem festen Sollwert |

---

## 💶 Preissteuerung

Zwei Wege, die Batterie nach einem dynamischen Tarif zu steuern, in einem eigenen Gerät:

- **Feste Preise** – bei oder unter einem **günstigen Preis** lädt die Batterie (aus dem Netz oder nur aus Solarüberschuss), bei oder über einem **teuren Preis** entlädt sie, dazwischen hält sie das Netz auf null (die Modi je Band wählst du selbst). Jeder Preissensor geht: Nord Pool, EnergyZero, Zonneplan, Tibber, Frank Energie oder dein eigener.
- **Tagesplan** (neu in v1.6.0) – über die [Nord Pool](https://github.com/custom-components/nordpool)-Preise von heute und morgen bestimmt er je Viertelstunde, ob die Batterie lädt, entlädt oder keines von beiden, mit getrennten Bezugs- und Einspeisepreisen, Umwandlungsverlusten, Verschleiß und einer Mindestmarge pro kWh. Standardmäßig handelt er: Laden und Entladen mit der vollen Leistung der Batterie; über die Modi je Band deckt er auf Wunsch nur das Haus oder lädt nur aus Solarüberschuss. Bereit für das Ende der niederländischen Saldierung 2027: *Net metering* ausschalten, und der Einspeisepreis folgt seiner eigenen Formel.

Auf 30 Tagen einer echten Anlage nachgespielt brachte der Tagesplan **€ 1,58 pro Tag** – so viel wie ein vollständiger Energieplaner an denselben Tagen; die festen Preise € 0,21.

**Tagesplan – zwei Voraussetzungen aus HACS:** **Nord Pool** für die Preise, **ApexCharts Card** für die Grafik.

1. **Nord Pool** ([in HACS öffnen](https://my.home-assistant.io/redirect/hacs_repository/?owner=custom-components&repository=nordpool&category=integration)) und **ApexCharts Card** ([in HACS öffnen](https://my.home-assistant.io/redirect/hacs_repository/?owner=RomRider&repository=apexcharts-card&category=plugin)) installieren; Home Assistant neu starten und den Browser neu laden.
2. **Integration hinzufügen → Nord Pool**: *Region* dein Preisgebiet (`GER` für Deutschland, `AT` für Österreich), *Currency* `EUR`, *Include VAT* an, *Price in cents* aus, *Energy scale* `kWh`, *Template for additional costs* unverändert lassen.
3. Preissteuerung (unten) mit diesem Sensor hinzufügen, `sensor.nordpool_kwh_…`, und *Der Preissensor enthält* auf **Spotpreis inkl. MwSt.** stellen; *Bezugsfaktor* auf 1 + MwSt.-Satz setzen (1,19 in Deutschland, 1,20 in Österreich).
4. **Entscheidung** auf **Tagesplan** stellen und **Bezugspreis** mit dem Preis deines Anbieters für diese Viertelstunde vergleichen; weicht er ab, *Bezugsaufschlag* anpassen.

Ausführliche Anleitung, Tarifeinstellungen für andere Verträge und Länder: [Setting up Nord Pool](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#setting-up-nord-pool-for-the-day-plan) (Englisch).

**Hinzufügen:** **Integration hinzufügen → Zendure RestAPI → Preissteuerung**, Batterie und Preissensor wählen. Das Gerät **Price Control** erscheint neben deiner Batterie; schalte es dort ein. Jede Einstellung lässt sich live ändern, auf diesem Gerät oder im Tab *Price Control* des Dashboards.

Standardmäßig aus, und sie schreibt den Betriebsmodus nur, wenn sich ihre Entscheidung ändert; ein von Hand gesetzter Modus bleibt also stehen. Steuert schon eine eigene Automation oder ein Planer den Betriebsmodus, lass sie aus. Details in der [technischen Referenz](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#price-control) (Englisch).

---

## 📏 Gemessen am Referenzgerät

SolarFlow 3000 Mix AC+, ein internes Batteriepack. Methode und Rohdaten in der technischen Referenz (Englisch).

| | Gemessen |
|---|---|
| [Ladewirkungsgrad](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#conversion-efficiency) bei 3 kW, AC → Zellen | **95,2 %** (drei Fenster, 95,1–95,2 %) |
| [Entladeverluste](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#conversion-efficiency) | fest ~33 W plus 97,4 % Wandlung – 95 %+ bei hoher Leistung, 81–84 % bei 100–200 W |
| [Nutzbare Kapazität](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#usable-capacity) | **8,8 kWh** an den Zellen; 9,3 kWh am AC-Anschluss für 0 → 100 % |
| [Ruheverbrauch](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#known-limitations) | 29–36 W im Wachzustand; etwa 5 W, sobald `standby` das Gerät schlafen lässt (Backup mode `closed`) |
| [Verzögerung Zendure P1-Zähler](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#the-delay) | 0,4–1,1 s hinter einem YouLess am selben Port; wiederholt in 28 % der Sekunden seinen vorherigen Wert |

---

## 📖 Technische Referenz

Alles, was auf dieser Seite fehlt, steht in **[README2.md – Technical reference](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md)** (Englisch):

- [Supported products](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#-supported-products) – Verlässlichkeit pro Modell und worauf sie beruht
- [Device support](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#device-support) – was sich zwischen den Modellen unterscheidet und warum Solarmodule am Gerät experimentell sind
- [How the controller works](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#how-the-controller-works) – die zwei Schleifen, die Buchführung, die Schutzmechanismen
- [External meters](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#an-external-p1-meter) – HomeWizard, YouLess, EcoTracker, Shelly Pro 3EM, Zendure 3CT
- [Languages](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#languages) – elf Sprachen, Korrekturen willkommen
- [Supported entities](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#supported-entities) und [The Energy dashboard](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#the-energy-dashboard)
- [Debug-Logging und der Beispiel-Trace](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#debug-logging)
- [zenSDK and this integration](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#zensdk-and-this-integration) – was Zendures SDK aufführt und was diese Integration ergänzt und korrigiert
- [Changelog](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#changelog)

**Neueste Version – v1.6.1:** die drei **Modi je Band** steuern jetzt auch den **Tagesplan**, und er plant mit ihnen; **Voreinstellungen**, die *Entscheidung* und *Net metering* folgen; die App-Einrichtung nennt beide Schritte, HEMS und benutzerdefinierter Modus. **v1.6.0:** **Preissteuerung für dynamische Tarife**, mit einem **Tagesplan** über die Nord-Pool-Preise – getrennte Bezugs- und Einspeisepreise, Umwandlungsverluste und eine Marge pro kWh; Entitätsnamen in elf Sprachen, mit Entity-IDs, die englisch bleiben; das Dashboard **Zendure RestAPI** auf Wunsch angelegt.

## Mehr

- 🐞 [Issues](https://github.com/windsurf/hassio-zendure-restapi/issues) – Fehler, Fragen und [Modellberichte](https://github.com/windsurf/hassio-zendure-restapi/issues/new?template=new_model.yml). Deutsch ist willkommen.
- 💬 [Forenthema](https://community.home-assistant.io/t/zendure-restapi-local-zero-export-control-for-solarflow-batteries/1029076) – Fragen, Setups und Erfahrungen.
- ⭐ Nützlich? Ein Stern für dieses Repository hilft anderen, es zu finden.

---

> **Haftungsausschluss:** Keine Verbindung zu Zendure, nicht von Zendure unterstützt. Bereitgestellt wie besehen, ohne Gewährleistung. Nutzung auf eigene Gefahr.

Lizenz: MIT – siehe [LICENSE](LICENSE).
