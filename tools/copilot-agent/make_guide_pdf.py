#!/usr/bin/env python3
"""French installation guide (PDF) for tenant admins, sent with the agent package.

    python tools/copilot-agent/make_guide_pdf.py [--out PATH]
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("copilot_build", HERE / "build_package.py")
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)


def make(out: Path) -> None:
    ss = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=ss["BodyText"], fontName="Helvetica", fontSize=10, leading=14, spaceAfter=6)
    h1 = ParagraphStyle("h1", parent=ss["Heading1"], fontName="Helvetica-Bold", fontSize=16, spaceAfter=4)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontName="Helvetica-Bold", fontSize=12, spaceBefore=10, spaceAfter=4)
    small = ParagraphStyle("s", parent=body, fontSize=8.5, leading=11, textColor=colors.HexColor("#555555"))
    cell = ParagraphStyle("c", parent=body, fontSize=9, leading=12, spaceAfter=0)

    def p(text, style=body):
        return Paragraph(text, style)

    n = len(build.PINNED_TOOLS)
    story = [
        p("Agent OpenCaseLaw pour Microsoft 365 Copilot", h1),
        p("Guide d'installation pour l'administration du tenant &middot; paquet "
          f"<b>opencaselaw-copilot-agent.zip</b>, version {build.APP_VERSION}", small),
        Spacer(1, 4 * mm),
        p("En bref", h2),
        p("L'agent donne accès, depuis Microsoft 365 Copilot Chat, à la jurisprudence suisse (fédérale et "
          "cantonale), à la législation fédérale et cantonale, aux Messages du Conseil fédéral, aux commentaires "
          "et à la doctrine en libre accès d'OpenCaseLaw. Il est chargé une seule fois par l'administration ; "
          "les utilisateurs et utilisatrices l'ajoutent ensuite depuis le magasin d'agents de Copilot Chat."),
        p("Testé", h2),
        p("L'agent a été installé et testé dans un tenant Microsoft 365 disposant uniquement de Copilot Chat de "
          "base (M365 Copilot Basic), sans licence Copilot Studio ni crédits : chargement via le centre "
          "d'administration Teams, ajout depuis le magasin d'agents (indiqué « gratuit »), réponses avec "
          "citations textuelles d'articles de loi et liens vers les arrêts. Les manifestes sont en outre validés "
          "contre les schémas JSON publiés par Microsoft."),
        p("Licences", h2),
        p("Aucune licence Copilot Studio, aucun crédit Copilot et aucune licence Microsoft 365 Copilot ne sont "
          "nécessaires. Selon le tableau de Microsoft (<i>Agent capabilities and licensing models</i>, "
          "learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/prerequisites), les agents déclaratifs "
          "dotés d'actions personnalisées sont disponibles dans Copilot Chat sans facturation à l'usage. Seuls "
          "les agents qui accèdent à des données du tenant (SharePoint, Graph, connecteurs Copilot) sont "
          "facturés ; cet agent n'y accède pas. Les limites d'utilisation habituelles de Copilot Chat s'appliquent."),
        p("Contenu du paquet", h2),
    ]
    rows = [
        ["Fichier", "Contenu"],
        ["manifest.json", "Manifeste d'application 1.30"],
        ["declarativeAgent.json", "Agent déclaratif 1.8 : instructions, règles de citation, suggestions de questions"],
        ["ai-plugin.json", "Plugin 2.4 : un runtime RemoteMCPServer (https://mcp.opencaselaw.ch/mcp), authentification None"],
        ["mcp-tools.json", f"Définitions des {n} outils autorisés, en lecture seule, figées dans le paquet"],
        ["color.png, outline.png", "Icônes"],
    ]
    table = Table([[p(a, cell), p(b, cell)] for a, b in rows], colWidths=[45 * mm, 120 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#bbbbbb")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story += [
        table, Spacer(1, 2 * mm),
        p(f"L'agent ne peut appeler que ces {n} outils de recherche, tous marqués en lecture seule, et la liste ne "
          "change qu'avec une nouvelle version du paquet. Les outils d'OpenCaseLaw reposant sur un modèle d'IA "
          "(audits, décisions fictives, questions d'examen, analyses de tendances) sont exclus."),
        p("Flux de données", h2),
    ]
    story += [p("&bull;&nbsp;" + t) for t in [
        "Copilot transmet depuis le cloud de Microsoft les appels d'outils (termes de recherche, numéros "
        "d'articles, références d'arrêts) à mcp.opencaselaw.ch. Le runtime est configuré sans authentification : "
        "aucun jeton ni identité d'utilisateur n'est transmis, et OpenCaseLaw ne voit que les adresses IP de Microsoft.",
        "L'agent a pour consigne de ne transmettre que des termes juridiques et d'anonymiser les faits "
        "(«&nbsp;locataire&nbsp;», «&nbsp;employeur&nbsp;») avant tout appel d'outil.",
        "L'agent appelle un point d'accès propre (mcp.opencaselaw.ch/mcp-edu). Pour ces requêtes, "
        "OpenCaseLaw ne conserve ni le texte des requêtes ni d'identifiant de session : pas d'archive de "
        "recherche, pas d'entraînement de modèles. Seuls des journaux techniques subsistent.",
        "Au moment de la requête, le texte de recherche et de courts extraits de décisions sont transmis à "
        "l'API d'Anthropic (Claude) pour l'analyse et le reclassement, sans adresse IP ni identifiant ; "
        "pour les actes cantonaux, le texte de recherche est transmis à LexFind (Suisse).",
        "Le journal d'accès du serveur (adresse IP, user agent) est supprimé après 72 heures.",
        "Détails : https://opencaselaw.ch/datenschutz/. Il est recommandé d'indiquer aux utilisateurs de ne pas "
        "saisir de données personnelles de tiers.",
    ]]
    story += [p("Installation", h2)]
    story += [p(f"{i}.&nbsp;{s}") for i, s in enumerate([
        "Centre d'administration Teams (admin.teams.microsoft.com) &gt; <i>Teams apps &gt; Manage apps</i> &gt; "
        "<i>Actions &gt; Upload new app</i>, puis choisir <b>opencaselaw-copilot-agent.zip</b>. Lors de la "
        "première utilisation de cette page, Microsoft peut mettre jusqu'à 30 minutes à activer la fonction.",
        "Sous <i>Available to</i>, définir qui peut utiliser l'agent (par défaut : toute l'organisation).",
        "Les utilisateurs ouvrent Copilot Chat (https://m365.cloud.microsoft/chat), <i>Agents &gt; Weitere "
        "Agents / Autres agents</i>, cherchent <b>OpenCaseLaw</b> (rubrique « Créé par votre organisation ») et "
        "cliquent sur <i>Ajouter</i>. L'agent apparaît quelques minutes après le chargement.",
        "Au premier appel, Copilot demande une seule fois l'autorisation de se connecter à OpenCaseLaw "
        "(<i>Autoriser</i>). Ensuite, les recherches s'exécutent sans nouvelle confirmation.",
        "Mise à jour : charger une nouvelle version sous <i>New version &gt; Upload file</i> sur la page de l'app. "
        "Les utilisateurs qui ont déjà ajouté l'agent reçoivent la mise à jour avec un certain délai.",
    ], 1)]
    story += [
        p("Contact", h2),
        p("Jonas Hertner, OpenCaseLaw &middot; team@jonashertner.com &middot; https://opencaselaw.ch. Données "
          "publiées sous CC0, code sous licence MIT ; l'accès est gratuit."),
    ]
    doc = SimpleDocTemplate(
        str(out), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
        title="Agent OpenCaseLaw pour Microsoft 365 Copilot - guide d'installation", author="OpenCaseLaw")
    doc.build(story)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=HERE / "dist" / "OpenCaseLaw-Copilot-guide-installation.pdf")
    args = ap.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    make(args.out)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
