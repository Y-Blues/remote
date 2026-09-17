# Screen-description library, multi-backend — brainstorming checkpoint (sous-projet 2)

**Status: IN PROGRESS.** Snapshot d'un brainstorming en cours, pas une spec revue/approuvée. Vit dans
`remote/docs/` faute d'un meilleur endroit tant qu'aucun nouveau dépôt n'existe encore (comme le checkpoint
transparent-rpc avant lui) — à déplacer une fois le(s) dépôt(s) réels créés. Contexte : sous-projet 2 du
brainstorming plus large, voir
[`2026-09-16-transparent-rpc-checkpoint.md`](2026-09-16-transparent-rpc-checkpoint.md) (sous-projet 1,
`remote`) et [`2026-09-16-transparent-rpc-design.md`](2026-09-16-transparent-rpc-design.md) (design figé
du sous-projet 1). Pas de dépendance bloquante entre les deux : ce document peut avancer en parallèle,
seule l'intégration avec le catalogue `ServiceDescriptor` (section Décidé, point 4) attend que ce dernier
existe.

## Objectif

Une bibliothèque Python où un écran (formulaire, liste, action) se décrit **une seule fois**, de façon
déclarative et sans dépendance à aucun toolkit graphique, puis se **rend** via un adapter interchangeable :
web, Qt (desktop), shell/terminal. Le développeur applicatif écrit une description d'écran ; il ne choisit
pas comment elle est rendue — c'est le déploiement qui choisit l'adapter, exactement comme `remote` rend
local-vs-distant une propriété de déploiement plutôt que de typage statique.

## Décidé

1. **Forme ports-et-adapters.** Un cœur (`core`, à ne pas confondre avec le dépôt `ycappuccino-core`
   existant — nom à trancher, voir Ouvert) possède le modèle d'écran et ne connaît aucun toolkit de rendu.
   Chaque cible a son propre adapter, dépôt séparé, dépendance éditable vers le cœur — même style que le
   reste du framework (un `pyproject.toml`/dépôt par préoccupation).

2. **Trois adapters, mêmes conventions de décision que le reste du framework** (une techno par cible,
   choisie explicitement plutôt que laissée ouverte) :
   - **Qt (desktop) : PySide6.** Licence LGPL, pas de contrainte de licence sur le code applicatif,
     contrairement à PyQt6 (GPL/commercial).
   - **Shell/terminal : `textual`.** Un vrai TUI (widgets, layout, navigation clavier), pas une simple
     séquence de prompts — cohérent avec l'idée d'un écran (formulaire, liste, actions), pas un wizard
     linéaire.
   - **Web : Python live dans le navigateur via `client` (Pyodide), pas de génération HTML côté serveur.**
     Décision utilisateur explicite (2026-09-16), après avoir posé l'alternative plus prudente
     (génération HTML/CSS côté serveur, zéro risque nouveau) — le choix retenu est le plus ambitieux et
     **hérite intégralement des risques non vérifiés déjà documentés dans `client/README.md`** : vrais
     threads OS sous un build Pyodide standard (`core.async_runner.AsyncRunner`), `pyyaml` dans la liste
     curatée Pyodide, tension COOP/COEP avec les CDN tiers — rien de tout cela n'a été exécuté dans un
     vrai navigateur à ce jour. Ce sous-projet ne referme pas ces risques ; il en dépend. Voir « Ouvert »
     pour la conséquence sur le séquencement.
   - `client` aujourd'hui (`components.py`/`transport.py`/`remote_proxy.py`) ne fournit que des proxys
     d'accès aux données (`RemoteCrud`, `RemoteServiceEndpoint`, ...) — **aucune primitive de manipulation
     du DOM n'existe encore nulle part**. L'adapter web devra écrire cette brique (bindings `js`/
     `pyodide.ffi`) lui-même ; il consommera `client` pour les données, pas pour l'affichage.

3. **Le cœur ne dépend d'aucun adapter, jamais.** Un écran décrit en Python pur (dataclasses ou
   équivalent), sans import Qt/Pyodide/textual — testable en CPython pur, sans aucun des trois toolkits
   installés, même discipline que le reste du framework (`ycappuccino.api` ne dépend d'aucune
   implémentation concrète).

4. **Lien futur avec le catalogue typé de `remote` (sous-projet 1), pas immédiat.** Une fois
   `ServiceDescriptor`/`@rpc_method` en place (design figé, section 6-8 de
   `2026-09-16-transparent-rpc-design.md`), les champs/actions d'un écran pourront potentiellement se
   dériver des mêmes signatures typées (`inspect.signature`/`typing.get_type_hints`) plutôt que d'être
   redéclarés à la main. Non bloquant : le modèle d'écran de ce document doit être exploitable seul, cette
   dérivation reste une évolution, pas un prérequis.

## Décidé, partie 2 (2026-09-16, avancement) : modèle de cœur figé, `ui` + `ui_shell` implémentés

Séquencement confirmé par l'utilisateur (shell d'abord, cohérent avec la recommandation ci-dessus) :
`ui` (cœur) et `ui_shell` (adapter `textual`) sont construits, testés (23 + 8 tests, `unittest`, réels —
`ui_shell`'s tests pilotent un vrai `textual.app.App` via `app.run_test()`, aucune simulation du rendu),
commités localement (dépôts `git init` locaux, pas encore de remote GitHub, jamais poussés — même
discipline que `remote`).

**Revirement important en cours de route (demandé explicitement par l'utilisateur), à ne pas perdre :**
un écran n'est **pas** censé être construit à la main en Python pour chaque cas d'usage — il est
**normalement chargé depuis un template YAML/JSON**, et la bibliothèque Python se limite au chargement
générique + au câblage générique des événements, jamais à la logique applicative d'un écran particulier.
Conséquence directe sur le modèle : `Action` ne porte plus de callable Python (`handler: Callable`,
version initiale, abandonnée) mais un `Endpoint` déclaratif (`service`, `method`, `path`, `params`) —
même forme d'adressage que `IExposedService.call(...)` (`ycappuccino.api.endpoints_service`, déjà utilisée
par `remote`), pas une seconde convention inventée. Un `Transport` (`Protocol`, une seule méthode
`call(service, method, path, params, body)`) est injecté par le déploiement — HTTP direct, `client`'s
`HttpTransport` dans un navigateur, un `IServiceEndpoint` local — et `perform_action(action, values,
transport)` est le dispatch générique que tout adapter réutilise, jamais réécrit par renderer.

**Inspiration [SData 2.0](https://sage.github.io/SData-2.0/)** (demandée explicitement) : uniquement son
idée `$template`/`$schema` (une ressource peut décrire son propre écran par défaut), pas son format de
payload Atom/XML ni son adressage `$filter`/`$orderby`/... (non repris, prématuré tant qu'aucun écran
« liste » n'existe). `ycappuccino.ui.loader.fetch_screen(transport, service)` appelle
`service/$template` via le `Transport` générique et parse la réponse comme un template local — relie
naturellement à `ServiceDescriptor` (sous-projet 1) le jour où un service peut générer son propre
`$template` à partir de son catalogue typé, sans que ce lien soit câblé aujourd'hui.

**Fichiers réels** (remplace la section « Modèle exact du cœur » ci-dessous, qui décrivait des questions
non tranchées — maintenant tranchées) :
- `ui/model.py` : `Field` (`type` ∈ `text|number|boolean|choice|date`, `required`, `default`, `choices`,
  `validate` optionnel — un callable Python, donc un échappatoire réservé aux écrans construits à la main,
  absent d'un écran chargé depuis un template), `Endpoint`, `Action`, `Screen` — `dataclasses` gelées,
  zéro dépendance à `ycappuccino.api`/`core` ni à aucun toolkit.
- `ui/validation.py` : `validate_screen(screen, values) -> {field: message}`, partagée par tous les
  adapters.
- `ui/transport.py` : `Transport` (Protocol) + `perform_action`.
- `ui/loader.py` : `load_screen(dict)`, `load_screen_yaml`/`load_screen_json`, `fetch_screen` (async,
  `$template`).
- `ui_shell/app.py` : `ScreenApp(screen, transport)` (`textual.app.App`), `run_screen(screen, transport)`.
  Un widget par type de champ (`Input`/`Checkbox`/`Select`), un `Button` par action, erreurs de validation
  affichées inline avant tout appel à `perform_action`.

**Pas encore fait avant le 2026-09-17** : layout au-delà d'une liste verticale simple, navigation entre
plusieurs écrans, lien réel avec `ServiceDescriptor`/`@rpc_method` (sous-projet 1, toujours pas
implémenté à ce stade), adapters Qt et web.

## Décidé, partie 3 (2026-09-17, avancement) : `ui_web` (adapter navigateur/Pyodide) implémenté

Deuxième adapter livré, TDD (`superpowers:test-driven-development`), 11 tests réels, `uv run python -m
unittest` vert. Dépôt réel (`Y-Blues/ui_web`, cloné/poussé, plus de "git init local" comme `ui`/`ui_shell`
avant leur premier push).

- **`ycappuccino.ui_web.dom.DomBinding`** (`Protocol`) : la seule primitive que ce dépôt invente —
  `create_element`/`append_child`/`set_text`/`set_attribute`/`get_value`/`set_value`/`on_click`.
  `client` ne fournissait jusqu'ici que des proxys de données (`RemoteCrud`, ...), **aucune primitive DOM**
  — confirmé en explorant `client` avant d'écrire ce dépôt, pas supposé.
- **`ycappuccino.ui_web.app.render_screen(screen, transport, dom, mount) -> ScreenView`** : même forme que
  `ui_shell.app.ScreenApp` (un input par `Field`, un bouton par `Action`, validation inline avant
  `perform_action`, coercion par `Field.type` — `number`→`int`/`float`, `boolean`→`bool`,
  `list`→liste virgule). Champ `choice` : le tag `<select>` est créé mais **sans ses `<option>`** — pas
  encore fait, pas de test rouge écrit pour ça (voir Ouvert).
- **`ycappuccino.ui_web.pyodide_dom.PyodideDom`** : la vraie implémentation, `js.document`/
  `pyodide.ffi.create_proxy` — importés à l'intérieur des fonctions, jamais au niveau module, même
  discipline que `client/pyodide_transport.py`. **Seul le chemin `ImportError`→`RuntimeError` de
  `__init__` est prouvé par un vrai test** (pas de `js` en CPython nu) ; tout le reste (appels DOM réels,
  le pont clic synchrone JS → coroutine Python via `asyncio.ensure_future`) est écrit depuis la surface
  d'API connue, **non exécuté contre un vrai navigateur** — même honnêteté que `client/README.md`, pas
  édulcorée dans `ui_web/README.md`.
- **`FakeDom`** (`src/unittest/python/fake_dom.py`) : arbre en mémoire réel (pas un mock) — les tests
  vérifient un vrai état d'arbre et peuvent réellement déclencher+attendre un callback de clic enregistré,
  prouvant le câblage de `render_screen`, pas juste qu'une méthode a été appelée.
- `ui/README.md` mis à jour : la mention "à venir : ycappuccino-ui-web" pointe maintenant vers le dépôt
  réel.

## Décidé, partie 4 (2026-09-17, avancement) : options du `<select>` livrées

`render_screen` ajoute maintenant un `<option>` (texte et `value` = le choix) par `Field.choices`
quand un `<select>` est créé — TDD, 2 tests réels (`FakeDom` prouve l'arbre d'options, un test de
soumission prouve que la valeur sélectionnée traverse `perform_action` inchangée), 13/13 tests verts.
Fermé dans « Ouvert » ci-dessous.

## Ouvert — reste à trancher

- **Adapter Qt** (`ycappuccino-ui-qt`, PySide6, décidé) : même modèle, pas commencé.
- **Bootstrap navigateur de `ui_web`** : pas de `static/index.html` propre à ce dépôt — reste à composer
  avec la séquence de `client/static/main.py` (charger Pyodide/iPOPO/api/core/client, puis `ui`/`ui_web`,
  puis appeler `render_screen` et attacher `mount` au vrai `document.body`) — non fait, non vérifié.
- **Layout au-delà d'une liste de champs** : pas encore un vrai besoin, à ne pas concevoir par anticipation.
- **Vérification navigateur réelle de `ui_web`** : même limite que `client` — rien de ce qui touche un
  vrai navigateur ne peut être prouvé dans l'environnement qui produit le code ; `PyodideDom` et son pont
  d'événements restent à valider manuellement avant toute mise en production.

## Prochaines étapes

1. Bootstrap navigateur réel de `ui_web` (composé avec `client/static/main.py`), puis vérification
   manuelle en navigateur (seule façon de lever les incertitudes listées dans `ui_web/README.md`).
2. Adapter Qt (PySide6) — pas encore commencé, pas urgent tant que l'adapter web n'est pas vérifié.
3. Une fois un adapter vérifié en conditions réelles (ou l'adapter Qt livré), produire le design figé
   (`docs/.../specs/YYYY-MM-DD-ui-screen-library-design.md`) — ce checkpoint reste un brainstorming avancé,
   pas une spec figée.
