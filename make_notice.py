"""Compose la notice d'utilisation KoboMeuble Studio en PDF (WeasyPrint)."""
import base64
import io
import os

import numpy as np
from PIL import Image
from weasyprint import HTML

HERE = "/Users/lenapatarin/Documents/POTECH M1/KOBO/code"
OUT = os.path.join(HERE, "NOTICE_UTILISATION.pdf")


def logo_data_uri(max_w=900):
    """Logo KOBO inversé (le PNG est blanc) pour rester lisible sur fond clair."""
    path = os.path.join(HERE, "kobo_logo.png")
    if not os.path.exists(path):
        return None
    img = Image.open(path).convert("RGBA")
    a = np.array(img)
    if a[:, :, :3].mean() > 180:                 # logo blanc -> on l'inverse
        a[:, :, :3] = 255 - a[:, :, :3]
    img = Image.fromarray(a)
    bbox = img.getchannel("A").getbbox()         # rogne la marge transparente
    if bbox:
        img = img.crop(bbox)
    if img.width > max_w:
        img = img.resize((max_w, int(img.height * max_w / img.width)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


LOGO = logo_data_uri()

HTML_DOC = f"""
<!doctype html>
<html lang="fr">
<head><meta charset="utf-8"><title>Notice d'utilisation — KoboMeuble Studio</title>
<style>
  @page {{
    size: A4;
    margin: 20mm 18mm 18mm 18mm;
    @bottom-left  {{ content: "KoboMeuble Studio — Notice d'utilisation";
                     font-family: "DejaVu Sans", sans-serif; font-size: 7.5pt; color: #8A8A8A; }}
    @bottom-right {{ content: counter(page) " / " counter(pages);
                     font-family: "DejaVu Sans", sans-serif; font-size: 7.5pt; color: #8A8A8A; }}
  }}
  @page :first {{ margin: 0; @bottom-left {{ content: "" }} @bottom-right {{ content: "" }} }}

  * {{ box-sizing: border-box; }}
  body {{ font-family: "DejaVu Sans", "Helvetica Neue", Arial, sans-serif;
          font-size: 9.6pt; line-height: 1.55; color: #1E1E1E; margin: 0; }}

  /* ---------- couverture ---------- */
  .cover {{ page-break-after: always; height: 297mm; padding: 38mm 24mm 0 24mm;
            position: relative; }}
  .cover img {{ width: 38mm; margin-bottom: 30mm; }}
  .cover .kicker {{ font-size: 8.5pt; letter-spacing: .22em; text-transform: uppercase;
                    color: #C85529; font-weight: 700; }}
  .cover h1 {{ font-size: 30pt; line-height: 1.1; margin: 6mm 0 4mm 0; font-weight: 700; }}
  .cover h2 {{ font-size: 13pt; font-weight: 400; color: #5A5A5A; margin: 0 0 16mm 0; }}
  .cover .lede {{ font-size: 10.5pt; color: #333; max-width: 118mm; }}
  .cover .bar {{ position: absolute; left: 0; right: 0; bottom: 26mm; height: 9mm;
                 background: #C85529; }}
  .cover .foot {{ position: absolute; left: 24mm; bottom: 12mm; font-size: 8pt; color: #8A8A8A; }}

  /* ---------- structure ---------- */
  h2.sec {{ font-size: 15pt; margin: 0 0 1mm 0; padding-bottom: 2mm;
            border-bottom: 2px solid #C85529; page-break-after: avoid; }}
  h2.sec .n {{ color: #C85529; margin-right: 3mm; }}
  .sec-note {{ color: #5A5A5A; font-size: 9pt; margin: 2mm 0 5mm 0; page-break-after: avoid; }}
  h3 {{ font-size: 10.6pt; margin: 6mm 0 1.5mm 0; page-break-after: avoid; }}
  section {{ margin-bottom: 9mm; }}
  p {{ margin: 0 0 2.5mm 0; }}
  ul, ol {{ margin: 0 0 3mm 0; padding-left: 5mm; }}
  li {{ margin-bottom: 1.2mm; }}
  b, strong {{ font-weight: 700; }}

  /* libellé repris tel quel de l'écran */
  .ui {{ font-family: "DejaVu Sans Mono", monospace; font-size: 8.4pt;
         background: #F1F1F1; border-radius: 2px; padding: 0.3mm 1.2mm;
         white-space: nowrap; }}

  .callout {{ border-left: 3px solid #C85529; background: #FBF2ED;
              padding: 3mm 4mm; margin: 3mm 0 4mm 0; page-break-inside: avoid; }}
  .callout .t {{ font-weight: 700; color: #A8431F; display: block; margin-bottom: 1mm;
                 font-size: 9.2pt; }}
  .tip {{ border-left-color: #7A7A7A; background: #F4F4F4; }}
  .tip .t {{ color: #3A3A3A; }}

  table {{ width: 100%; border-collapse: collapse; margin: 2mm 0 4mm 0;
           page-break-inside: avoid; }}
  th, td {{ text-align: left; vertical-align: top; padding: 2mm 2.5mm;
            border-bottom: 1px solid #E2E2E2; font-size: 9pt; }}
  th {{ background: #F5F5F5; font-size: 8.4pt; text-transform: uppercase;
        letter-spacing: .06em; color: #5A5A5A; border-bottom: 1.5px solid #CFCFCF; }}
  td.file {{ white-space: nowrap; font-family: "DejaVu Sans Mono", monospace;
             font-size: 8.4pt; }}

  /* sommaire */
  .toc {{ page-break-after: always; }}
  .toc ol {{ list-style: none; padding: 0; counter-reset: toc; }}
  .toc li {{ counter-increment: toc; padding: 2.4mm 0; border-bottom: 1px dotted #D5D5D5;
             font-size: 10pt; }}
  .toc li::before {{ content: counter(toc) "."; color: #C85529; font-weight: 700;
                     display: inline-block; width: 8mm; }}
  .toc li span {{ color: #6A6A6A; font-size: 8.8pt; display: block; margin-left: 8mm; }}

  .checklist li {{ margin-bottom: 2.2mm; }}
</style>
</head>
<body>

<div class="cover">
  {'<img src="' + LOGO + '" alt="KOBO">' if LOGO else ''}
  <div class="kicker">Notice d'utilisation</div>
  <h1>KoboMeuble<br>Studio</h1>
  <h2>Conception de meubles et préparation des plans techniques</h2>
  <p class="lede">De la saisie du chantier jusqu'aux fichiers d'atelier : feuille de débit,
  plans d'usinage DXF, maquettes SketchUp et vues 3D cotées. Ce document suit l'ordre réel
  d'utilisation de l'application, écran par écran.</p>
  <div class="bar"></div>
  <div class="foot">Document généré à partir de l'interface en production.</div>
</div>

<div class="toc">
  <h2 class="sec"><span class="n">—</span>Sommaire</h2>
  <p class="sec-note">L'application se parcourt de haut en bas, en quatre étapes.</p>
  <ol>
    <li>Se connecter et comprendre l'écran
        <span>Mot de passe, et où se trouve quoi</span></li>
    <li>Étape 1 — Projet et assemblage
        <span>Informations chantier, chargement d'un projet, pose des caissons, pieds</span></li>
    <li>Étape 2 — Édition détaillée d'un caisson
        <span>Dimensions, portes, tiroirs, étagères, montants, joues, feuille de débit</span></li>
    <li>Étape 3 — Contrôle de la prévisualisation 3D
        <span>La dernière vérification avant de produire les fichiers</span></li>
    <li>Étape 4 — Générer et télécharger les livrables
        <span>Filtre matière, génération, et ce que contient chaque fichier</span></li>
    <li>Reprendre un projet plus tard
        <span>Le fichier Excel sert aussi de sauvegarde</span></li>
    <li>Les erreurs qui coûtent cher
        <span>À vérifier avant chaque envoi en atelier</span></li>
  </ol>
</div>

<!-- 1 -->
<section>
  <h2 class="sec"><span class="n">1</span>Se connecter et comprendre l'écran</h2>
  <p class="sec-note">Deux minutes ici évitent de chercher les boutons ensuite.</p>

  <h3>Ouvrir l'application</h3>
  <p>L'accès est protégé. Saisissez le mot de passe dans le champ
  <span class="ui">Mot de passe</span>, puis cliquez sur
  <span class="ui">Accéder à l'interface</span>. En cas d'erreur de saisie, le champ reste
  affiché et vous pouvez réessayer immédiatement.</p>

  <h3>Comment l'écran est organisé</h3>
  <p>L'interface se lit de gauche à droite, puis de haut en bas :</p>
  <ul>
    <li><b>Colonne de gauche — <span class="ui">Projet et caissons</span></b> : c'est là que
    tout se construit. Elle contient deux onglets,
    <span class="ui">Etape 1 - Projet et Assemblage</span> puis
    <span class="ui">Etape 2 - Edition détaillée</span>.</li>
    <li><b>Zone centrale — <span class="ui">Prévisualisation 3D</span></b> : le meuble se
    redessine à chaque modification. Elle sert à vérifier, pas à modifier.</li>
    <li><b>Bas de page — <span class="ui">Export des livrables</span></b> : la génération et
    le téléchargement des fichiers d'atelier.</li>
  </ul>

  <div class="callout tip">
    <span class="t">Le principe à retenir</span>
    Vous décrivez le meuble à gauche, vous le vérifiez au centre, vous le produisez en bas.
    Rien n'est calculé tant que vous n'avez pas demandé la génération des livrables.
  </div>
</section>

<!-- 2 -->
<section>
  <h2 class="sec"><span class="n">2</span>Étape 1 — Projet et assemblage</h2>
  <p class="sec-note">Onglet <span class="ui">Etape 1 - Projet et Assemblage</span>, quatre
  sous-étapes dans l'ordre.</p>

  <h3>1A — Informations générales du projet</h3>
  <p>Ces informations alimentent la page de garde des plans et l'en-tête de la feuille de
  débit. Renseignez :</p>
  <ul>
    <li><span class="ui">Nom du Projet</span> et <span class="ui">Date souhaitée</span></li>
    <li><span class="ui">Client</span>, <span class="ui">Adresse Chantier</span>,
        <span class="ui">Réf. Chantier</span>, <span class="ui">Téléphone / Mail</span></li>
    <li>Sous <b>Matériaux (Défaut)</b> : <span class="ui">Panneau / Décor</span>,
        <span class="ui">Chant (mm)</span>, <span class="ui">Décor Chant</span>. Ce sont les
        valeurs reprises par défaut pour toutes les pièces ; elles restent modifiables
        caisson par caisson.</li>
  </ul>
  <p>Terminez par <span class="ui">💾 Enregistrer les informations projet</span> : tant que
  vous n'avez pas cliqué, la saisie n'est pas prise en compte.</p>

  <h3>1B — Charger un projet existant (optionnel)</h3>
  <p>Deux entrées possibles :</p>
  <ul>
    <li><span class="ui">Charger un Projet (.xlsx)</span> — reprend un projet KoboMeuble déjà
    téléchargé. <b>La sauvegarde est incluse dans le fichier Excel des livrables</b> : c'est
    ce même fichier que vous rechargez ici.</li>
    <li><span class="ui">Charger un Projet Configurateur (.json)</span> — reconstruit un
    meuble représentatif à partir d'un JSON issu du configurateur web.</li>
  </ul>

  <h3>1C — Assemblage de la scène</h3>
  <p>Un meuble se construit caisson par caisson :</p>
  <ol>
    <li>Cliquez sur <span class="ui">1. Ajouter le Caisson Central</span>. C'est le point de
    départ obligatoire ; le bouton se désactive ensuite.</li>
    <li>Pour chaque caisson supplémentaire, choisissez d'abord le
    <span class="ui">Caisson de référence</span> dans la liste, puis cliquez sur la direction
    voulue autour du carré central : <span class="ui">⬆️ Ajouter en haut</span>,
    <span class="ui">⬅️ Ajouter à gauche</span> ou
    <span class="ui">➡️ Ajouter à droite</span>. Le nouveau caisson se pose au contact du
    caisson de référence.</li>
  </ol>
  <p><span class="ui">Vider la scène 🗑️</span> repart de zéro. L'action est immédiate et sans
  confirmation.</p>

  <h3>1D — Options globales</h3>
  <p>Activez <span class="ui">Ajouter des pieds</span> <b>uniquement si le meuble est posé au
  sol</b>. Choisissez ensuite la <span class="ui">Hauteur (mm)</span> parmi
  <span class="ui">20</span>, <span class="ui">80-100</span> ou
  <span class="ui">110-120</span>, puis le <span class="ui">Diamètre pieds (mm)</span>.
  Les pieds influencent la hauteur hors-tout et les plans.</p>
</section>

<!-- 3 -->
<section>
  <h2 class="sec"><span class="n">3</span>Étape 2 — Édition détaillée d'un caisson</h2>
  <p class="sec-note">Onglet <span class="ui">Etape 2 - Edition détaillée</span>. Tous les
  réglages s'appliquent au caisson sélectionné, un seul à la fois.</p>

  <h3>2A — Choisir le caisson à modifier</h3>
  <p>Sélectionnez-le dans <span class="ui">Éditer le caisson :</span>. Six onglets
  apparaissent alors :
  <span class="ui">Dimensions</span>, <span class="ui">Porte/Tiroir</span>,
  <span class="ui">Étagères</span>, <span class="ui">Montants Secondaires</span>,
  <span class="ui">Joues</span>, <span class="ui">Feuille de Débit</span>.
  <span class="ui">Supprimer le Caisson</span> retire le caisson sélectionné.</p>

  <h3>Onglet Dimensions</h3>
  <ul>
    <li><b>Matière</b> : <span class="ui">Matière Corps</span> pour ce caisson.</li>
    <li><b>Dimensions externes</b> : <span class="ui">Longueur (X)</span>,
    <span class="ui">Largeur (Y - Profondeur)</span>, <span class="ui">Hauteur (Z)</span>,
    en millimètres et hors-tout.</li>
    <li><b>Épaisseurs des panneaux</b> :
    <span class="ui">Parois latérales (Montants)</span>,
    <span class="ui">Arrière (Fond)</span>, <span class="ui">Haut/Bas (Traverses)</span>.</li>
    <li><b>Éléments de base</b> : les interrupteurs
    <span class="ui">Panneau Arrière (Fond)</span>, <span class="ui">Montant Gauche</span>,
    <span class="ui">Montant Droit</span>, <span class="ui">Traverse Bas</span>,
    <span class="ui">Traverse Haut</span> permettent de retirer une pièce de structure —
    utile pour un caisson accolé ou ouvert. Une pièce désactivée disparaît de la feuille de
    débit.</li>
  </ul>

  <h3>Onglet Porte/Tiroir</h3>
  <p><b>Porte (Façade)</b> — activez <span class="ui">Ajouter une porte</span> puis réglez :
  <span class="ui">Type de porte</span> (Simple ou Double),
  <span class="ui">Sens d'ouverture</span> (Droite ou Gauche, pour une porte simple),
  <span class="ui">Épaisseur (mm)</span>,
  <span class="ui">Modèle</span> (Standard ou Cache-pied) et
  <span class="ui">Jeu extérieur (mm)</span>. La section
  <b>Charnières</b> permet de laisser le calcul automatique ou d'imposer le nombre et la
  position de chaque charnière.</p>
  <p><b>Fileur</b> — réduit la largeur utile du caisson du côté de l'ouverture ; les façades
  sont recalculées en conséquence.</p>
  <p><b>Configuration des Tiroirs</b> — <span class="ui">➕ Ajouter un Tiroir</span> pose un
  tiroir, <span class="ui">🧱 Ajouter plusieurs tiroirs (empiler)</span> en pose une pile
  répartie automatiquement. Chaque tiroir se règle ensuite dans son propre bloc
  <span class="ui">⚙️ Tiroir n</span>, avec un bouton de suppression.</p>

  <div class="callout">
    <span class="t">Le piège n°1 : la pose reste provisoire</span>
    Quand vous ajoutez un tiroir ou une étagère, l'application affiche
    <i>« Pose en cours : le tiroir est en prévisualisation »</i>. L'élément n'est
    <b>pas</b> encore posé. Ouvrez
    <span class="ui">✅ Valider la position</span> et validez, sinon l'élément ne sera ni
    dans la 3D définitive, ni dans la feuille de débit, ni dans les plans.
  </div>

  <h3>Onglet Étagères</h3>
  <p><span class="ui">Ajouter une étagère au Caisson</span> ou
  <span class="ui">🧱 Ajouter plusieurs étagères fixes (empiler)</span>. Chaque étagère est
  soit <b>mobile</b> (posée sur taquets, non usinée dans la structure) soit <b>fixe</b>
  (encastrée), et se règle dans son bloc <span class="ui">⚙️ Étagère n</span>. Là encore, la
  pose doit être validée.</p>

  <h3>Onglet Montants Secondaires</h3>
  <p>Ajoute des montants verticaux intermédiaires, qui redécoupent l'intérieur du caisson en
  zones. Ces zones servent ensuite à positionner portes, tiroirs et étagères verticales. On y
  trouve aussi l'ajout d'<span class="ui">➕ Ajouter une Étagère Verticale</span>.</p>

  <h3>Onglet Joues</h3>
  <p>Quatre habillages indépendants : <span class="ui">Joue gauche</span>,
  <span class="ui">Joue droite</span>, <span class="ui">Joue dessus</span>,
  <span class="ui">Joue dessous</span>. Cochez <span class="ui">Activer …</span> puis
  renseignez largeur, longueur et épaisseur. Les joues apparaissent dans la 3D et dans le
  débit.</p>

  <h3>Onglet Feuille de Débit</h3>
  <p>Affiche le débit calculé pour ce caisson, pièce par pièce, dans un tableau modifiable.
  C'est l'endroit où contrôler les dimensions finies avant de générer les livrables. Les
  lignes strictement identiques (même repère, mêmes dimensions, même matière, mêmes chants,
  même usinage) sont regroupées en une seule ligne avec la quantité cumulée.</p>
</section>

<!-- 4 -->
<section>
  <h2 class="sec"><span class="n">4</span>Étape 3 — Contrôle de la prévisualisation 3D</h2>
  <p class="sec-note">La dernière occasion de voir une erreur avant qu'elle parte à
  l'atelier.</p>
  <p>La vue se met à jour à chaque modification. Tournez-la et vérifiez :</p>
  <ul>
    <li>que <b>tous les éléments posés apparaissent</b> — un tiroir manquant signale presque
    toujours une pose non validée (voir l'encadré plus haut) ;</li>
    <li>que les <b>caissons s'assemblent</b> comme prévu, sans recouvrement ni jour ;</li>
    <li>que les <b>portes s'ouvrent du bon côté</b> et dégagent bien l'intérieur ;</li>
    <li>que les <b>étagères et tiroirs</b> sont aux bonnes hauteurs.</li>
  </ul>
  <div class="callout tip">
    <span class="t">Bon réflexe</span>
    Corrigez toujours dans l'étape 2 puis revenez vérifier ici. La 3D est un miroir de l'état
    du projet : si quelque chose n'y est pas, cela ne sera dans aucun livrable.
  </div>
</section>

<!-- 5 -->
<section>
  <h2 class="sec"><span class="n">5</span>Étape 4 — Générer et télécharger les livrables</h2>
  <p class="sec-note">Section <span class="ui">Export des livrables</span>, en bas de page.
  Il faut au moins un caisson.</p>

  <h3>Avant de générer</h3>
  <ul>
    <li>Renseignez le titre affiché sous <b>PLANS D'USINAGES</b> sur la page de garde.</li>
    <li>Choisissez la portée du <span class="ui">Téléchargement</span> :
    <span class="ui">Toutes les matières</span>, ou
    <span class="ui">Sélectionner des matières</span> pour ne sortir que certaines matières
    dans l'Excel et le DXF (utile pour envoyer un seul panneau à un débiteur).</li>
  </ul>

  <h3>Générer</h3>
  <p>Cliquez sur <span class="ui">⚙️ Générer les livrables</span>. <b>Rien n'est calculé
  avant ce clic</b> : c'est volontaire, pour ne pas recalculer tout le projet à chaque
  réglage.</p>

  <div class="callout">
    <span class="t">Le piège n°2 : des fichiers périmés</span>
    Dès que vous modifiez le projet, l'application affiche
    <i>« ⚠️ Le projet a été modifié depuis la dernière génération »</i> et le bouton devient
    <span class="ui">🔄 Regénérer les livrables</span>. Les fichiers proposés au
    téléchargement sont alors <b>ceux d'avant votre modification</b>. Regénérez avant de
    télécharger. Le même avertissement apparaît si vous changez le filtre matière.
  </div>

  <h3>Ce que vous téléchargez</h3>
  <table>
    <thead><tr><th style="width:38%">Bouton</th><th>Contenu et usage</th></tr></thead>
    <tbody>
      <tr><td class="file">⬇️ Télécharger les livrables (.zip)</td>
          <td>Tous les fichiers ci-dessous en une seule archive. À privilégier pour
          transmettre un dossier complet.</td></tr>
      <tr><td class="file">📥 Fiche de Débit (.xlsx)</td>
          <td>La feuille de débit mise en forme : repère, dimensions finies, matière, chants,
          usinages, quantités. <b>Contient aussi la sauvegarde du projet</b> — c'est ce
          fichier que vous rechargerez pour reprendre le travail.</td></tr>
      <tr><td class="file">📐 Plans AutoCAD (.dxf)</td>
          <td>Les plans d'usinage, pièce par pièce, exploitables en CN ou en DAO.</td></tr>
      <tr><td class="file">🏗️ SketchUp portes fermées (.dae)</td>
          <td>Maquette 3D du meuble fermé, pour la présentation client ou l'implantation.</td></tr>
      <tr><td class="file">🚪 SketchUp portes entre-ouvertes (.dae)</td>
          <td>Même maquette, portes ouvertes, pour montrer et vérifier l'intérieur.</td></tr>
      <tr><td class="file">🖼️ Vues 3D cotées (.pdf)</td>
          <td>Une page par caisson : perspective cotée + récapitulatif écrit des dimensions,
          étagères, portes et tiroirs. C'est le document d'atelier.</td></tr>
      <tr><td class="file">📐 Script cotations SketchUp (.rb)</td>
          <td>Script à coller dans la console Ruby de SketchUp
          (<i>Fenêtre &gt; Console Ruby</i>) pour générer les cotations natives sur la
          maquette.</td></tr>
    </tbody>
  </table>

  <h3>Outils complémentaires</h3>
  <ul>
    <li><b>Comptage vis / tourillons</b> — décompte de la visserie, calculé à la demande via
    son propre bouton. Il se périme lui aussi quand le projet change : relancez-le.</li>
    <li><b>Prévisualisation portes doubles cache-pieds</b> —
    <span class="ui">👁️ Générer l'aperçu (2 feuilles porte double)</span>, pour contrôler ce
    cas particulier avant export.</li>
  </ul>
</section>

<!-- 6 -->
<section>
  <h2 class="sec"><span class="n">6</span>Reprendre un projet plus tard</h2>
  <p class="sec-note">Il n'y a pas de bouton « Sauvegarder » : la sauvegarde voyage dans
  l'Excel.</p>
  <ol>
    <li>Générez les livrables et téléchargez la <span class="ui">Fiche de Débit (.xlsx)</span>
    (ou le .zip, qui la contient). Conservez ce fichier.</li>
    <li>Pour reprendre : rouvrez l'application, allez en
    <span class="ui">Etape 1 - Projet et Assemblage</span>, section <b>1B</b>, et déposez le
    fichier dans <span class="ui">Charger un Projet (.xlsx)</span>.</li>
    <li>Le projet est reconstruit à l'identique : caissons, portes, tiroirs, étagères,
    informations chantier et matériaux. Vous reprenez où vous en étiez.</li>
  </ol>
  <div class="callout tip">
    <span class="t">Conséquence pratique</span>
    Si vous fermez l'onglet sans avoir téléchargé l'Excel, le travail est perdu. Prenez
    l'habitude de générer et de télécharger avant toute interruption.
  </div>
</section>

<!-- 7 -->
<section>
  <h2 class="sec"><span class="n">7</span>Les erreurs qui coûtent cher</h2>
  <p class="sec-note">À passer en revue avant chaque envoi en atelier.</p>
  <ul class="checklist">
    <li><b>Un élément posé mais non validé.</b> Tiroirs et étagères restent en
    prévisualisation tant que <span class="ui">✅ Valider la position</span> n'a pas été
    utilisé. Ils n'apparaissent alors ni en 3D définitive, ni au débit, ni aux plans.</li>
    <li><b>Des livrables non regénérés.</b> Si l'avertissement
    <i>« Le projet a été modifié depuis la dernière génération »</i> est affiché, les fichiers
    téléchargeables sont périmés.</li>
    <li><b>Les informations projet non enregistrées.</b> Le formulaire de l'étape 1A n'est
    pris en compte qu'après clic sur
    <span class="ui">💾 Enregistrer les informations projet</span>.</li>
    <li><b>Un filtre matière oublié.</b> En mode
    <span class="ui">Sélectionner des matières</span>, l'Excel et le DXF ne contiennent que
    les matières cochées — vérifiez avant d'envoyer un dossier « complet ».</li>
    <li><b>Des pieds activés à tort.</b> Ils modifient la hauteur hors-tout : ne les activez
    que pour un meuble posé au sol.</li>
    <li><b>Un élément de structure désactivé par erreur.</b> Un
    <span class="ui">Montant Gauche</span> ou une <span class="ui">Traverse Haut</span>
    décoché disparaît réellement du débit.</li>
    <li><b>Aucune sauvegarde téléchargée.</b> Pas de fichier Excel récupéré = projet perdu à
    la fermeture de l'onglet.</li>
  </ul>
</section>

</body></html>
"""

HTML(string=HTML_DOC, base_url=HERE).write_pdf(OUT)
print("PDF ecrit :", OUT, os.path.getsize(OUT), "octets")
