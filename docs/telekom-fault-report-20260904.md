# Telekom fault report — repeated PPPoE session terminations, 2026-09-04

Evidence gathered from the GL.iNet BE9300 router at 192.168.9.1. **Do not include the PPPoE
username or password in any correspondence** — Telekom identifies the line by the customer
number / Anschlusskennung, which the account holder should fill in.

## Observed events (all 2026-09-04, Europe/Berlin)

Exact timestamps recovered from Loki, which ingests the router's syslog — the router's own
buffer had already rotated. **Seven** session terminations in nine and a half hours:

| session terminated | reconnected | new WAN IP | previous session lasted | outage |
|---|---|---|---|---|
| 03:47:18 | 03:50:59 | 84.149.188.254 | — | **3m41s** |
| 06:13:38 | 06:21:49 | 84.149.176.13 | 151 min | **8m11s** |
| 08:19:36 | 08:19:55 | 217.232.8.166 | 118 min | 19s |
| 08:30:44 | 08:37:16 | 217.232.7.40 | **17 min** | **6m32s** |
| 10:36:16 | 10:36:35 | 217.232.13.64 | 119 min | 19s |
| 11:53:55 | 11:54:11 | 217.232.9.150 | 78 min | 16s |
| 13:14:15 | 13:14:34 | 217.232.6.212 | 80 min | 19s |

Three points the table makes that a bare count does not:

* **One session survived only 17 minutes** (08:19:55 to 08:30:44).
* **Two outages exceeded six minutes** — 8m11s and 6m32s — far beyond a routine
  re-establishment, which the same line does in 16–19 seconds when it is behaving.
* **The address pool changed** mid-day, from `84.149.x.x` to `217.232.x.x`, suggesting
  re-authentication against a different BRAS rather than a simple lease renewal.

Router log at each event:
```
Connection terminated.
Modem hangup
Exit.
Plugin pppoe.so loaded.
pppd 2.4.9 started by root, uid 0
```

## Why this is not a customer-side problem
* The router's own LCP keepalive is aggressive — `lcp-echo-interval 1`, `lcp-echo-failure 5`,
  adaptive — so a peer that stops answering is detected in about five seconds. The router is
  not failing to notice a dead session; the session is being **terminated**.
* `Modem hangup` indicates the session was torn down from the line/BRAS side, not renegotiated
  by the router.
* Reconnection itself is healthy: PPPoE re-establishes in ~3 seconds and authenticates
  immediately, so the CPE and credentials are fine.
* Telekom's normal behaviour is a single forced reconnect per 24 hours. Seven in nine and a
  half hours is not that.

---

## German text to send (copy from here down)

> **Betreff: Wiederholte PPPoE-Verbindungsabbrüche — Bitte um Leitungsprüfung**
>
> Sehr geehrte Damen und Herren,
>
> an meinem Telekom-Anschluss (Kundennummer: __________, Tarif **MagentaZuhause XL**,
> VDSL 250) kommt es zu wiederholten
> PPPoE-Verbindungsabbrüchen. Allein am 04.09.2026 wurde die Verbindung zwischen 03:47 und
> 13:14 Uhr **siebenmal** getrennt und jeweils mit einer neuen IP-Adresse neu aufgebaut:
>
> | Trennung | Neuaufbau | neue IP | Ausfalldauer |
> |---|---|---|---|
> | 03:47:18 | 03:50:59 | 84.149.188.254 | 3 min 41 s |
> | 06:13:38 | 06:21:49 | 84.149.176.13 | **8 min 11 s** |
> | 08:19:36 | 08:19:55 | 217.232.8.166 | 19 s |
> | 08:30:44 | 08:37:16 | 217.232.7.40 | **6 min 32 s** |
> | 10:36:16 | 10:36:35 | 217.232.13.64 | 19 s |
> | 11:53:55 | 11:54:11 | 217.232.9.150 | 16 s |
> | 13:14:15 | 13:14:34 | 217.232.6.212 | 19 s |
>
> Eine Sitzung bestand dabei nur 17 Minuten. Zwei Ausfälle dauerten über sechs Minuten,
> obwohl derselbe Anschluss im Normalfall innerhalb von 16 bis 19 Sekunden wieder aufgebaut
> wird. Zudem wechselte der Adressbereich von 84.149.x.x auf 217.232.x.x.
>
> Im Router-Protokoll erscheint bei jedem Abbruch:
>
> `Connection terminated. / Modem hangup / Exit.`
>
> Der Router meldet also einen von der Gegenstelle beendeten Sitzungsaufbau, keinen
> Fehler auf meiner Seite. Die LCP-Überwachung des Routers ist eng eingestellt
> (`lcp-echo-interval 1`, `lcp-echo-failure 5`), und der Neuaufbau der Verbindung
> funktioniert jeweils innerhalb von etwa drei Sekunden einschließlich Authentifizierung.
> Die Zugangsdaten und das Endgerät funktionieren demnach einwandfrei.
>
> Eine einmalige Zwangstrennung pro 24 Stunden ist mir bekannt und wäre unproblematisch.
> Sieben Trennungen innerhalb von neuneinhalb Stunden sind es nicht — sie unterbrechen laufende
> Videoübertragungen und Aufnahmen.
>
> Laut Auftragsunterlagen sind für diesen Tarif eine minimale Download-Geschwindigkeit von
> 175 MBit/s, eine normalerweise verfügbare von 200 MBit/s und eine maximale von 250 MBit/s
> vereinbart. Während der oben genannten Ausfälle stand überhaupt keine Verbindung zur
> Verfügung. Eine Rückfalloption (Hybrid LTE bzw. 5G Backup) wurde nicht beauftragt, sodass
> jeder Sitzungsabbruch einen vollständigen Ausfall des Anschlusses bedeutet.
>
> Ich bitte daher um eine Prüfung der Leitung und des zugehörigen DSLAM-Ports auf
> Instabilität sowie um Rückmeldung, ob auf Netzseite Sitzungsabbrüche protokolliert sind.
>
> **Ich bitte ausdrücklich um eine Antwort in schriftlicher Form (E-Mail), da ich der
> deutschen Sprache nicht ausreichend mächtig bin, um das Anliegen telefonisch zu klären.**
>
> Mit freundlichen Grüßen
> ____________________

---

## What that says, in English
States the line has repeated PPPoE drops; lists the four timestamps and IPs; quotes the
`Modem hangup` log; explains the router's keepalive is tight and reconnection works, so the
fault is not customer-side; notes one forced reconnect per day is expected but four in six
hours is not; asks them to check the line and DSLAM port for instability and to confirm
whether network-side session drops are logged; **and explicitly asks for a written reply
because the account holder does not speak sufficient German.**

---

## Contract facts that strengthen the case

From the original order (Telekom Deutschland GmbH, sold via a reseller; the order states
plainly that **the contract is between Telekom Deutschland GmbH and the customer** and that the
reseller has no influence on activation, bandwidth or availability — so Telekom is the
responsible party):

* Product: **MagentaZuhause XL**, VDSL 250, 24-month initial term from 2024-04-05, now on
  rolling renewal with one month's notice.
* Contractually stated transmission rates for this tariff:

  | | minimum | normally available | maximum |
  |---|---|---|---|
  | download | **175 Mbit/s** | 200 | 250 |
  | upload | 20 Mbit/s | 35 | 40 |

* **No fallback was contracted.** Sections 18.1 (Hybrid LTE Backup) and 18.2 (Hybrid 5G/LTE
  Backup) are both unchecked, so every PPPoE termination is a total loss of service with no
  mobile failover. This is why a session drop is not a minor event on this line.

Why that matters: a service that terminates seven times in nine and a half hours, twice for
more than six minutes, is not delivering the contracted product during those windows.
Germany's TKG gives customers remedies where a provider persistently fails to deliver the
agreed service, so it is worth stating the contracted figures explicitly rather than only
describing the symptom.

**Two practical points before sending:**
1. The contract holder is not necessarily the person raising the fault. Telekom will normally
   only discuss the line with the named contract holder, so the letter should go out in that
   name or with written authorisation.
2. The Kundennummer is **not** on the original order — it was a new connection, so the number
   was assigned afterwards. Take it from an invoice or the Kundencenter.

## Where to find your Kundennummer

Log in at **[meinkundencenter.telekom.de](https://meinkundencenter.telekom.de)** (or
[telekom.de/mein-kundencenter](https://www.telekom.de/mein-kundencenter)) with your Telekom
Login — usually your email address plus password. The customer number is under
**Vertragspartner / contract partner**.

It is also on **every invoice**, in the **MeinMagenta app** (user symbol -> Persönliche Daten
-> Vertragspartner), and in the reference text of the Telekom direct debit on your bank
statement. Telekom's own guide: [Wo finde ich meine Kundennummer?](https://www.telekom.de/hilfe/vertrag-rechnung/vertrag/meine-daten/kundennummer)
