#!/usr/bin/env python3
"""Data-protection fact sheet (French) for a university DPO reviewing the Copilot agent.

Every statement mirrors the public privacy policy (docs/datenschutz/index.html).
Change both together.

    python tools/copilot-agent/make_dpo_sheet_pdf.py [--out PATH]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

HERE = Path(__file__).resolve().parent


def make(out: Path) -> None:
    ss = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=ss["BodyText"], fontName="Helvetica", fontSize=9.5, leading=13, spaceAfter=5)
    h1 = ParagraphStyle("h1", parent=ss["Heading1"], fontName="Helvetica-Bold", fontSize=15, spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontName="Helvetica-Bold", fontSize=11.5, spaceBefore=8, spaceAfter=4)
    small = ParagraphStyle("s", parent=body, fontSize=8.5, leading=11, textColor=colors.HexColor("#555555"))
    cell = ParagraphStyle("c", parent=body, fontSize=8.8, leading=11.5, spaceAfter=0)
    cellb = ParagraphStyle("cb", parent=cell, fontName="Helvetica-Bold")

    def p(text, style=body):
        return Paragraph(text, style)

    def table(rows, widths, header=True):
        t = Table([[p(c, cellb if (header and i == 0) or j == 0 else cell) for j, c in enumerate(r)]
                   for i, r in enumerate(rows)], colWidths=widths)
        style = [("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#bbbbbb")),
                 ("VALIGN", (0, 0), (-1, -1), "TOP"),
                 ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
        if header:
            style.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")))
        t.setStyle(TableStyle(style))
        return t

    story = [
        p("Agent OpenCaseLaw pour Microsoft 365 Copilot", h1),
        p("Fiche de protection des données &middot; septembre 2026", small),
        Spacer(1, 3 * mm),

        p("En bref", h2),
        p("&bull;&nbsp;L'agent envoie à OpenCaseLaw uniquement des termes de recherche juridique. "
          "Aucune identité, aucun compte, aucun jeton, aucune adresse IP d'utilisateur."),
        p("&bull;&nbsp;OpenCaseLaw ne peut donc rattacher aucune requête à une personne de l'UNIL, "
          "sauf si une personne saisit elle-même des données personnelles dans sa question."),
        p("&bull;&nbsp;Les requêtes de l'agent ne sont pas conservées : pas d'archive, pas d'entraînement de "
          "modèles, pas de profils. Seuls des journaux techniques subsistent (section 3)."),
        p("&bull;&nbsp;Certaines recherches sont transmises à Anthropic (États-Unis) pour l'analyse et le "
          "reclassement, et la recherche d'actes cantonaux à LexFind (Suisse)."),

        p("1. Rôles", h2),
        table([
            ["Responsable du traitement", "Jonas Hertner (OpenCaseLaw), team@jonashertner.com"],
            ["Hébergement", "Actuellement Hetzner Online GmbH, Nuremberg (Allemagne). Le serveur sera "
                            "prochainement hébergé par une institution publique en Suisse."],
            ["Destinataires", "Anthropic PBC (États-Unis) : analyse et reclassement de certaines recherches. "
                              "LexFind (lexfind.ch, Suisse) : recherche d'actes législatifs cantonaux."],
            ["Côté UNIL", "Copilot Chat est exploité par Microsoft dans le cadre du contrat UNIL–Microsoft. "
                          "Cette fiche couvre uniquement ce qui arrive chez OpenCaseLaw."],
        ], [42 * mm, 128 * mm], header=False),

        p("2. Données reçues", h2),
        table([
            ["Reçu", "Paramètres des appels d'outils choisis par Copilot : termes de recherche, numéros "
                     "d'articles, références d'arrêts, filtres (tribunal, langue, date)."],
            ["Non reçu", "Nom, e-mail ou identifiant Microsoft de l'utilisateur, jeton d'authentification, "
                         "adresse IP de l'utilisateur, reste de la conversation Copilot."],
            ["Adresse IP", "Celle des serveurs de Microsoft, pas celle de l'utilisateur."],
            ["Minimisation", "L'agent a pour consigne de n'envoyer que des termes juridiques et d'anonymiser "
                             "les faits (« locataire », « employeur ») avant tout appel."],
        ], [42 * mm, 128 * mm], header=False),
        Spacer(1, 1 * mm),
        p("Nous recommandons d'indiquer aux utilisateurs de ne pas saisir de données personnelles de tiers "
          "dans leurs questions.", small),

        p("3. Ce qui est enregistré", h2),
        p("L'agent appelle un point d'accès propre (mcp.opencaselaw.ch/mcp-edu). Pour les requêtes qui "
          "y arrivent, les seuls enregistrements sont :"),
        table([
            ["Enregistrement", "Contenu", "Durée"],
            ["Journal d'accès", "Adresse IP (ici : Microsoft), user agent, URL", "72 heures"],
            ["Journal d'application", "Nom de l'outil et paramètres structurels (numéros d'articles, "
                                      "références d'arrêts, filtres). Aucun texte libre, aucune adresse IP.",
             "Rotation technique"],
            ["Journal technique", "Classes de client et d'endpoint, code HTTP, temps de réponse. "
                                  "Aucune donnée personnelle.", "14 jours"],
            ["Statistiques et coûts", "Agrégats journaliers ; pour les appels à Anthropic : modèle, nombre de "
                                      "tokens, coût. Sans adresse IP ni texte de requête.", "Illimitée"],
        ], [42 * mm, 100 * mm, 28 * mm]),
        Spacer(1, 1 * mm),
        p("Ne sont pas enregistrés : le texte des requêtes, les identifiants de session, les traces de "
          "recherche, le registre des coûts par adresse IP. Les requêtes de l'agent n'entrent dans aucune "
          "archive de recherche et ne servent pas à entraîner des modèles.", body),
        p("Transmissions au moment de la requête : à Anthropic, le texte de la requête et de courts extraits "
          "de décisions candidates, sans adresse IP ni identifiant, selon les conditions commerciales de "
          "l'API d'Anthropic ; à LexFind, le texte de recherche pour les actes cantonaux.", body),
        p("Les outils d'OpenCaseLaw qui envoient des textes plus longs à un modèle d'IA (audit de citations, "
          "décisions fictives, questions d'examen) ne font pas partie de l'agent.", small),

        p("4. Sécurité, transparence, droits", h2),
        p("Service public en lecture seule, sans comptes. Connexions chiffrées (HTTPS). Le code du serveur, "
          "de la collecte et de l'agent est open source (licence MIT) : github.com/jonashertner/opencaselaw. "
          "Politique de confidentialité complète : opencaselaw.ch/datenschutz. Droits d'accès, de rectification "
          "et de suppression selon la nLPD et le RGPD : team@jonashertner.com ; comme aucune requête n'est "
          "liée à une personne, leur exercice est limité en pratique."),
        p("Contact : Jonas Hertner, team@jonashertner.com, +41 43 215 08 50.", body),
    ]
    doc = SimpleDocTemplate(
        str(out), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
        title="Agent OpenCaseLaw pour Microsoft 365 Copilot - fiche de protection des données",
        author="OpenCaseLaw")
    doc.build(story)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=HERE / "dist" / "OpenCaseLaw-Copilot-protection-des-donnees.pdf")
    args = ap.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    make(args.out)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
