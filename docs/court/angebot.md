# Zitatprüfung für Eingaben und Urteilsentwürfe

Lokal, ohne Netz, auf dem Arbeitsplatz oder dem Server des Gerichts.

## Was es tut

Eingaben der Parteien kommen als PDF (PDF/A aus dem elektronischen Rechtsverkehr über justitia.swiss; gescannte Eingaben ohne Textebene brauchen zuerst eine Texterkennung). Das Programm liest die Eingabe oder einen ganzen Ordner mit Eingaben, findet jede Zitierung und jedes wörtliche Zitat, prüft sie gegen den Korpus und schreibt zu jeder Eingabe einen Bericht und zum Ordner eine Übersicht: welche zitierten Entscheide es nicht gibt, welche Erwägungen oder Zitate vom Entscheidtext abweichen, und wo ein «nicht im publizierten Korpus» erwartet ist (unpublizierter Entscheid, angefochtener Entscheid). Ob das Argument trägt, sagt der Bericht nie; das bleibt Sache des Gerichts. Dieselbe Prüfung gilt dem Urteilsentwurf als Word-Datei (inklusive Fussnoten) vor der Zirkulation. Weder Eingabe noch Entwurf verlassen das Gerät. Es gibt keine Anmeldung, keine Cloud, keine Protokollierung.

Ein Rechtsklick auf die Datei genügt. Der Bericht liegt nach wenigen Sekunden daneben und öffnet sich im Browser.

## Was geprüft wird

| Prüfung | Ergebnis im Bericht |
|---|---|
| Der zitierte Entscheid existiert und ist der gemeinte (BGE, BGer, BVGer, BStGer, kantonale Gerichte) | gefunden, nicht gefunden, mehrdeutig |
| Die zitierte Erwägung (E. 3.2) existiert im Entscheid | Passage abgerufen, nicht indexiert |
| Das wörtliche Zitat stimmt mit dem Entscheidtext überein | wortgleich, abweichend (mit Differenz), nicht gefunden |
| Datum und Aktenzeichen neben der Zitierung stimmen mit dem Entscheid überein | abweichend (geschrieben vs. Entscheid) |
| Gesetzesartikel (Art. 8 Abs. 1 ZGB, art. 335 al. 1 CO, § 12 StG/ZH) existieren im Erlass | gefunden, Artikel fehlt, Erlass unbekannt |
| Zitierähnliche Angaben, die nicht geprüft werden konnten | separat aufgelistet |

Bei Gerichten, deren Entscheide nur teilweise publiziert sind, sagt der Bericht das ausdrücklich: "nicht im publizierten Korpus" ist etwas anderes als "falsch zitiert". Unpublizierte eigene Entscheide und der angefochtene Entscheid sind in keinem Korpus.

## Was nicht geprüft wird

Ob ein Entscheid die Aussage trägt, ob die Praxis seither geändert wurde und ob der Entwurf anonymisiert ist. Das Programm prüft Existenz, Identität und Wortlaut. Es ersetzt keine Lektüre.

## Der Korpus

| Inhalt | Umfang |
|---|---|
| Entscheide, 1875 bis heute, 118 Gerichte | 1,07 Mio. |
| Erwägungen mit Nummerierung erschlossen | 76 % der Entscheide; Bundesgericht 88 %, Obergericht Zürich 96 % |
| Bundeserlasse (Fedlex) | 5'500 |
| Kantonale Erlasse | 15'600 |
| Lokales Prüfpaket, wöchentlich erneuert | ca. 9 GB |

Daten und Code sind offen (CC0 und MIT). Das Gericht kann beides jederzeit selbst prüfen und weiterverwenden.

## Was das Gericht kauft

Nicht den Inhalt, sondern den Betrieb: signierte Installationspakete für Windows und macOS, den wöchentlichen Paketspiegel mit Prüfsummen, Updates, Support, und die Kalibrierung auf die Zitierweise des Gerichts. Preise und Lizenzbedingungen auf Anfrage.

## Pilot

Drei Monate mit einem Gericht. Das Gericht stellt zweihundert publizierte eigene Entscheide als Testmenge; der Anbieter kalibriert die Erkennung darauf und misst die Trefferquote. Am Ende liegt ein Messbericht vor, und das Gericht entscheidet. Die Pilotvereinbarung ist auf Anfrage erhältlich; Datenfluss in `datenfluss.md`, Installationsanleitung für die IT in `../court-it-install.md`.
