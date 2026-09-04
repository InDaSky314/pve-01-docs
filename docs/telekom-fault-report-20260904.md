# Telekom fault report — repeated PPPoE session terminations, 2026-09-04

Evidence gathered from the GL.iNet BE9300 router at 192.168.9.1. **Do not include the PPPoE
username or password in any correspondence** — Telekom identifies the line by the customer
number / Anschlusskennung, which the account holder should fill in.

## Observed events (all 2026-09-04, Europe/Berlin)

| time | new WAN IP | session lifetime |
|---|---|---|
| early morning | 217.232.7.40 | — |
| 10:36 | 217.232.13.64 | ~90 min |
| 11:54 | 217.232.9.150 | ~78 min |
| 13:14 | 217.232.6.212 | current |

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
* Telekom's normal behaviour is a single forced reconnect per 24 hours. Four in roughly six
  hours is not that.

---

## German text to send (copy from here down)

> **Betreff: Wiederholte PPPoE-Verbindungsabbrüche — Bitte um Leitungsprüfung**
>
> Sehr geehrte Damen und Herren,
>
> an meinem Telekom-Anschluss (Anschlusskennung / Kundennummer: __________) kommt es seit
> mehreren Tagen zu wiederholten PPPoE-Verbindungsabbrüchen. Am 04.09.2026 wurde die
> Verbindung innerhalb von etwa sechs Stunden viermal getrennt und jeweils mit einer neuen
> IP-Adresse neu aufgebaut:
>
> * ca. 08:00 Uhr — 217.232.7.40
> * 10:36 Uhr — 217.232.13.64
> * 11:54 Uhr — 217.232.9.150
> * 13:14 Uhr — 217.232.6.212
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
> Vier Trennungen innerhalb von sechs Stunden sind es nicht — sie unterbrechen laufende
> Videoübertragungen und Aufnahmen.
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
