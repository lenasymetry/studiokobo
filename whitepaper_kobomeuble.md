# KoboMeuble — Livre Blanc

**Version 1.0 — Avril 2026**

---

## Résumé

KoboMeuble est une interface web de conception et de fabrication de meubles à caissons. Elle permet à un menuisier ou un agenceur de configurer une scène de caissons, de valider visuellement la structure en 3D, puis de générer en un clic l'ensemble des documents de fabrication : plans d'usinage, feuille de débit et modèle 3D.

---

## 1. Contexte et objectifs

La conception de meubles sur mesure génère deux besoins récurrents :

1. **Préparer la feuille de débit** — liste exhaustive de toutes les pièces à découper, avec dimensions, matière, chants et usirrages.
2. **Produire les plans d'usinage** — dessins cotés de chaque pièce avec la position exacte des trous de vis, tourillons et charnières.

Ces deux livrables sont aujourd'hui souvent produits manuellement ou sous Excel, sans lien direct avec un modèle 3D. KoboMeuble les automatise à partir d'un seul paramétrage.

---

## 2. Fonctionnalités principales

### 2.1 Informations projet

Avant de commencer, l'utilisateur renseigne les informations de chantier (nom du projet, client, adresse, référence, téléphone/mail, date souhaitée). Ces données alimentent automatiquement l'en-tête de la feuille de débit Excel et la page de garde du dossier de plans.

Les matériaux par défaut (panneau décor, chant, décor chant) sont définis à ce stade et s'appliquent à tous les caissons à la création.

### 2.2 Assemblage de la scène (Étape 1)

L'utilisateur commence par créer un **caisson central**, puis ajoute des caissons secondaires par un panneau directionnel interactif (gauche, droite, au-dessus). La relation parent–enfant est conservée : les caissons secondaires se positionnent automatiquement en fonction de la géométrie du caisson de référence.

Un projet existant peut être rechargé à tout moment depuis un fichier `.xlsx` exporté précédemment — le projet complet (géométrie, matières, options) est encodé dans le fichier.

**Options globales :** ajout de pieds réglables (hauteur 20 / 80–100 / 110–120 mm, diamètre configurable).

### 2.3 Édition détaillée par caisson (Étape 2)

Chaque caisson est modifiable individuellement selon cinq onglets :

| Onglet | Contenu |
|---|---|
| **Dimensions** | Longueur, hauteur, profondeur, épaisseurs des panneaux (montants, fond, traverses). Activation/désactivation de chaque élément de base (fond, montants, traverses). Matière du corps. |
| **Porte / Tiroir** | Type de porte (simple, double), sens d'ouverture, modèle, épaisseur, jeu, matière. Fileur optionnel. Pour les tiroirs : système (TANDEMBOX / LÉGRABOX), type technique (K / M / N / D), dimensions façade, matière facade et matière intérieure. |
| **Étagères** | Ajout d'étagères fixes ou mobiles, paramétrage individuel (position, épaisseur, matière). Ajout rapide en pile. Étagères verticales et montants secondaires disponibles. |
| **Montants Secondaires** | Ajout de montants verticaux (simples ou doubles) avec position X, épaisseur et matière. |
| **Feuille de Débit** | Prévisualisation en tableau de toutes les pièces du caisson sélectionné. |

### 2.4 Prévisualisation 3D (Étape 3)

La scène complète est rendue en temps réel avec Plotly (visualisation 3D interactive). L'utilisateur peut tourner, zoomer, vérifier la cohérence structurelle avant export.

Les aperçus en cours de placement (étagère en cours d'ajout, montant en preview) s'affichent directement dans la scène sans nécessiter de validation préalable.

### 2.5 Génération et téléchargement des livrables

Depuis la section **Export des livrables**, l'utilisateur déclenche la génération à la demande.

**Filtre matière :** possibilité de générer les livrables pour toutes les matières ou pour une sélection précise. Utile pour anticiper la commande fournisseur matière par matière.

**Livrables disponibles :**

| Fichier | Format | Contenu |
|---|---|---|
| Feuille de débit | `.xlsx` | Toutes les pièces groupées par matière et épaisseur, avec dimensions, chants, usinage et informations chantier |
| Plans d'usinage | `.dxf` | Plans cotés de chaque pièce, page de garde, cartouche, symboles de chants — compatible AutoCAD / ZWCAD |
| Dossier de plans | `.html` | Version navigateur consultable sans logiciel |
| Modèle 3D fermé | `.dae` | Import direct dans SketchUp (Collada), portes fermées |
| Modèle 3D entre-ouvert | `.dae` | Import SketchUp, portes à 50° (disponible si porte présente) |

**Téléchargement groupé :** un bouton unique télécharge un `.zip` contenant la feuille de débit `.xlsx` et les plans `.dxf` dans un dossier nommé d'après le projet.

**Comptage vis / tourillons :** calcul à la demande du nombre exact de vis et de tourillons par scène d'usinage, présenté en tableau avec totaux.

**Détection de changement :** si la scène est modifiée après une génération, un avertissement invite à regénérer les fichiers pour rester à jour.

---

## 3. Architecture technique

| Couche | Technologie |
|---|---|
| Interface | Streamlit (Python) |
| Visualisation 3D | Plotly Graph Objects |
| Plans d'usinage | ezdxf (DXF R2010) |
| Feuille de débit | openpyxl |
| Modèle 3D | Collada / DAE (généré en Python) |
| Archive | zipfile (ZIP_DEFLATED) |
| Persistance session | `st.session_state` |
| Sauvegarde projet | Encodé dans l'Excel (feuille cachée JSON) |

L'application est conçue pour un déploiement sans base de données. La totalité de l'état du projet transite par le fichier Excel téléchargé.

---

## 4. Flux de travail typique

```
Renseigner le projet
       ↓
Créer le caisson central
       ↓
Ajouter des caissons secondaires (gauche / droite / haut)
       ↓
Éditer chaque caisson : dimensions, portes, tiroirs, étagères, matières
       ↓
Vérifier en prévisualisation 3D
       ↓
Sélectionner le filtre matière (optionnel)
       ↓
Générer les livrables
       ↓
Télécharger le ZIP (Excel + DXF)
```

---

## 5. Périmètre actuel

**Inclus**
- Caissons rectangulaires (corps, fond, traverses, montants)
- Portes battantes (simple / double, fileur)
- Tiroirs TANDEMBOX et LÉGRABOX (K / M / N / D)
- Étagères fixes et mobiles, étagères verticales
- Montants secondaires (simples et doubles)
- Plans d'usinage avec cotation automatique, chants, symboles de perçage
- Export DXF éditable (entités primitives, compatible AutoCAD)
- Modèle SketchUp (Collada)

**Non inclus à ce stade**
- Caissons non rectangulaires (formes sur mesure)
- Portes coulissantes
- Charnières à débrayage (Clip Top, Blumotion) — le comptage est prévu mais le choix de modèle n'est pas exposé
- Connectivité ERP / logiciel de découpe numérique (CNC)

---

## 6. Sécurité et confidentialité

- Aucune donnée n'est transmise à un serveur externe. Le traitement est entièrement local à la session Streamlit.
- Les fichiers générés ne contiennent pas de données biométriques ou personnelles au-delà des informations chantier saisies par l'utilisateur.
- L'accès à l'interface peut être protégé par authentification au niveau de la plateforme de déploiement.

---

*KoboMeuble — Document interne / Confidentiel*
