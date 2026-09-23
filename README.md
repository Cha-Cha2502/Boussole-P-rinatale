# Boussole Périnatale — collecte automatique

Chaque jour, GitHub lit une série de flux d'actualité (RSS). Il garde les articles qui parlent de périnatalité et met le site à jour. Tout est gratuit et ne nécessite aucune IA.

## Ce qu'il y a dans ce dossier

| Fichier | Rôle |
|---|---|
| `site/index.html` | L'application (même interface qu'avant) |
| `site/articles.json` | Les articles, réécrit à chaque collecte |
| `scripts/collecte.py` | Le programme qui lit les flux et filtre les articles |
| `sources.json` | **Le fichier à modifier** : flux suivis, mots-clés, exclusions |
| `curated.json` | Les articles relus à la main (badge « Vérifié »), toujours affichés |
| `.github/workflows/collecte.yml` | La tâche planifiée qui lance la collecte chaque jour |

## Mise en place (15 minutes, sans rien installer)

1. **Créer un compte** sur https://github.com (gratuit).
2. **Créer un dépôt** : bouton **New** → nom `boussole-perinatale` → cochez **Public** → **Create repository**.
3. **Déposer les fichiers** : sur la page du dépôt, cliquez sur **uploading an existing file**. Glissez-déposez les dossiers `site` et `scripts`, ainsi que `sources.json`, `curated.json` et `README.md`. Cliquez sur **Commit changes**.
4. **Créer la tâche planifiée.** Le dossier `.github` est souvent masqué sur Mac et Windows, donc créez ce fichier à la main :
   - Cliquez sur **Add file → Create new file**.
   - Nom du fichier : tapez exactement `.github/workflows/collecte.yml`. Les dossiers se créent quand vous tapez `/`.
   - Copiez-collez le contenu du fichier `collecte.yml` fourni, puis cliquez sur **Commit changes**.
5. **Activer le site** : **Settings → Pages** → rubrique *Build and deployment*, *Source* : choisissez **GitHub Actions**.
6. **Lancer une première collecte** : onglet **Actions** → **Collecte des articles** → **Run workflow**. Attendez environ une minute que la pastille devienne verte.
7. **Ouvrir l'application** : `https://VOTRE-PSEUDO.github.io/boussole-perinatale/`.

La collecte se fait ensuite toute seule, chaque matin.

## Installer sur un téléphone

L'adresse ci-dessus s'installe comme une application : une icône sur l'écran d'accueil, un affichage en plein écran et une lecture possible hors connexion.

**iPhone** (utilisez Safari)
1. Ouvrez l'adresse du site dans Safari.
2. Touchez le bouton **Partager** (carré avec une flèche vers le haut).
3. Choisissez **Sur l'écran d'accueil**, puis **Ajouter**.

**Android** (utilisez Chrome)
1. Ouvrez l'adresse du site dans Chrome.
2. Touchez le menu **⋮** en haut à droite.
3. Choisissez **Installer l'application**, ou **Ajouter à l'écran d'accueil**, puis confirmez.

Les favoris et l'historique sont propres à chaque téléphone : ils ne se synchronisent pas avec l'ordinateur.

## Réglages courants

- **Changer la fréquence** : dans `.github/workflows/collecte.yml`, modifiez la ligne `cron`.
  - `"0 5 * * *"` = tous les jours
  - `"0 5 * * 1"` = tous les lundis
  - `"0 5,17 * * *"` = deux fois par jour
- **Ajouter un mot-clé ou exclure un sujet** : modifiez `mots_cles` ou `exclusions` dans `sources.json` (crayon ✏️ sur GitHub, puis *Commit*).
- **Ajouter un média ou un site** : ajoutez une ligne dans `flux` avec l'adresse de son flux RSS. Mettez `"filtre": true` si le site ne parle pas que de périnatalité.
- **Ajouter une recherche** : ajoutez une ligne dans `google_news`. Les opérateurs `OR` et les guillemets fonctionnent.
- **Épingler un article vérifié** : ajoutez-le dans `curated.json`, en reprenant le format des autres.

## Ce qu'il faut savoir

- **Pas de résumé rédigé.** Le texte affiché est le chapô publié par la source. Pour les articles venus de Google Actualités, il n'y en a pas : l'appli invite à ouvrir l'article.
- **Le tri est automatique, donc imparfait.** Le filtrage se fait par mots-clés. Vous verrez passer un peu de presse people ou de faits divers : ajoutez les mots gênants dans `exclusions`. Les catégories dépendent de la source ou de la recherche qui a trouvé l'article, donc un témoignage peut se retrouver en « Politique ».
- **Pas d'articles vérifiés par une personne**, sauf ceux de `curated.json`. Il faut toujours se référer à la source.
- **« Prioritaire »** = article publié par un organisme officiel (HAS, Santé publique France, ministères, Sénat, CAF…) dans les 10 derniers jours.
- **Les « Points de discussion » ont été retirés** : ils nécessitaient l'IA.
- **Le site est public** (dépôt gratuit). Les favoris et l'historique restent dans votre navigateur.
- **Google Actualités** : les flux de recherche sont un service gratuit sans garantie. Si Google les modifie, la collecte continue avec les autres flux, et l'appli signale les sources en erreur dans ses Réglages.
- **Tâche en pause** : GitHub peut suspendre une tâche planifiée si le dépôt reste inactif longtemps. Il vous prévient alors par e-mail, et un clic dans l'onglet **Actions** la relance.
