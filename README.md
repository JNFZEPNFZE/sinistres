# Suivi des sinistres — copropriété

Application web locale pour suivre les sinistres des copropriétés gérées :
déclaration, expertise, travaux, indemnisation et relances.

**Aucune installation de bibliothèque n'est nécessaire** : seul Python 3.9+ est requis
(les données sont stockées dans un fichier SQLite `sinistres.db`).

## Démarrer

```bash
python3 app.py          # puis ouvrir http://localhost:8000
python3 app.py 8080     # sur un autre port
```

Sous Windows : installer Python depuis python.org, puis double-cliquer sur `app.py`
(ou `py app.py` dans un terminal) et ouvrir http://localhost:8000.

Au premier lancement, l'application demande de créer le **compte administrateur**.
L'administrateur ajoute ensuite les comptes de ses collègues depuis le menu « Administration ».

## Fonctionnalités

- **Tableau de bord** : dossiers ouverts, échéances dépassées, prochaines échéances,
  dossiers sans action depuis 30 jours, montants estimés / indemnisés, répartition par statut.
- **Dossiers sinistres** avec référence automatique (`SIN-2026-0001`) :
  copropriété, type (dégât des eaux, incendie, bris de glace, vol…), parties communes/privatives,
  localisation, déclarant, tiers responsable, assureur, n° de sinistre assureur,
  convention (IRSI / CIDE-COP), expert, entreprise, montants (estimation, devis, franchise,
  indemnité), prochaine action et échéance.
- **Statuts** : À déclarer → Déclaré → En attente d'expertise → Expertise réalisée →
  Devis / Travaux → Indemnisation en cours → Clôturé (ou Refusé / Sans suite).
- **Documents** joints à chaque dossier (photos, constat amiable, rapport d'expertise, devis,
  factures, courriers…) : glisser-déposer, classement par catégorie, aperçu des photos,
  25 Mo maximum par fichier (réglable avec `SINISTRES_MAX_UPLOAD_MO`).
- **Journal de suivi** par dossier (appels, courriels, relances, expertise…), avec
  traçage automatique des changements de statut.
- **Alertes de délai de déclaration** (5 jours ouvrés en général, 2 jours ouvrés pour le vol)
  et **suggestion de tranche IRSI** pour les dégâts des eaux selon le montant estimé.
- **Recherche et filtres** (copropriété, statut, type, texte libre), tri par colonne.
- **Export Excel / CSV** (séparateur `;`, accents compatibles Excel).
- **Fiche imprimable** (bouton « Imprimer » → PDF via le navigateur).
- Gestion des **copropriétés** (assureur MRI, n° de police, courtier) ; l'assureur est
  pré-rempli à la création d'un sinistre.

- **Comptes utilisateurs** : connexion par identifiant / mot de passe, rôles
  *Administrateur* (gestion des comptes, sauvegarde, suppressions) et *Gestionnaire* ;
  l'auteur de chaque action est indiqué dans le journal.

## Utilisation à plusieurs

### Sur le réseau du cabinet

Sur le poste qui sert de « serveur » (allumé pendant les heures de bureau) :

```bash
SINISTRES_HOST=0.0.0.0 python3 app.py
```

Les collègues ouvrent `http://<adresse-IP-du-poste>:8000` (ex. `http://192.168.1.20:8000`).
Sous Windows, autorisez Python dans le pare-feu lorsque la question est posée.

> Sans HTTPS, les mots de passe circulent en clair sur le réseau local : acceptable sur un
> réseau de bureau de confiance, à proscrire sur un Wi-Fi public ou ouvert.

### En ligne (accès depuis l'extérieur, télétravail)

Sur un serveur Linux (VPS hébergé en France / UE de préférence, données personnelles) :

1. Copier l'application dans `/opt/sinistres` et installer le service :
   `deploiement/sinistres.service` (instructions en tête du fichier).
2. Installer [Caddy](https://caddyserver.com), qui fournit automatiquement le HTTPS :
   adapter le nom de domaine dans `deploiement/Caddyfile`.
3. Variables déjà positionnées dans le service : `SINISTRES_COOKIE_SECURE=1`
   (cookie de session uniquement en HTTPS) et `SINISTRES_TRUST_PROXY=1`.

Sécurité intégrée : mots de passe hachés (scrypt), sessions de 12 h prolongées à chaque
utilisation (`SINISTRES_SESSION_HEURES`), blocage après 8 échecs de connexion en 15 min,
protection CSRF, en-têtes de sécurité, fichiers joints jamais exécutés par le navigateur.

## Sauvegarde

Les données sont dans `sinistres.db` et les pièces jointes dans le dossier `documents/`.
Pour sauvegarder : menu **Administration → Télécharger une sauvegarde complète** (archive ZIP
contenant la base et les documents), ou copiez ces deux éléments, application arrêtée.
Pour changer leur emplacement : `SINISTRES_DB=/chemin/sinistres.db SINISTRES_DOCS=/chemin/documents python3 app.py`.

Une base créée par une version précédente est mise à niveau automatiquement au démarrage.

## Tests

```bash
python3 -m unittest discover tests
```
