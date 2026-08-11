# White Paper - Interface actuelle KoboMeuble

Date: 13 avril 2026  
Version: Interface actuelle (Streamlit)

## 1. Résumé exécutif
L'interface KoboMeuble est une application Streamlit orientée production pour la conception de caissons, la visualisation technique et la génération de livrables atelier.

Sa valeur principale est de réunir dans un seul flux:
- la configuration projet,
- l'assemblage des caissons,
- l'édition détaillée des composants,
- la visualisation 3D,
- l'export multi-format (XLSX, DXF, HTML, DAE).

Le résultat est une interface simple côté utilisateur final, mais suffisamment structurée pour supporter des cas réels d'usinage.

## 2. Objectif produit
Permettre à un opérateur ou à un bureau d'étude de passer rapidement d'une intention de meuble à des documents exploitables:
- feuille de débit,
- plans d'usinage,
- export DAO,
- livrables groupés.

## 3. Public cible
- Agencement intérieur
- Atelier de fabrication
- Concepteur technique
- Utilisateur non développeur ayant besoin d'un outil guidé

## 4. Parcours utilisateur actuel
Le parcours est organisé en étapes visibles dans l'interface.

### Étape 0 - Accès sécurisé
- Écran d'authentification par mot de passe.
- Contrôle d'accès avant affichage de l'application principale.

### Étape 1 - Projet et assemblage
- Saisie des informations projet (client, chantier, date, matériaux par défaut).
- Chargement d'un projet existant depuis un fichier XLSX.
- Création d'un caisson central puis ajout des caissons secondaires par directions (haut, gauche, droite).
- Options globales de scène (exemple: pieds du meuble).

### Étape 2 - Édition détaillée
Par caisson sélectionné:
- Dimensions et épaisseurs.
- Éléments de base (montants, traverses, fond).
- Portes et tiroirs.
- Étagères horizontales.
- Montants secondaires.
- Étagères verticales.
- Feuille de débit associée.

### Visualisation et validation
- Rendu 3D interactif de la scène.
- Vues d'usinage générées à partir de la logique métier.
- Contrôle visuel avant export.

### Génération des livrables
- Génération à la demande (pas automatique, donc maîtrisée).
- Avertissements si la scène ou le filtre matière a changé.
- Téléchargements unitaires et archive ZIP globale.

## 5. Capacités clés de l'interface
1. Interface guidée par étapes
2. Gestion multi-caissons dans une scène
3. Paramétrage fin des composants techniques
4. Filtrage des exports par matière
5. Comptage vis/tourillons à la demande
6. Export multi-format prêt atelier

## 6. Architecture fonctionnelle
L'interface repose sur une séparation claire:
- app.py: orchestration UI, états, parcours utilisateur.
- state_manager.py: gestion de l'état de session et des actions de mise à jour.
- machining_logic.py: calculs techniques (zones, perçages, collisions, positions).
- drawing_interface.py: génération de vues techniques Plotly.
- export_manager.py: génération des livrables (HTML/XLSX et coordination export).
- dxf_export/: pipeline de rendu DXF éditable.

Cette structure facilite la maintenance: UI, logique métier et exports sont découplés.

## 7. Expérience utilisateur - points forts
- Workflow lisible et progressif.
- Boutons d'action explicites.
- Feedback utilisateur clair (warnings, spinner, métriques).
- Export orienté production avec formats complémentaires.
- Bonne continuité entre modélisation, contrôle et sortie documentaire.

## 8. Limites observables (état actuel)
- Un grand nombre d'options avancées peut augmenter la charge cognitive pour un nouveau profil.
- L'application est fortement centrée sur un écran de travail unique, ce qui limite les rôles multi-utilisateurs.
- Peu d'indicateurs de validation métier "bloquants" avant export (principalement informatifs).

## 9. Recommandations simples et efficaces
1. Ajouter un mode "Essentiel" et un mode "Expert" pour alléger l'édition.
2. Ajouter une check-list de validation pré-export (dimensions critiques, collisions, données projet manquantes).
3. Proposer des modèles projet prédéfinis (cuisine, dressing, meuble bas) pour accélérer l'onboarding.
4. Ajouter un journal des modifications de la scène pour la traçabilité.

## 10. KPI de pilotage proposés
- Temps moyen entre création projet et premier export.
- Taux de régénération des livrables après modification.
- Taux d'erreur export DXF/HTML/SketchUp.
- Nombre moyen de caissons par projet.
- Taux d'utilisation du filtre matière.

## 11. Conclusion
L'interface actuelle KoboMeuble est déjà robuste, cohérente et orientée résultat atelier.

Elle remplit efficacement son objectif principal: transformer une configuration meuble en livrables techniques exploitables, avec un parcours guidé et des exports concrets.

Avec quelques améliorations UX ciblées (modes d'usage, validation pré-export, modèles), elle peut gagner en rapidité d'adoption sans perdre sa puissance technique.