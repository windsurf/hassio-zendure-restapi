# Zendure RestAPI – Home-Assistant-Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/windsurf/hassio-zendure-restapi.svg)](https://github.com/windsurf/hassio-zendure-restapi/releases)
[![100% lokal](https://img.shields.io/badge/100%25-lokal-brightgreen.svg)](#warum-diese-integration)
[![Works with HomeWizard](https://img.shields.io/badge/works%20with-HomeWizard%20P1-1e90ff.svg)](#-unterstützte-zähler)

🇬🇧 [English version](README.md) · 🇳🇱 [Nederlandse versie](README.nl.md)

**Deine Zendure-SolarFlow-Batterie in Home Assistant, 100 % lokal, in drei Schritten.**
Kein Cloud-Konto, kein MQTT-Broker, keine YAML-Pakete zum Kopieren. Über HACS installieren, Batterie hinzufügen, Modus wählen – die Batterie hält deinen Netzbezug auf null, einmal pro Sekunde.

**🔌 Works with HomeWizard.** Du hast schon einen HomeWizard-P1-Zähler? Mehr brauchst du nicht – ein Zendure-Zähler ist nicht nötig. YouLess, EcoTracker, Shelly Pro 3EM und die Zendure-Zähler funktionieren ebenfalls.

![Zendure-Dashboard](images/dashboard.png)

*`smart_matching` hält das Netz auf null: Das Haus braucht 175 W, die Batterie liefert sie, der Zähler zeigt 0 W. Dieses Dashboard liegt der Integration bei.*

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
- **Sieben Betriebsmodi** – vom passiven `standby` bis `smart_matching`, nur laden, nur entladen, schnell laden/entladen und manuell. Steuerbar über deine eigene Preis-Automation.
- **Dein eigener Zähler.** HomeWizard, YouLess, EcoTracker, Shelly Pro 3EM oder Zendure – im Netzwerk gefunden oder per IP hinzugefügt, direkt gelesen, einmal pro Sekunde, nicht über einen langsameren Home-Assistant-Sensor. Oder jeder Leistungssensor, den Home Assistant schon hat.
- **Sicher konstruiert.** Kein gültiger Zählerwert, keine Bewegung: Die Batterie stoppt. Zwei Batterien streiten nie um denselben Zähler.
- **Geringer Standby-Verbrauch, gemessen.** Gibt die Batterie in ihren Sparzustand zurück, wenn nichts zu tun ist: etwa 5 W statt 29–36 W, für jede Stunde Stillstand.
- **Schont den Flash-Speicher.** Die Smart-Modi schreiben ihre Grenzwerte in den Arbeitsspeicher, nicht in den Flash – Zehntausende Schreibvorgänge pro Jahr, die das Gerät nicht abnutzen.
- **Lässt eine volle Batterie in Ruhe.** An den eigenen SOC-Grenzen des Geräts wird nichts in die gesperrte Richtung bestellt: Eine ganze Nacht bei 100 % kostete zwei Grenzwertänderungen, und der Status sagt, warum.
- **Alles sichtbar.** Fertiges Dashboard, ein Controller-Status, der sagt *warum*, Lade- und Entladewirkungsgrad als Sensoren und ein optionaler CSV-Trace pro Sekunde für Genaue.
- **Jede Änderung vor dem Release gemessen.** Änderungen an der Regelschleife werden zuerst an aufgezeichneten Messreihen pro Sekunde nachgespielt; die Messungen und die Korrekturen an Zendures Dokumentation stehen öffentlich in der technischen Referenz.
- **Spricht deine Sprache.** Einrichtungsdialoge und Zustände auf Deutsch, Englisch, Niederländisch, Französisch, Italienisch, Spanisch, Portugiesisch, Polnisch, Tschechisch, Schwedisch und Dänisch.

---

## 🚀 Loslegen

**1. Lokale API einschalten** – unter EN 18031 ist sie standardmäßig aus.
In der Zendure-App **HEMS** hinzufügen und die App danach schließen. Im Browser prüfen:
`http://<IP-der-Batterie>/properties/report` muss JSON zeigen.

**2. Über HACS installieren**

[![In HACS öffnen](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=windsurf&repository=hassio-zendure-restapi&category=integration)

Oder: HACS → Drei-Punkte-Menü → Benutzerdefinierte Repositories → `https://github.com/windsurf/hassio-zendure-restapi`, Kategorie **Integration**. **Zendure RestAPI** installieren und Home Assistant neu starten.

**3. Geräte hinzufügen**
Home Assistant findet Batterie und P1-Zähler meist selbst, unter **Einstellungen → Geräte & Dienste**. Sonst **Zendure RestAPI** hinzufügen, **Zendure-Gerät** wählen und die IP-Adresse eingeben. **Operation mode** auf `smart_matching` stellen und zusehen, wie das Netz auf null geht.

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

**Neueste Version – v1.5.5:** nur Dokumentation – wie sich diese Integration zu Zendures zenSDK verhält, eine neue Messung zweier Zähler nebeneinander und der Controller-Status im Recorder. **v1.5.4:** Die Smart-Modi bestellen nichts mehr in eine volle oder leere Batterie und sagen, warum; `manual` auf 0 W kann ruhen. **v1.5.3:** mehrere externe Zähler nebeneinander, mit einem Select *External meter* an der Batterie zur Auswahl; jeder Leistungssensor in Home Assistant als externer Zähler. **v1.5.2:** der externe P1-Zähler als eigenes Gerät mit Sensoren für Leistung, Status und Energie; HomeWizard-P1-Zähler werden automatisch gefunden; Einrichtungsdialoge in elf Sprachen; externer Zähler im Dashboard. Beim Update von v1.5.0/v1.5.1 ziehen deine Zählereinstellungen von selbst um.

## Mehr

- 🐞 [Issues](https://github.com/windsurf/hassio-zendure-restapi/issues) – Fehler, Fragen und [Modellberichte](https://github.com/windsurf/hassio-zendure-restapi/issues/new?template=new_model.yml). Deutsch ist willkommen.
- ⭐ Nützlich? Ein Stern für dieses Repository hilft anderen, es zu finden.

---

> **Haftungsausschluss:** Keine Verbindung zu Zendure, nicht von Zendure unterstützt. Bereitgestellt wie besehen, ohne Gewährleistung. Nutzung auf eigene Gefahr.

Lizenz: MIT – siehe [LICENSE](LICENSE).
